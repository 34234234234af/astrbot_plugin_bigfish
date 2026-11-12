"""插件本体测试：用 astrbot 替身把 main.py 真正加载起来跑一遍指令链路。

    python tests/plugin_test.py

覆盖：可自定义的指令词、开房/状态/关房、bot 自娱、群内播报 + 人格点评、
空闲自娱 + 私聊汇报、自定义主指令名的接管逻辑。
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
    "command_names": ["鱼", "小鱼"],
    "sub_commands": {
        "open": "开黑|来一把",
        "join": "",
        "status": "",
        "close": "",
        "selfplay": "",
        "stats": "",
        "board": "",
        "help": "",
    },
    "relay_host": "127.0.0.1",
    "relay_port": 8398,
    "public_base_url": "https://fish.example.com",
    "max_players": 2,
    "match_seconds": 60,
    "bot_plays": True,
    "bot_name": "小鱼苗",
    "bot_skill": 0.8,
    "comment_enabled": True,
    "comment_on_selfplay": True,
    "comment_on_match": True,
    "persona_id": "",
    "comment_style": "毒舌一点，多用鱼的比喻。",
    "comment_max_chars": 60,
    "idle_enabled": True,
    "idle_minutes": 1,
    "idle_cooldown_minutes": 0,
    "idle_daily_limit": 5,
    "report_umo": "test:FriendMessage:42",
    "selfplay_solo": False,
}


async def run_cmd(plugin, text, umo="test:GroupMessage:100"):
    ev = AstrMessageEvent(message_str=text, umo=umo)
    out = []
    async for res in plugin.fish_cmd(ev):
        out.append(res[1])
    return "\n".join(out)


def show(text: str, indent="     "):
    for line in text.splitlines():
        print(indent + line)


async def main() -> int:
    ctx = Context()
    plugin = plugin_main.BigFishPlugin(ctx, CONFIG)

    for _ in range(40):
        if plugin.relay.running:
            break
        await asyncio.sleep(0.1)
    assert plugin.relay.running, "中继没起来"
    print(f"[ok] 中继已启动，数据目录={plugin.data_dir}")

    # ---- 1. 指令词可自定义 ----
    assert plugin.command_names()[:2] == ["鱼", "小鱼"], plugin.command_names()
    assert "fish" in plugin.command_names(), "内置词应该始终保留"
    assert plugin.sub_words("open")[:2] == ["开黑", "来一把"], plugin.sub_words("open")
    assert "开房" in plugin.sub_words("open"), "内置同义词应该保留"
    print(f"[ok] 指令词：主={plugin.command_names()} 开房={plugin.sub_words('open')}")

    txt = await run_cmd(plugin, "鱼")
    assert "开黑" in txt and "大鱼吃小鱼" in txt, txt
    print("[ok] 帮助用的是自定义词（/鱼）")
    show(txt)

    # ---- 2. 用自定义子指令开房 ----
    txt = await run_cmd(plugin, "鱼 开黑")
    assert "https://fish.example.com/?room=" in txt, txt
    code = txt.split("房号")[1].split()[0].strip()
    assert plugin.relay.get_room(code), f"中继里没有房间 {code}"
    assert code in plugin.bots, "bot 没被派进房"
    print(f"[ok] 用「开黑」开了房间 code={code}")
    await asyncio.sleep(0.5)

    # ---- 3. 内置同义词仍然可用 ----
    txt = await run_cmd(plugin, "鱼 状态")
    assert "小鱼苗" in txt and code in txt, txt
    print(f"[ok] 「状态」查询正常：{txt.splitlines()[0]}")

    # ---- 4. 自定义主指令名（走全消息钩子） ----
    ev = AstrMessageEvent(
        message_str="小鱼 状态", umo="test:GroupMessage:100", is_at_or_wake_command=True
    )
    await plugin._on_any_message(ev)
    assert ev.stopped, "自定义指令没有被接管"
    assert ev.sent and code in ev.sent[-1], ev.sent
    print(f"[ok] 自定义主指令「小鱼」被接管：{ev.sent[-1].splitlines()[0]}")

    # 没唤醒的普通消息不应该被接管
    ev2 = AstrMessageEvent(message_str="小鱼 状态", is_at_or_wake_command=False)
    await plugin._on_any_message(ev2)
    assert not ev2.stopped and not ev2.sent, "没有唤醒却触发了指令"

    # 内置名不会被钩子重复处理（避免双回复）
    ev3 = AstrMessageEvent(message_str="鱼 状态", is_at_or_wake_command=True)
    await plugin._on_any_message(ev3)
    assert not ev3.stopped, "内置指令名不该被钩子接管（会重复回复）"
    print("[ok] 唤醒判断 / 不重复接管都正确")

    # ---- 5. 自己玩（含人格点评） ----
    before_calls = len(ctx.llm_calls)
    txt = await run_cmd(plugin, "鱼 自己玩")
    assert "一共吃了" in txt, txt
    assert len(ctx.llm_calls) == before_calls + 1, "自己玩应该调用一次大模型做点评"
    call = ctx.llm_calls[-1]
    assert "小水母" in (call["system_prompt"] or ""), "没把人格设定塞进 system_prompt"
    assert "毒舌一点" in (call["system_prompt"] or ""), "没带自定义风格"
    assert ctx.llm_reply in txt, "点评没出现在战报里"
    print("[ok] 自己玩 + 人格点评：")
    show(txt)

    # ---- 6. 群里打完 → 播报 + 人格点评 ----
    result = {
        "duration": 60.0,
        "players": [
            {"id": "h9", "name": "阿珍", "score": 300, "eaten": 9, "deaths": 1, "max_size": 40.0},
            {"id": "b7", "name": "小鱼苗", "score": 210, "eaten": 7, "deaths": 2, "max_size": 33.0},
        ],
        "winner": "h9",
    }
    before = len(ctx.sent)
    before_calls = len(ctx.llm_calls)
    await plugin._on_room_end(code, result)
    assert len(ctx.sent) == before + 1, "结算没有发到群里"
    umo, text = ctx.sent[-1]
    assert "本局结束" in text and "阿珍" in text, text
    assert len(ctx.llm_calls) == before_calls + 1, "群内结算应该调用一次人格点评"
    assert ctx.llm_reply in text, "点评没拼进群消息"
    assert "第 2 名" in (ctx.llm_calls[-1]["prompt"] or ""), "点评 prompt 里没带上 bot 自己名次"
    print(f"[ok] 群内播报 + 点评 → {umo}")
    show(text)

    # ---- 7. 关掉点评就不该调模型 ----
    plugin.conf["comment_on_match"] = False
    before_calls = len(ctx.llm_calls)
    await plugin._on_room_end(code, result)
    assert len(ctx.llm_calls) == before_calls, "关掉点评后不该再调用大模型"
    assert ctx.llm_reply not in ctx.sent[-1][1], "关掉点评后不该出现点评内容"
    plugin.conf["comment_on_match"] = True
    print("[ok] 点评开关生效")

    # ---- 8. 战绩 / 排行 ----
    st = await run_cmd(plugin, "鱼 战绩")
    assert "总局数 1" in st, st
    print(f"[ok] 战绩：{st.splitlines()[1]}")
    bd = await run_cmd(plugin, "鱼 排行")
    print(f"[ok] 排行：{bd.splitlines()[1] if len(bd.splitlines()) > 1 else bd}")

    # ---- 9. 空闲自娱 + 私聊汇报 ----
    plugin.store.data["activity"] = time.time() - 3600
    plugin.store.data["stats"]["last_play"] = 0
    before = len(ctx.sent)
    await plugin._idle_tick()
    assert len(ctx.sent) > before, "空闲自娱没有私聊汇报"
    umo, text = ctx.sent[-1]
    assert umo == CONFIG["report_umo"], umo
    assert "没人陪我玩" in text, text
    print(f"[ok] 空闲自娱已私聊汇报 → {umo}")
    show(text)

    # ---- 10. 关房 ----
    txt = await run_cmd(plugin, "鱼 关房")
    assert "已解散" in txt, txt
    assert plugin.relay.get_room(code) is None
    print("[ok] 关房")

    await plugin.terminate()
    print("\n插件链路全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
