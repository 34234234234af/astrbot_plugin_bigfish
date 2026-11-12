"""大鱼吃小鱼 —— AstrBot 插件入口。

设计要点（省服务器）：
- 真实对局的模拟跑在"房主"的浏览器里，服务器只做 WebSocket 中继；
- bot 以普通玩家身份进房，每秒收十几次快照、发十几次方向，CPU 占用可忽略；
- bot 单独"自娱自乐"用的是本地无头模拟，快进算完，几毫秒一局。
"""

from __future__ import annotations

import asyncio
import random
import re
import socket
import sys
import time
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from astrbot.api import AstrBotConfig, logger  # noqa: E402
from astrbot.api.event import AstrMessageEvent, MessageChain, filter  # noqa: E402
from astrbot.api.star import Context, Star, register  # noqa: E402

try:
    import aiohttp
except Exception:  # pragma: no cover
    aiohttp = None

from bigfish_core.engine import Sim  # noqa: E402
from bigfish_core.report import format_report, format_room_result  # noqa: E402
from bigfish_core.store import Store  # noqa: E402
from bigfish_net.botclient import BotPlayer  # noqa: E402
from bigfish_net.relay import Relay, gen_code  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_bigfish"

RIVALS = ["水母帮凶", "隔壁的鲨鱼", "一只章鱼", "小丑鱼", "摸鱼课代表", "深海路灯"]

# 内置指令名，任何时候都可用（改配置也去不掉，防止把自己锁在门外）
STATIC_COMMANDS = ("鱼", "fish", "钓鱼", "大鱼")

# 子指令的内置同义词。用户在配置里写自己的词后，这些仍然兼容。
DEFAULT_SUBS: dict[str, str] = {
    "open": "开房|开局|建房|开始|new",
    "join": "加入|进房|链接|join",
    "status": "状态|谁|status",
    "close": "关房|解散|结束|close",
    "selfplay": "自己玩|自娱|单机|solo",
    "stats": "战绩|统计|stat",
    "board": "排行|榜|top",
    "help": "帮助|help|?",
}

SUB_KEYS = ("open", "join", "status", "close", "selfplay", "stats", "board", "help")

HELP_TAIL = "说明：游戏跑在「房主」的浏览器里，服务器不承担模拟开销。"


def _guess_lan_ip() -> str:
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        try:
            if s:
                s.close()
        except Exception:
            pass


def _is_private(event: AstrMessageEvent) -> bool:
    try:
        return bool(event.is_private_chat())
    except Exception:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        return "Friend" in umo or "Private" in umo or "friend" in umo


def _clean_line(text: str, max_chars: int = 60) -> str:
    """把 LLM 输出压成一行干净的群消息。"""
    line = " ".join((text or "").split())
    for ch in ("**", "*", "`", "~~"):
        line = line.replace(ch, "")
    line = line.strip().strip("\"'“”「」『』 ")
    if max_chars and len(line) > max_chars:
        line = line[:max_chars].rstrip() + "…"
    return line


@register(
    PLUGIN_NAME,
    "G",
    "实时「大鱼吃小鱼」：房主浏览器跑模拟，bot 进房陪玩，没人的时候自己玩并私聊汇报。",
    "v0.1.0",
)
class BigFishPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.conf = config or {}
        self.data_dir = self._resolve_data_dir()
        self.store = Store(self.data_dir / "state.json")

        self.relay = Relay(
            host=str(self.c("relay_host", "0.0.0.0")),
            port=int(self.c("relay_port", 8317)),
            web_dir=PLUGIN_DIR / "web",
        )
        self.relay.on_end = self._on_room_end

        self.rooms: dict[str, dict] = {}
        self.bots: dict[str, BotPlayer] = {}
        self._tasks: list[asyncio.Task] = []
        self._booted = False
        self._reported: dict[str, float] = {}
        self._try_boot()

    # ============================================================ 基础
    def c(self, key: str, default=None):
        try:
            v = self.conf.get(key, default)
        except Exception:
            return default
        return default if v is None else v

    def _has_loop(self) -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _resolve_data_dir(self) -> Path:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        except Exception:
            return PLUGIN_DIR / "data"

    def _try_boot(self) -> None:
        """AstrBot 加载插件时通常在事件循环里，这里顺手把服务拉起来。"""
        if self._booted or not self._has_loop():
            return
        self._booted = True
        self._tasks.append(asyncio.create_task(self._boot()))

    async def _boot(self) -> None:
        try:
            if self._external():
                self._tasks.append(asyncio.create_task(self._idle_loop()))
                alive = await self._remote_alive()
                if alive:
                    logger.info(f"[bigfish] 外部中继在线：{self._external_base()}")
                else:
                    logger.warning(
                        "[bigfish] 外部中继连不上，房间类指令会提示；自娱功能不受影响。"
                        f"（地址：{self._external_base() or '未配置'}）"
                    )
                return

            ok = await self.relay.start()
            if ok:
                self._tasks.append(asyncio.create_task(self._idle_loop()))
                self._tasks.append(asyncio.create_task(self._sweep_loop()))
                logger.info(f"[bigfish] 就绪：{self._base_url()}/?room=XXXXXX")
            else:
                logger.warning("[bigfish] 中继未启动，房间类指令不可用（自娱功能不受影响）")
        except Exception as e:
            logger.error(f"[bigfish] 启动失败: {e}")

    # ------------------------------------------------- 外部中继
    def _external(self) -> bool:
        return str(self.c("relay_mode", "local")).lower() in ("external", "remote", "外部")

    def _external_base(self) -> str:
        return str(self.c("public_base_url", "") or "").strip().rstrip("/")

    async def _remote_alive(self) -> bool:
        base = self._external_base()
        if not base or aiohttp is None:
            return False
        try:
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(f"{base}/health") as r:
                    if r.status != 200:
                        return False
                    data = await r.json(content_type=None)
                    return bool(data.get("ok"))
        except Exception:
            return False

    async def _remote_room_info(self, code: str) -> dict | None:
        base = self._external_base()
        if not base or aiohttp is None:
            return None
        try:
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(f"{base}/api/room", params={"code": code}) as r:
                    if r.status != 200:
                        return None
                    return await r.json(content_type=None)
        except Exception:
            return None

    async def _ensure_relay(self) -> bool:
        if self._external():
            return await self._remote_alive()
        if self.relay.running:
            return True
        self._try_boot()
        for _ in range(20):
            if self.relay.running:
                return True
            await asyncio.sleep(0.1)
        if not self._booted:
            self._try_boot()
            await asyncio.sleep(0.2)
        return self.relay.running

    async def terminate(self):
        for t in list(self._tasks):
            t.cancel()
        for code in list(self.bots):
            await self._stop_bot(code)
        try:
            await self.relay.stop()
        except Exception:
            pass
        self.store.save()
        logger.info("[bigfish] 已停止")

    # ============================================================ 指令
    @filter.on_astrbot_loaded()
    async def _on_astrbot_loaded(self):
        """AstrBot 加载完成后确保中继和后台任务起来了（__init__ 里没循环时会走到这）。"""
        self._try_boot()

    # ------------------------------------------------- 指令词（可在配置里改）
    def command_names(self) -> list[str]:
        """主指令名。配置里的词 + 内置词，去重保序。"""
        raw = self.c("command_names", [])
        if isinstance(raw, str):
            raw = re.split(r"[|,，、\s]+", raw)
        names: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                w = str(item).strip().lstrip("/")
                if w and w not in names:
                    names.append(w)
        for w in STATIC_COMMANDS:
            if w not in names:
                names.append(w)
        return names or ["鱼"]

    def _auto_names(self) -> list[str]:
        """配置里新增的指令名（内置词由 @filter.command 负责，避免重复响应）。"""
        return [n for n in self.command_names() if n not in STATIC_COMMANDS]

    def sub_words(self, key: str) -> list[str]:
        """某个子指令可用的词：配置里写的 + 内置同义词。"""
        raw_all = self.c("sub_commands", {})
        raw = ""
        if isinstance(raw_all, dict):
            raw = str(raw_all.get(key, "") or "")
        elif isinstance(raw_all, str):
            raw = ""
        words: list[str] = []
        for src in (raw, DEFAULT_SUBS.get(key, "")):
            for w in re.split(r"[|,，、/\s]+", src):
                w = w.strip()
                if w and w not in words:
                    words.append(w)
        return words

    def _match_sub(self, word: str) -> str:
        if not word:
            return "help"
        for key in SUB_KEYS:
            if word in self.sub_words(key):
                return key
        return ""

    def _usage(self, key: str) -> str:
        """给提示文案用，例如 `/鱼 开房`（跟随用户配置的词）。"""
        return f"/{self.command_names()[0]} {self.sub_words(key)[0]}"

    def help_text(self) -> str:
        cmd = self.command_names()[0]
        w = lambda k: self.sub_words(k)[0]  # noqa: E731
        lines = [
            "🐟 大鱼吃小鱼",
            f"/{cmd} {w('open')}　开一个实时房间，把链接发到群里",
            f"/{cmd} {w('join')}　再要一次当前房间的链接",
            f"/{cmd} {w('status')}　看看房间里现在有谁",
            f"/{cmd} {w('close')}　解散房间",
            f"/{cmd} {w('selfplay')}　让 bot 现在就自己打一局给你看",
            f"/{cmd} {w('stats')}　bot 的自娱战绩",
            f"/{cmd} {w('board')}　群里玩家的历史最好成绩",
            HELP_TAIL,
        ]
        others = [n for n in self.command_names() if n != cmd]
        if others:
            lines.append("也可以用这些名字叫我：" + "、".join("/" + n for n in others))
        return "\n".join(lines)

    async def _dispatch(self, event: AstrMessageEvent, parts: list[str]) -> str:
        """把「子指令 + 参数」派发出去，返回要回复的文本。"""
        sub = parts[0] if parts else ""
        key = self._match_sub(sub)

        if key == "help" or not sub:
            return self.help_text()
        if key == "open":
            return await self._cmd_open(event)
        if key == "join":
            return self._cmd_join(event)
        if key == "status":
            return await self._cmd_status(event)
        if key == "close":
            return await self._cmd_close(event)
        if key == "selfplay":
            return await self._cmd_self_play(event)
        if key == "stats":
            return self._cmd_stats()
        if key == "board":
            return self._cmd_board()
        return f"没看懂「{sub}」。\n\n{self.help_text()}"

    # ------------------------------------------------- 静态指令（内置词兜底）
    @filter.command("鱼", alias={"fish", "大鱼", "钓鱼"})
    async def fish_cmd(self, event: AstrMessageEvent):
        """大鱼吃小鱼主指令"""
        text = (event.message_str or "").strip()
        parts = text.split()
        if parts and parts[0].lstrip("/") in STATIC_COMMANDS:
            parts = parts[1:]

        if _is_private(event):
            self.store.remember_owner(event.unified_msg_origin)

        try:
            yield event.plain_result(await self._dispatch(event, parts))
        except Exception as e:
            logger.error(f"[bigfish] 指令出错: {e}")
            yield event.plain_result(f"出错了：{e}")

    # ------------------------------------------------- 自定义指令名
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _on_any_message(self, event: AstrMessageEvent):
        """记录活跃时间；同时接管配置里新增的指令名（内置词交给 @filter.command）。"""
        try:
            self.store.touch_activity()
        except Exception:
            pass

        try:
            custom = self._auto_names()
            if not custom:
                return
            if not getattr(event, "is_at_or_wake_command", False):
                return
            text = (event.message_str or "").strip()
            if not text:
                return
            parts = text.split()
            if parts[0].lstrip("/") not in custom:
                return

            if _is_private(event):
                self.store.remember_owner(event.unified_msg_origin)

            event.stop_event()
            reply = await self._dispatch(event, parts[1:])
            await event.send(MessageChain().message(reply))
        except Exception as e:
            logger.error(f"[bigfish] 自定义指令处理失败: {e}")

    # ============================================================ 房间
    def _base_url(self) -> str:
        base = str(self.c("public_base_url", "") or "").strip().rstrip("/")
        if base:
            return base
        if self._external():
            return ""  # 外部中继必须显式配置地址
        return f"http://{_guess_lan_ip()}:{self.relay.port}"

    def _room_link(self, code: str) -> str:
        return f"{self._base_url()}/?room={code}"

    def _find_room_of(self, umo: str) -> str | None:
        for code, info in self.rooms.items():
            if info.get("umo") == umo:
                return code
        return None

    async def _cmd_open(self, event: AstrMessageEvent) -> str:
        if not await self._ensure_relay():
            if self._external():
                base = self._external_base()
                if not base:
                    return (
                        "现在是「外部中继」模式，但没填地址。\n"
                        "请到插件配置里把「公网访问地址」填成你家电脑那个链接"
                        "（比如 https://xxx.trycloudflare.com），或者把中继模式改回 local。"
                    )
                return (
                    "我家电脑上的中继没开，玩不了 🐟\n"
                    f"（现在连的是 {base}，检查那个窗口还开着没有、隧道地址有没有变）"
                )
            return (
                "中继服务没起来（端口可能被占用）。\n"
                f"请检查配置里的 relay_port（现在是 {self.relay.port}），或在服务器上放行该端口。"
            )

        umo = event.unified_msg_origin
        exist = self._find_room_of(umo)
        if exist:
            # 外部模式下房间可能已经被远端回收了，先确认一下再决定
            if self._external() and not await self._remote_room_info(exist):
                self.rooms.pop(exist, None)
                self.store.drop_room(exist)
                await self._stop_bot(exist)
            else:
                return (
                    f"这个会话已经有一个房间了：\n{self._room_link(exist)}\n"
                    f"要重开先发 /{self.command_names()[0]} {self.sub_words('close')[0]}。"
                )

        code = gen_code()
        max_players = int(self.c("max_players", 2))

        if self._external():
            # 外部中继：房间由 bot 连上去的那一刻自动创建，这里只做登记
            self.rooms[code] = {
                "umo": umo,
                "creator": str(event.get_sender_id()),
                "creator_name": event.get_sender_name(),
                "ts": time.time(),
            }
            await self._spawn_bot(code)
            # 等远端把房间建出来，确认一下才回链接
            created = False
            for _ in range(15):
                if await self._remote_room_info(code):
                    created = True
                    break
                await asyncio.sleep(0.2)
            if not created:
                self.rooms.pop(code, None)
                await self._stop_bot(code)
                return (
                    "bot 连不上中继，房间没建成。\n"
                    "看一眼前面那个中继窗口有没有报错，或者隧道地址是不是变了。"
                )
        else:
            try:
                self.relay.create_room(
                    code,
                    meta={"umo": umo, "creator": event.get_sender_id()},
                    max_players=max_players,
                )
            except Exception as e:
                logger.error(f"[bigfish] 建房失败: {e}")
                return f"建房失败：{e}"

            self.rooms[code] = {
                "umo": umo,
                "creator": str(event.get_sender_id()),
                "creator_name": event.get_sender_name(),
                "ts": time.time(),
            }
            if bool(self.c("bot_plays", True)):
                await self._spawn_bot(code)

        self.store.add_room(code, {"umo": umo, "ts": time.time()})

        bot_line = (
            f"\nbot「{self.c('bot_name', '小鱼苗')}」已经在房间里等着了。"
            if code in self.bots
            else ""
        )
        where = "我家电脑" if self._external() else "服务器"
        return (
            f"🐟 房间开好了 · 房号 {code}\n"
            f"{self._room_link(code)}\n"
            f"规则：第一个打开链接的人当房主，游戏在这个房主的浏览器里运行，"
            f"{where}只做转发，所以不卡。\n"
            f"房主点「开始游戏」开局，一局 {int(self.c('match_seconds', 90))} 秒。{bot_line}"
        )

    def _cmd_join(self, event: AstrMessageEvent) -> str:
        code = self._find_room_of(event.unified_msg_origin)
        if not code:
            return f"这个会话还没有房间，先发 {self._usage('open')}。"
        return f"房间 {code}：\n{self._room_link(code)}"

    async def _cmd_status(self, event: AstrMessageEvent) -> str:
        code = self._find_room_of(event.unified_msg_origin)
        if not code:
            return f"这个会话还没有房间，先发 {self._usage('open')}。"

        if self._external():
            info = await self._remote_room_info(code)
            if not info:
                self.rooms.pop(code, None)
                return f"房间已经不在了（可能中继重启过），重新发 {self._usage('open')} 吧。"
            roster = info.get("roster") or []
            state = "对局进行中" if info.get("started") else "等待开始"
        else:
            room = self.relay.get_room(code)
            if not room:
                self.rooms.pop(code, None)
                return f"房间已经失效了，重新发 {self._usage('open')} 吧。"
            roster = room.roster()
            state = "对局进行中" if room.started else "等待开始"

        names = "、".join(
            f"{r['name']}({'房主' if r['role'] == 'host' else ('bot' if r['role'] == 'bot' else '玩家')})"
            for r in roster
        ) or "还没有人进来"
        return f"房间 {code} · {state}\n在线：{names}\n{self._room_link(code)}"

    async def _cmd_close(self, event: AstrMessageEvent) -> str:
        code = self._find_room_of(event.unified_msg_origin)
        if not code:
            return "这个会话当前没有房间。"

        if self._external():
            # 远端房间没法直接关，让 bot 退出即可；没人之后中继会在半分钟内回收
            await self._stop_bot(code)
            self.rooms.pop(code, None)
            self.store.drop_room(code)
            return f"房间 {code} 已收工（bot 退出了，房间会在半分钟内自动回收）。"

        await self.relay.close_room(code, "房主解散了房间")
        await self._stop_bot(code)
        self.rooms.pop(code, None)
        self.store.drop_room(code)
        return f"房间 {code} 已解散。"

    async def _spawn_bot(self, code: str) -> None:
        await self._stop_bot(code)
        name = str(self.c("bot_name", "小鱼苗") or "小鱼苗")
        skill = float(self.c("bot_skill", 0.75) or 0.75)
        max_players = int(self.c("max_players", 2))

        if self._external():
            base = self._external_base()
            if not base:
                logger.error("[bigfish] 外部中继模式但没配 public_base_url")
                return
        else:
            bind = self.relay.bind_host
            if bind in ("0.0.0.0", "::", "", None):
                bind = "127.0.0.1"
            base = f"http://{bind}:{self.relay.port}"

        player = BotPlayer(
            base,
            code,
            name,
            skill,
            max_players=max_players,
            on_end=lambda result: self._on_room_end(code, result),
        )
        self.bots[code] = player
        player.start()

    async def _stop_bot(self, code: str) -> None:
        bot = self.bots.pop(code, None)
        if bot:
            try:
                await bot.stop()
            except Exception:
                pass

    async def _on_room_end(self, code: str, result: dict) -> None:
        """房主浏览器结算完成 → 群里播报 + 记入排行榜。

        本地模式下 relay 会回调一次，外部模式下 bot 客户端会收到 end 消息，
        两条路都可能触发，所以这里按「房间+胜负+总分」去重。
        """
        if not result:
            return
        rows = result.get("players") or []
        if not rows:
            return

        sig = f"{code}:{result.get('winner')}:{sum(int(p.get('score', 0)) for p in rows)}"
        now = time.time()
        if now - self._reported.get(sig, 0.0) < 20:
            return
        self._reported[sig] = now
        if len(self._reported) > 200:
            cutoff = now - 600
            self._reported = {k: v for k, v in self._reported.items() if v >= cutoff}

        info = self.rooms.get(code) or {}
        umo = info.get("umo") or ""
        try:
            self.store.record_board(rows, result.get("winner") or "")
        except Exception:
            pass
        if not umo:
            return
        bot_pid = ""
        row_names = {r.get("name") for r in rows}
        if str(self.c("bot_name", "小鱼苗")) in row_names:
            bot_pid = next(
                (r.get("id") for r in rows if r.get("name") == str(self.c("bot_name", "小鱼苗"))),
                "",
            )
        text = format_room_result(result, bot_pid or "")
        try:
            comment = await self._persona_comment("match", result, umo, bot_pid)
        except Exception as e:
            logger.debug(f"[bigfish] 群内点评生成失败: {e}")
            comment = ""
        if comment:
            text = f"{text}\n\n{comment}"
        await self._send_text(umo, text)

    # ============================================================ bot 自娱
    def _report_target(self) -> str:
        explicit = str(self.c("report_umo", "") or "").strip()
        if explicit:
            return explicit
        return str(self.store.data.get("owner_umo", "") or "")

    async def _send_text(self, umo: str, text: str) -> bool:
        try:
            await self.context.send_message(umo, MessageChain().message(text))
            return True
        except Exception as e:
            logger.error(f"[bigfish] 发送失败({umo}): {e}")
            return False

    def _simulate(self) -> tuple[dict, str]:
        """跑一局无头对局，返回 (result, bot_pid)。耗时几毫秒。"""
        dur = float(self.c("match_seconds", 90) or 90)
        seed = random.randrange(1 << 30)
        bot_name = str(self.c("bot_name", "小鱼苗") or "小鱼苗")
        skill = float(self.c("bot_skill", 0.75) or 0.75)

        solo = bool(self.c("selfplay_solo", False))
        players = [{"id": "bot", "name": bot_name, "skill": skill}]
        if not solo:
            players.append(
                {
                    "id": "rival",
                    "name": random.choice(RIVALS),
                    "skill": round(random.uniform(0.45, 0.85), 2),
                }
            )

        sim = Sim(players=players, duration=dur, seed=seed)
        guard = 0
        limit = int(dur * 30) + 600
        while not sim.step():
            guard += 1
            if guard > limit:
                break
        return sim.result(), "bot"

    # ------------------------------------------------- 人格化点评
    async def _persona_prompt(self, umo: str) -> str:
        """取当前人格的 system prompt。取不到就返回空串。"""
        pm = getattr(self.context, "persona_manager", None)
        if pm is None:
            return ""
        prompt = ""
        pid = str(self.c("persona_id", "") or "").strip()
        try:
            if pid:
                persona = None
                getter = getattr(pm, "get_persona_v3_by_id", None)
                if getter is not None:
                    persona = getter(pid)
                if persona is None and hasattr(pm, "get_persona"):
                    persona = await pm.get_persona(pid)  # type: ignore[misc]
                if isinstance(persona, dict):
                    prompt = str(persona.get("prompt") or "")
                elif persona is not None:
                    prompt = str(getattr(persona, "prompt", "") or "")
            if not prompt and hasattr(pm, "get_default_persona_v3"):
                persona = await pm.get_default_persona_v3(umo=umo)  # type: ignore[misc]
                if isinstance(persona, dict):
                    prompt = str(persona.get("prompt") or "")
                elif persona is not None:
                    prompt = str(getattr(persona, "prompt", "") or "")
        except Exception as e:
            logger.debug(f"[bigfish] 读取人格失败: {e}")
        return prompt.strip()

    async def _llm_text(self, umo: str, prompt: str, system_prompt: str, max_chars: int) -> str:
        """调一次 LLM 拿一句话。新旧接口都试，全失败就返回空串。"""
        # 1) v4.5.7+ 推荐接口
        try:
            get_pid = getattr(self.context, "get_current_chat_provider_id", None)
            llm_gen = getattr(self.context, "llm_generate", None)
            if get_pid is not None and llm_gen is not None:
                provider_id = await get_pid(umo=umo)  # type: ignore[misc]
                if provider_id:
                    resp = await llm_gen(  # type: ignore[misc]
                        chat_provider_id=provider_id,
                        prompt=prompt,
                        system_prompt=system_prompt,
                    )
                    text = (getattr(resp, "completion_text", "") or "").strip()
                    if text:
                        return _clean_line(text, max_chars)
        except Exception as e:
            logger.debug(f"[bigfish] llm_generate 失败，尝试旧接口: {e}")

        # 2) 旧接口
        try:
            provider = None
            for kwargs in ({"umo": umo}, {}):
                try:
                    provider = self.context.get_using_provider(**kwargs)
                except TypeError:
                    continue
                except Exception:
                    provider = None
                if provider is not None:
                    break
            if provider is None:
                return ""
            resp = await provider.text_chat(prompt=prompt, system_prompt=system_prompt)
            text = (getattr(resp, "completion_text", "") or "").strip()
            return _clean_line(text, max_chars)
        except Exception as e:
            logger.debug(f"[bigfish] LLM 点评生成失败: {e}")
            return ""

    def _comment_enabled(self, kind: str) -> bool:
        if not bool(self.c("comment_enabled", True)):
            return False
        if kind == "selfplay":
            return bool(self.c("comment_on_selfplay", True))
        if kind == "match":
            return bool(self.c("comment_on_match", True))
        return False

    def _result_brief(self, result: dict) -> str:
        rows = sorted(result.get("players") or [], key=lambda r: r.get("score", 0), reverse=True)
        parts = []
        for i, r in enumerate(rows, 1):
            parts.append(
                f"第{i}名 {r.get('name', '?')} {r.get('score', 0)}分"
                f"（吃{r.get('eaten', 0)}条，被吃{r.get('deaths', 0)}次，最大体型{r.get('max_size', 0)}）"
            )
        return "；".join(parts)

    async def _persona_comment(self, kind: str, result: dict, umo: str, me_pid: str = "") -> str:
        """按人格设定，对刚结束的一局发表一句评价/吐槽。"""
        if not self._comment_enabled(kind):
            return ""

        max_chars = int(self.c("comment_max_chars", 60) or 60)
        bot_name = str(self.c("bot_name", "小鱼苗") or "小鱼苗")
        brief = self._result_brief(result)
        duration = int(result.get("duration", 0) or 0)
        me = next((r for r in result.get("players", []) if r.get("id") == me_pid), None)

        if kind == "selfplay":
            scene = (
                f"你（{bot_name}）刚刚一个人玩了一局「大鱼吃小鱼」，时长 {duration} 秒。"
                f"成绩：{brief}。"
            )
            if me:
                scene += (
                    f"你自己吃了 {me.get('eaten', 0)} 条鱼，被吃掉 {me.get('deaths', 0)} 次，"
                    f"最大体型 {me.get('max_size', 0)}。"
                )
            task = "以你自己的口吻，对这次自己的表现发表一句感想，可以得意、可以自嘲。"
        else:
            scene = f"群里刚打完一局「大鱼吃小鱼」，时长 {duration} 秒，结果是：{brief}。"
            if me:
                rank = 1 + sum(
                    1 for r in result.get("players", []) if r.get("score", 0) > me.get("score", 0)
                )
                scene += (
                    f"你也下场了，拿了第 {rank} 名，吃了 {me.get('eaten', 0)} 条鱼，"
                    f"被吃 {me.get('deaths', 0)} 次。"
                )
            else:
                scene += "你这次只是在旁边看着，没下场。"
            task = "以你自己的口吻，对这场比赛发表一句点评，可以夸赢家、可以揶揄输家。"

        style = str(self.c("comment_style", "") or "").strip()
        persona = await self._persona_prompt(umo)

        system_parts = [persona] if persona else []
        system_parts.append(
            "你现在在群聊里说话，正在评价一场刚结束的小游戏。要完全符合你上面的人格设定。"
            "只输出一句话，口语化，不要分点，不要 Markdown，不要加引号，不要自称『AI』。"
        )
        if style:
            system_parts.append(f"额外风格要求：{style}")
        system_parts.append(f"字数控制在 {max_chars} 字以内。")

        prompt = f"{scene}\n{task}"
        return await self._llm_text(umo, prompt, "\n".join(p for p in system_parts if p), max_chars)

    async def _do_selfplay(self, umo: str, title: str) -> str:
        result, me_pid = self._simulate()
        stats = self.store.record_self_play(result, me_pid)
        flavor = await self._persona_comment("selfplay", result, umo, me_pid)
        if flavor:
            self.store.data["stats"]["last_flavor"] = flavor
        text = format_report(
            result,
            me_pid,
            title=title,
            history=stats,
            flavor=flavor,
        )
        return text

    async def _cmd_self_play(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        text = await self._do_selfplay(umo, "🐟 我自己打了一局")
        return text

    def _cmd_stats(self) -> str:
        st = self.store.data.get("stats", {})
        today = time.strftime("%Y-%m-%d", time.localtime())
        today_n = self.store.data.get("daily", {}).get(today, 0)
        lines = [
            "🐟 bot 的自娱战绩",
            f"总局数 {st.get('plays', 0)}，{st.get('wins', 0)} 胜 {st.get('losses', 0)} 负",
            f"最高分 {st.get('best_score', 0)}，累计吃鱼 {st.get('total_eaten', 0)} 条",
            f"今天已经自己玩了 {today_n} 局",
        ]
        last = st.get("last_play") or 0
        if last:
            lines.append("上次玩：" + time.strftime("%m-%d %H:%M", time.localtime(last)))
        if st.get("last_flavor"):
            lines.append("上次吐槽：" + str(st["last_flavor"]))
        return "\n".join(lines)

    def _cmd_board(self) -> str:
        items = self.store.top_board(10)
        if not items:
            return f"还没有人玩过，发 {self._usage('open')} 拉人开局。"
        lines = ["🐟 群内排行榜"]
        for i, (name, d) in enumerate(items, 1):
            lines.append(
                f"{i}. {name} — 最高 {d.get('score', 0)} 分，"
                f"{d.get('wins', 0)} 胜 / {d.get('plays', 0)} 局"
            )
        return "\n".join(lines)

    # ============================================================ 后台任务
    async def _idle_loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                await asyncio.sleep(60)
                await self._idle_tick()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"[bigfish] 空闲任务异常: {e}")
                await asyncio.sleep(60)

    async def _idle_tick(self) -> None:
        if not bool(self.c("idle_enabled", True)):
            return
        target = self._report_target()
        if not target:
            return  # 还没人私聊过 bot，不知道该汇报给谁

        activity = float(self.store.data.get("activity") or 0)
        if not activity:
            return  # 还没见过任何消息，别急着开玩

        idle_min = float(self.c("idle_minutes", 90) or 90)
        if self.store.idle_seconds() < idle_min * 60:
            return

        cooldown = float(self.c("idle_cooldown_minutes", 120) or 120)
        last_play = float(self.store.data["stats"].get("last_play") or 0)
        if last_play and time.time() - last_play < cooldown * 60:
            return

        daily_limit = int(self.c("idle_daily_limit", 3) or 0)
        if not self.store.can_self_play(daily_limit):
            return

        text = await self._do_selfplay(target, "🐟 没人陪我玩，我自己打了一局")
        if await self._send_text(target, text):
            self.store.mark_reported()
            logger.info("[bigfish] 已私聊汇报自娱战绩")

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(300)
                await self.relay.sweep(ttl=float(self.c("room_ttl_minutes", 180) or 180) * 60)
                # 清掉已经不在中继里的房间记录
                for code in list(self.rooms):
                    if not self.relay.get_room(code):
                        self.rooms.pop(code, None)
                        await self._stop_bot(code)
                        self.store.drop_room(code)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"[bigfish] 清理任务异常: {e}")
