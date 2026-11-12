"""bot 掉线重连测试。

    python tests/bot_reconnect_test.py

背景：之前 bot 的 _run() 只要连上过一次就 return，网络抖一下或房主刷新，
bot 就从房间里永久消失（群里看到的现象就是「等一会儿 bot 不见了」）。
现在要求：中途掉线自动重连；只有房间真的没了才退出。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
shutil.rmtree(os.path.join(HERE, "_stub_data"), ignore_errors=True)

sys.path.insert(0, os.path.join(HERE, "_stub"))
sys.path.insert(0, PLUGIN_ROOT)

from bigfish_net.botclient import BotPlayer  # noqa: E402
from bigfish_net.relay import Relay  # noqa: E402

PORT = 8395
BASE = f"http://127.0.0.1:{PORT}"
ROOM = "RCONN1"


async def wait_until(fn, timeout=8.0, step=0.15):
    waited = 0.0
    while waited < timeout:
        if fn():
            return True
        await asyncio.sleep(step)
        waited += step
    return False


async def main() -> int:
    relay = Relay("127.0.0.1", PORT, os.path.join(PLUGIN_ROOT, "web"), auto_create=True)
    assert await relay.start(), "中继起不来"
    relay.create_room(ROOM, max_players=4)

    bot = BotPlayer(BASE, ROOM, "小鱼苗", 0.8)
    bot.start()

    print("1) 首次进房")
    assert await wait_until(lambda: (r := relay.get_room(ROOM)) and len(r.peers) == 1), "bot 没进房"
    first_pid = next(iter(relay.get_room(ROOM).peers))
    print(f"   [ok] bot 进房 pid={first_pid}")

    print("2) 模拟掉线：服务端把这个连接掐掉")
    peer = relay.get_room(ROOM).peers[first_pid]
    await peer.ws.close()
    assert await wait_until(lambda: relay.get_room(ROOM) and len(relay.get_room(ROOM).peers) == 0), \
        "掉线后应该先从房间消失"
    print("   [ok] 已断开")

    print("3) bot 应该自己回来")
    assert await wait_until(
        lambda: relay.get_room(ROOM) and len(relay.get_room(ROOM).peers) == 1, timeout=10
    ), "bot 没有重连（旧 bug 会卡在这里）"
    second_pid = next(iter(relay.get_room(ROOM).peers))
    assert second_pid != first_pid, "重连后应该是新的连接"
    print(f"   [ok] bot 重连成功 pid={second_pid}（旧的 {first_pid}）")

    print("4) 房间被真正关闭后，不应该再重连")
    await relay.close_room(ROOM, "测试关闭")
    await asyncio.sleep(3.0)
    assert relay.get_room(ROOM) is None, "房间不应该被重建"
    print("   [ok] 房间关闭后 bot 没有乱重连")

    await bot.stop()
    await relay.stop()
    print("\nbot 重连测试通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
