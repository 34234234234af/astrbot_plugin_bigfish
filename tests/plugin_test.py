"""插件本体测试：用 astrbot 替身把 main.py 真正加载起来跑一遍指令链路。

    python tools/plugin_test.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)

# 每次测试用干净的插件数据目录
_STUB_DATA = os.path.join(HERE, "_stub_data")
shutil.rmtree(_STUB_DATA, ignore_errors=True)

sys.path.insert(0, os.path.join(HERE, "_stub"))
sys.path.insert(0, PLUGIN_ROOT)

from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.star import Context  # noqa: E402

import main as plugin_main  # noqa: E402

CONFIG = {
    "relay_host": "127.0.0.1",
    "relay_port": 8398,
    "public_base_url": "https://fish.example.com",
    "max_players": 2,
    "match_seconds": 60,
    "bot_plays": True,
    "bot_name": "小鱼苗",
    "bot_skill": 0.8,
    "idle_enabled": True,
    "idle_minutes": 1,
    "idle_cooldown_minutes": 0,
    "idle_daily_limit": 5,
    "report_umo": "test:FriendMessage:42",
    "use_llm_flavor": True,
    "selfplay_solo": False,
}


async def run_cmd(plugin, text, umo="test:GroupMessage:100"):
    ev = AstrMessageEvent(message_str=text, umo=umo)
    out = []
    async for res in plugin.fish_cmd(ev):
        out.append(res[1])
    return "\n".join(out)


async def main() -> int:
    ctx = Context()
    plugin = plugin_main.BigFishPlugin(ctx, CONFIG)

    for _ in range(40):
        if plugin.relay.running:
            break
        await asyncio.sleep(0.1)
    assert plugin.relay.running, "中继没起来"
    print(f"[ok] 中继已启动，数据目录={plugin.data_dir}")

    # ---- 帮助 ----
    txt = await run_cmd(plugin, "鱼")
    assert "大鱼吃小鱼" in txt and "开房" in txt, txt
    print("[ok] /鱼 帮助")

    # ---- 开房 ----
    txt = await run_cmd(plugin, "鱼 开房")
    assert "https://fish.example.com/?room=" in txt, txt
    code = txt.split("房号")[1].split()[0].strip()
    assert plugin.relay.get_room(code), f"中继里没有房间 {code}"
    assert code in plugin.bots, "bot 没被派进房"
    print(f"[ok] 开房 code={code}\n     {txt.splitlines()[0]}")
    await asyncio.sleep(0.5)

    # ---- 状态 ----
    txt = await run_cmd(plugin, "鱼 状态")
    assert "小鱼苗" in txt and code in txt, txt
    print(f"[ok] 状态: {txt.splitlines()[0]} / {txt.splitlines()[1]}")

    # ---- 自己玩 ----
    txt = await run_cmd(plugin, "鱼 自己玩")
    assert "一共吃了" in txt or "吃了" in txt, txt
    print("[ok] 自己玩：")
    for line in txt.splitlines():
        print("     " + line)

    # ---- 战绩 / 排行 ----
    st = await run_cmd(plugin, "鱼 战绩")
    assert "总局数 1" in st, st
    print(f"[ok] 战绩: {st.splitlines()[1]}")
    bd = await run_cmd(plugin, "鱼 排行")
    print(f"[ok] 排行: {bd.splitlines()[1] if len(bd.splitlines()) > 1 else bd}")

    # ---- 群内结算播报 ----
    result = {
        "duration": 60.0,
        "players": [
            {"id": "h9", "name": "阿珍", "score": 300, "eaten": 9, "deaths": 1, "max_size": 40.0},
            {"id": "b7", "name": "小鱼苗", "score": 210, "eaten": 7, "deaths": 2, "max_size": 33.0},
        ],
        "winner": "h9",
    }
    await plugin._on_room_end(code, result)
    assert ctx.sent, "结算没有发到群里"
    umo, text = ctx.sent[-1]
    assert "本局结束" in text and "阿珍" in text, text
    print(f"[ok] 群内播报 → {umo}")
    for line in text.splitlines():
        print("     " + line)

    # ---- 空闲自娱 + 私聊汇报 ----
    plugin.store.data["activity"] = time.time() - 3600
    plugin.store.data["stats"]["last_play"] = 0
    before = len(ctx.sent)
    await plugin._idle_tick()
    assert len(ctx.sent) > before, "空闲自娱没有私聊汇报"
    umo, text = ctx.sent[-1]
    assert umo == CONFIG["report_umo"], umo
    assert "没人陪我玩" in text, text
    print(f"[ok] 空闲自娱已私聊汇报 → {umo}")
    for line in text.splitlines():
        print("     " + line)

    # ---- 关房 ----
    txt = await run_cmd(plugin, "鱼 关房")
    assert "已解散" in txt, txt
    assert plugin.relay.get_room(code) is None
    print("[ok] 关房")

    await plugin.terminate()
    print("\n插件链路全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
