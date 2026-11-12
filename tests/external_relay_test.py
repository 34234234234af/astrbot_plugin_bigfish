"""外部中继模式测试：模拟「中继跑在我自己电脑上」，AstrBot 插件在另一头。

    python tests/external_relay_test.py

流程：
1. 在一个进程里起中继（等价于你电脑上双击 relay_server.py）
2. 插件配置成 relay_mode=external，指向这个中继
3. /鱼 开房 → bot 连上去自动建房 → 返回公网链接
4. 假扮群友打开链接成为房主 → 打完发结算 → 群里收到播报
5. 关掉中继 → /鱼 开房 应提示"电脑没开"
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
_STUB_DATA = os.path.join(HERE, "_stub_data")
shutil.rmtree(_STUB_DATA, ignore_errors=True)

sys.path.insert(0, os.path.join(HERE, "_stub"))
sys.path.insert(0, PLUGIN_ROOT)

import aiohttp  # noqa: E402

from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.star import Context  # noqa: E402
from bigfish_net.relay import Relay  # noqa: E402

import main as plugin_main  # noqa: E402

PORT = 8397
BASE = f"http://127.0.0.1:{PORT}"
GROUP = "test:GroupMessage:200"

CONFIG = {
    "relay_mode": "external",
    "public_base_url": BASE,
    "relay_host": "127.0.0.1",
    "relay_port": 8396,  # 外部模式下不该被用到
    "max_players": 2,
    "match_seconds": 30,
    "bot_plays": True,
    "bot_name": "小鱼苗",
    "bot_skill": 0.8,
    "comment_enabled": True,
    "comment_on_match": True,
    "comment_on_selfplay": True,
    "comment_style": "",
    "comment_max_chars": 60,
    "idle_enabled": False,
    "report_umo": "test:FriendMessage:42",
    "selfplay_solo": False,
}


async def run_cmd(plugin, text, umo=GROUP):
    ev = AstrMessageEvent(message_str=text, umo=umo)
    out = []
    async for res in plugin.fish_cmd(ev):
        out.append(res[1])
    return "\n".join(out)


def show(text, indent="     "):
    for line in text.splitlines():
        print(indent + line)


async def main() -> int:
    # ---- 1. 「我电脑上」的中继 ----
    relay = Relay("127.0.0.1", PORT, os.path.join(PLUGIN_ROOT, "web"), auto_create=True)
    assert await relay.start(), "中继起不来"
    print(f"[ok] 外部中继已启动 {BASE}")

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE}/health") as r:
            health = await r.json()
        assert health.get("ok") and health.get("auto_create"), health
        print(f"[ok] /health = {health}")

        ctx = Context()
        plugin = plugin_main.BigFishPlugin(ctx, CONFIG)
        await asyncio.sleep(0.4)

        assert plugin._external(), "没识别成外部中继模式"
        assert not plugin.relay.running, "外部模式下不该再自己起一个中继"
        assert await plugin._remote_alive(), "探活失败"
        print("[ok] 插件识别为外部中继模式，且没有重复启动本地中继")

        # ---- 2. 开房（bot 连上去自动建房）----
        txt = await run_cmd(plugin, "鱼 开房")
        assert f"{BASE}/?room=" in txt, txt
        code = txt.split("房号")[1].split()[0].strip()
        room = relay.get_room(code)
        assert room is not None, "远端房间没建出来"
        assert room.max_players == 2, f"max_players 没传过去: {room.max_players}"
        assert any(p.role == "bot" for p in room.peers.values()), "bot 没进房"
        print(f"[ok] 开房 {code}，远端房间已由 bot 自动创建（最大 {room.max_players} 人）")
        show(txt)

        # ---- 3. 查房间状态走 /api/room ----
        txt = await run_cmd(plugin, "鱼 状态")
        assert "小鱼苗" in txt and code in txt, txt
        print(f"[ok] 状态查询走远端 API：{txt.splitlines()[0]} / {txt.splitlines()[1]}")

        # ---- 4. 群友打开链接 → 房主 → 打完结算 ----
        bot_pid = next(p.pid for p in room.peers.values() if p.role == "bot")
        url = f"{BASE}/ws?room={code}&name=%E9%98%BF%E7%8F%8D&role=auto"
        before = len(ctx.sent)
        async with session.ws_connect(url) as ws:
            welcome = json.loads((await ws.receive()).data)
            assert welcome["t"] == "welcome" and welcome["role"] == "host", welcome
            print(f"[ok] 群友进房成为房主 pid={welcome['pid']}")

            await ws.send_str(json.dumps({"t": "start"}))
            await asyncio.sleep(0.2)
            result = {
                "duration": 30.0,
                "players": [
                    {"id": welcome["pid"], "name": "阿珍", "score": 300, "eaten": 9,
                     "deaths": 1, "max_size": 40.0},
                    {"id": bot_pid, "name": "小鱼苗", "score": 210, "eaten": 7,
                     "deaths": 2, "max_size": 33.0},
                ],
                "winner": welcome["pid"],
            }
            await ws.send_str(json.dumps({"t": "end", "result": result}))
            for _ in range(30):
                await asyncio.sleep(0.1)
                if len(ctx.sent) > before:
                    break

        assert len(ctx.sent) > before, "群内没有收到结算播报（bot 的 end 回调没走通）"
        umo, text = ctx.sent[-1]
        assert umo == GROUP, umo
        assert "本局结束" in text and "阿珍" in text, text
        assert ctx.llm_reply in text, "人格点评没拼进去"
        print(f"[ok] 结算经 bot 客户端回调 → 群里播报（含人格点评）")
        show(text)

        # ---- 5. 重复回调要去重 ----
        n = len(ctx.sent)
        await plugin._on_room_end(code, result)
        await plugin._on_room_end(code, result)
        assert len(ctx.sent) == n, "同一次结算被重复播报了"
        print("[ok] 结算去重生效（本地 relay 回调和 bot 回调不会双发）")

        # ---- 6. 关房 ----
        txt = await run_cmd(plugin, "鱼 关房")
        assert "已收工" in txt, txt
        print(f"[ok] 外部模式关房：{txt}")

    # ---- 7. 关掉中继后应该友好提示 ----
    await relay.stop()
    await asyncio.sleep(0.3)
    txt = await run_cmd(plugin, "鱼 开房", umo="test:GroupMessage:201")
    assert "中继没开" in txt, txt
    print("[ok] 中继不在线时的提示：")
    show(txt)

    await plugin.terminate()
    print("\n外部中继模式全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
