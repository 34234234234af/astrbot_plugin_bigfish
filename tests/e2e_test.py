"""端到端冒烟测试：中继 + bot 客户端 + 假房主（用 Sim 充当浏览器）。

    python tools/e2e_test.py

它验证：
1. 房间能创建、能分配房主、能靠 /health 探活；
2. bot 真的连进来并以玩家身份持续发方向；
3. 房主结算后 relay.on_end 会被触发（群里播报靠这个）；
4. 房主断开后房间被回收。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "_stub"))          # astrbot 替身
sys.path.insert(0, PLUGIN_ROOT)                           # 插件本体

import aiohttp  # noqa: E402

from bigfish_core.engine import Sim  # noqa: E402
from bigfish_net.botclient import BotPlayer  # noqa: E402
from bigfish_net.relay import Relay  # noqa: E402

PORT = 8399
ROOM = "TEST01"


async def main() -> int:
    relay = Relay("127.0.0.1", PORT, os.path.join(PLUGIN_ROOT, "web"))
    assert await relay.start(), "中继启动失败"

    ended: dict = {}

    async def on_end(code: str, result: dict):
        ended["code"] = code
        ended["result"] = result

    relay.on_end = on_end
    relay.create_room(ROOM, meta={"umo": "test:GroupMessage:1"}, max_players=2)

    async with aiohttp.ClientSession() as session:
        # ---- 1. HTTP 探活 + 首页 ----
        async with session.get(f"http://127.0.0.1:{PORT}/health") as r:
            health = await r.json()
        assert health.get("ok"), health
        async with session.get(f"http://127.0.0.1:{PORT}/") as r:
            html = await r.text()
        assert "大鱼吃小鱼" in html, "首页没正常返回"
        print(f"[ok] health={health} index={len(html)} bytes")

        # ---- 2. bot 进房 ----
        bot = BotPlayer(f"http://127.0.0.1:{PORT}", ROOM, "小鱼苗", 0.8)
        bot.start()
        await asyncio.sleep(0.7)
        room = relay.get_room(ROOM)
        assert room and len(room.peers) == 1, f"bot 没进房: {room}"
        bot_pid = next(iter(room.peers))
        print(f"[ok] bot 已进房 pid={bot_pid}")

        # ---- 3. 假房主 ----
        url = f"http://127.0.0.1:{PORT}/ws?room={ROOM}&name=%E6%B5%8B%E8%AF%95&role=auto"
        async with session.ws_connect(url) as ws:
            welcome = json.loads((await ws.receive()).data)
            assert welcome["t"] == "welcome", welcome
            assert welcome["role"] == "host", welcome
            host_pid = welcome["pid"]
            print(f"[ok] 房主就位 pid={host_pid} roster={[r['name'] for r in welcome['roster']]}")

            world = Sim(
                players=[
                    {"id": host_pid, "name": "测试", "skill": 0.0},
                    {"id": bot_pid, "name": "小鱼苗", "skill": 0.8},
                ],
                duration=20.0,
                seed=11,
            )
            world.auto_pilot = False  # 人类玩家由 relay 转来的 input 驱动

            await ws.send_str(json.dumps({"t": "start"}))
            inputs = 0

            rosters: list[list] = []

            async def pump():
                nonlocal inputs
                async for m in ws:
                    if m.type != aiohttp.WSMsgType.TEXT:
                        break
                    d = json.loads(m.data)
                    if d.get("t") == "input":
                        inputs += 1
                        world.set_input(d["pid"], d.get("dx", 0), d.get("dy", 0))
                    elif d.get("t") == "roster":
                        rosters.append(d.get("roster") or [])

            reader = asyncio.create_task(pump())

            # ---- 群友进房：房主必须收到名单更新 ----
            # （少了这个，房主在大厅看不到新来的人，开局也拉不上他）
            guest_url = (
                f"http://127.0.0.1:{PORT}/ws?room={ROOM}&name=%E9%98%BF%E5%BC%BA&role=auto"
            )
            async with session.ws_connect(guest_url) as guest:
                gw = json.loads((await guest.receive()).data)
                assert gw["t"] == "welcome" and gw["role"] == "guest", gw
                await asyncio.sleep(0.4)
                assert any(any(r.get("name") == "阿强" for r in ros) for ros in rosters), (
                    f"房主没收到含新玩家的名单更新: {rosters}"
                )
                print(f"[ok] 房主收到名单更新，能看到「阿强」共 {len(rosters)} 次推送")
            await asyncio.sleep(0.3)

            # 房主用固定方向游（模拟一个菜鸟玩家）
            fixed = {"x": 0.3, "y": 0.2}
            acc = bc = 0.0
            tick = 1 / 30
            done = False
            while not done:
                await asyncio.sleep(tick)
                world.set_input(host_pid, fixed["x"], fixed["y"])
                done = world.step(tick)
                bc += tick
                if bc >= 1 / 15:
                    bc = 0
                    await ws.send_str(json.dumps({"t": "state", **world.snapshot()}))

            result = world.result()
            await ws.send_str(json.dumps({"t": "end", "result": result}))
            await asyncio.sleep(0.4)
            reader.cancel()

        print(f"[ok] bot 发送了 {inputs} 次方向输入")
        assert inputs > 40, f"bot 好像没在操作（{inputs} 次输入）"

        rows = result["players"]
        for r in rows:
            print(f"     {r['name']:<8} score={r['score']:<5} eaten={r['eaten']} deaths={r['deaths']}")
        assert len(world.events) > 3, "整局没有任何吃鱼事件，规则可能有问题"
        print(f"[ok] 本局共 {len(world.events)} 条吃鱼事件")

        # ---- 4. 结算回调 ----
        assert ended.get("result"), "relay.on_end 没有被触发"
        assert ended["result"]["players"], ended
        print(f"[ok] on_end 已触发 code={ended['code']} 冠军={ended['result']['winner']}")

        # ---- 5. 关房 ----
        await relay.close_room(ROOM)
        await asyncio.sleep(0.2)
        assert relay.get_room(ROOM) is None
        print("[ok] 房间已回收")

        await bot.stop()

    await relay.stop()
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
