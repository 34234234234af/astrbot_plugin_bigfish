"""大鱼吃小鱼 —— AstrBot 插件入口。

设计要点（省服务器）：
- 真实对局的模拟跑在"房主"的浏览器里，服务器只做 WebSocket 中继；
- bot 以普通玩家身份进房，每秒收十几次快照、发十几次方向，CPU 占用可忽略；
- bot 单独"自娱自乐"用的是本地无头模拟，快进算完，几毫秒一局。
"""

from __future__ import annotations

import asyncio
import random
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

from bigfish_core.engine import Sim  # noqa: E402
from bigfish_core.report import format_report, format_room_result  # noqa: E402
from bigfish_core.store import Store  # noqa: E402
from bigfish_net.botclient import BotPlayer  # noqa: E402
from bigfish_net.relay import Relay, gen_code  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_bigfish"

RIVALS = ["水母帮凶", "隔壁的鲨鱼", "一只章鱼", "小丑鱼", "摸鱼课代表", "深海路灯"]

HELP_TEXT = """🐟 大鱼吃小鱼
/鱼 开房　开一个实时房间，把链接发到群里
/鱼 加入　再要一次当前房间的链接
/鱼 状态　看看房间里现在有谁
/鱼 关房　解散房间
/鱼 自己玩　让 bot 现在就自己打一局给你看
/鱼 战绩　bot 的自娱战绩
/鱼 排行　群里玩家的历史最好成绩
说明：游戏跑在"房主"的浏览器里，服务器不承担模拟开销。"""


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
            ok = await self.relay.start()
            if ok:
                self._tasks.append(asyncio.create_task(self._idle_loop()))
                self._tasks.append(asyncio.create_task(self._sweep_loop()))
                logger.info(f"[bigfish] 就绪：{self._base_url()}/?room=XXXXXX")
            else:
                logger.warning("[bigfish] 中继未启动，房间类指令不可用（自娱功能不受影响）")
        except Exception as e:
            logger.error(f"[bigfish] 启动失败: {e}")

    async def _ensure_relay(self) -> bool:
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

    @filter.command("鱼", alias={"fish", "大鱼", "钓鱼"})
    async def fish_cmd(self, event: AstrMessageEvent):
        """大鱼吃小鱼主指令"""
        text = (event.message_str or "").strip()
        parts = text.split()
        if parts and parts[0] in ("鱼", "大鱼", "钓鱼", "fish", "/鱼"):
            parts = parts[1:]
        sub = parts[0] if parts else ""

        if _is_private(event):
            self.store.remember_owner(event.unified_msg_origin)

        try:
            if sub in ("", "帮助", "help", "?"):
                yield event.plain_result(HELP_TEXT)
                return

            if sub in ("开房", "开局", "建房", "开始", "new"):
                yield event.plain_result(await self._cmd_open(event))
                return

            if sub in ("加入", "进房", "链接", "join"):
                yield event.plain_result(self._cmd_join(event))
                return

            if sub in ("状态", "谁", "status"):
                yield event.plain_result(await self._cmd_status(event))
                return

            if sub in ("关房", "解散", "结束", "close"):
                yield event.plain_result(await self._cmd_close(event))
                return

            if sub in ("自己玩", "自娱", "单机", "solo"):
                yield event.plain_result(await self._cmd_self_play(event))
                return

            if sub in ("战绩", "统计", "stat"):
                yield event.plain_result(self._cmd_stats())
                return

            if sub in ("排行", "榜", "top"):
                yield event.plain_result(self._cmd_board())
                return

            yield event.plain_result(f"没看懂「{sub}」。\n\n{HELP_TEXT}")
        except Exception as e:
            logger.error(f"[bigfish] 指令出错: {e}")
            yield event.plain_result(f"出错了：{e}")

    # ============================================================ 房间
    def _base_url(self) -> str:
        base = str(self.c("public_base_url", "") or "").strip().rstrip("/")
        if base:
            return base
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
            return (
                "中继服务没起来（端口可能被占用）。\n"
                f"请检查配置里的 relay_port（现在是 {self.relay.port}），或在服务器上放行该端口。"
            )

        umo = event.unified_msg_origin
        exist = self._find_room_of(umo)
        if exist:
            return f"这个会话已经有一个房间了：\n{self._room_link(exist)}\n要重开先发 /鱼 关房。"

        code = gen_code()
        try:
            self.relay.create_room(
                code,
                meta={"umo": umo, "creator": event.get_sender_id()},
                max_players=int(self.c("max_players", 2)),
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
        self.store.add_room(code, {"umo": umo, "ts": time.time()})

        if bool(self.c("bot_plays", True)):
            await self._spawn_bot(code)

        bot_line = (
            f"\nbot「{self.c('bot_name', '小鱼苗')}」已经在房间里等着了。"
            if code in self.bots
            else ""
        )
        return (
            f"🐟 房间开好了 · 房号 {code}\n"
            f"{self._room_link(code)}\n"
            f"规则：第一个打开链接的人当房主，游戏在这个房主的浏览器里运行，"
            f"服务器只做转发，所以不卡也不吃我 CPU。\n"
            f"房主点「开始游戏」开局，一局 {int(self.c('match_seconds', 90))} 秒。{bot_line}"
        )

    def _cmd_join(self, event: AstrMessageEvent) -> str:
        code = self._find_room_of(event.unified_msg_origin)
        if not code:
            return "这个会话还没有房间，先发 /鱼 开房。"
        return f"房间 {code}：\n{self._room_link(code)}"

    async def _cmd_status(self, event: AstrMessageEvent) -> str:
        code = self._find_room_of(event.unified_msg_origin)
        if not code:
            return "这个会话还没有房间，先发 /鱼 开房。"
        room = self.relay.get_room(code)
        if not room:
            self.rooms.pop(code, None)
            return "房间已经失效了，重新发 /鱼 开房 吧。"
        roster = room.roster()
        names = "、".join(
            f"{r['name']}({'房主' if r['role'] == 'host' else ('bot' if r['role'] == 'bot' else '玩家')})"
            for r in roster
        ) or "还没有人进来"
        state = "对局进行中" if room.started else "等待开始"
        return f"房间 {code} · {state}\n在线：{names}\n{self._room_link(code)}"

    async def _cmd_close(self, event: AstrMessageEvent) -> str:
        code = self._find_room_of(event.unified_msg_origin)
        if not code:
            return "这个会话当前没有房间。"
        await self.relay.close_room(code, "房主解散了房间")
        await self._stop_bot(code)
        self.rooms.pop(code, None)
        self.store.drop_room(code)
        return f"房间 {code} 已解散。"

    async def _spawn_bot(self, code: str) -> None:
        await self._stop_bot(code)
        name = str(self.c("bot_name", "小鱼苗") or "小鱼苗")
        skill = float(self.c("bot_skill", 0.75) or 0.75)
        bind = self.relay.bind_host
        if bind in ("0.0.0.0", "::", "", None):
            bind = "127.0.0.1"
        player = BotPlayer(
            f"http://{bind}:{self.relay.port}",
            code,
            name,
            skill,
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
        """房主浏览器结算完成 → 群里播报 + 记入排行榜。"""
        info = self.rooms.get(code) or {}
        umo = info.get("umo") or ""
        rows = result.get("players") or []
        if not rows:
            return
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

    async def _flavor(self, result: dict, me_pid: str, umo: str = "") -> str:
        if not bool(self.c("use_llm_flavor", True)):
            return ""
        me = next((r for r in result.get("players", []) if r.get("id") == me_pid), None)
        if not me:
            return ""
        rival = next((r for r in result.get("players", []) if r.get("id") != me_pid), None)
        score_line = f"我 {me['score']} 分，对手 {rival['score']} 分" if rival else f"我 {me['score']} 分"
        prompt = (
            f"你刚自己玩了一局大鱼吃小鱼。{score_line}，吃了 {me['eaten']} 条鱼，"
            f"被吃 {me['deaths']} 次，最大体型 {me['max_size']}。"
            "用一句不超过 30 字的中文，对自己的表现吐槽或得意一下，不要加引号。"
        )
        try:
            provider = None
            try:
                provider = self.context.get_using_provider(umo=umo) if umo else None
            except TypeError:
                provider = self.context.get_using_provider()
            except Exception:
                provider = None
            if provider is None:
                try:
                    provider = self.context.get_using_provider()
                except Exception:
                    provider = None
            if provider is None:
                return ""
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt="你是一条会说话的小鱼，语气轻松可爱，只输出一句话。",
            )
            text = (getattr(resp, "completion_text", "") or "").strip()
            return text.splitlines()[0][:40] if text else ""
        except Exception as e:
            logger.debug(f"[bigfish] LLM 吐槽生成失败: {e}")
            return ""

    async def _do_selfplay(self, umo: str, title: str) -> str:
        result, me_pid = self._simulate()
        stats = self.store.record_self_play(result, me_pid)
        flavor = await self._flavor(result, me_pid, umo)
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
            return "还没有人玩过，发 /鱼 开房 拉人开局。"
        lines = ["🐟 群内排行榜"]
        for i, (name, d) in enumerate(items, 1):
            lines.append(
                f"{i}. {name} — 最高 {d.get('score', 0)} 分，"
                f"{d.get('wins', 0)} 胜 / {d.get('plays', 0)} 局"
            )
        return "\n".join(lines)

    # ============================================================ 后台任务
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _on_any_message(self, event: AstrMessageEvent):
        """只记时间戳，用来判断"群里多久没人说话"。不产出任何回复，不影响 LLM。"""
        try:
            self.store.touch_activity()
        except Exception:
            pass

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
