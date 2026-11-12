"""把中继单独跑在一台机器上（比如你自己的电脑）。

不需要装 AstrBot，只要有 Python + aiohttp 就行：

    pip install aiohttp
    python relay_server.py                  # 监听 0.0.0.0:8317
    python relay_server.py --port 9000      # 换端口
    python relay_server.py --tunnel         # 顺便开一条 cloudflared 临时隧道，直接给你公网地址

Windows 上直接双击同目录的 `中继-启动.bat` 或 `中继-启动并穿隧道.bat` 更方便。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from bigfish_net.relay import Relay, logger  # noqa: E402


def _lan_ip() -> str:
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


TS_URL = re.compile(r"https://[-\w.]+\.trycloudflare\.com")


async def run_with_tunnel(port: int) -> None:
    """顺手拉起 cloudflared 临时隧道，并把公网地址打印成醒目的大字。"""
    exe = os.environ.get("CLOUDFLARED") or "cloudflared"
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{port}",
            "--no-autoupdate",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        print()
        print("!" * 66)
        print("  没找到 cloudflared。先装一下：")
        print("  1) 打开 https://github.com/cloudflare/cloudflared/releases/latest")
        print("  2) 下载 cloudflared-windows-amd64.exe，改名 cloudflared.exe")
        print("  3) 放到这个目录，或者加到 PATH 里")
        print("!" * 66)
        print()
        return

    found = False
    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode("utf-8", "ignore").rstrip()
        if line:
            print("  [cloudflared] " + line)
        if not found:
            m = TS_URL.search(line)
            if m:
                found = True
                url = m.group(0)
                bar = "=" * 66
                print()
                print(bar)
                print("  公网地址（复制这一串，填到插件配置的「公网访问地址」里）：")
                print()
                print(f"      {url}")
                print()
                print("  注意：这是临时隧道，关掉这个窗口就失效；重开后地址会变。")
                print(bar)
                print()
                try:
                    (PLUGIN_DIR / "last_tunnel_url.txt").write_text(url, "utf-8")
                except Exception:
                    pass
    await proc.wait()


async def main() -> int:
    ap = argparse.ArgumentParser(description="大鱼吃小鱼 · 独立中继")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    ap.add_argument("--port", type=int, default=8317, help="监听端口，默认 8317")
    ap.add_argument("--tunnel", action="store_true", help="顺便开 cloudflared 临时隧道")
    ap.add_argument("--ttl", type=int, default=180, help="空房间保留分钟数")
    args = ap.parse_args()

    relay = Relay(args.host, args.port, PLUGIN_DIR / "web", auto_create=True)
    if not await relay.start():
        print("\n启动失败：端口可能被占用，换一个 --port 试试。\n")
        return 1

    ip = _lan_ip()
    print()
    print("=" * 66)
    print("  大鱼吃小鱼 · 中继已启动")
    print(f"  本机   : http://127.0.0.1:{args.port}/health")
    print(f"  局域网 : http://{ip}:{args.port}/?room=XXXXXX")
    print("  房间由第一个连进来的人（通常是 bot）自动创建")
    print("=" * 66)
    print()

    tasks = [asyncio.create_task(_sweeper(relay, args.ttl * 60))]
    if args.tunnel:
        tasks.append(asyncio.create_task(run_with_tunnel(args.port)))

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await relay.stop()
        print("\n中继已停止。")
    return 0


async def _sweeper(relay: Relay, ttl: float) -> None:
    while True:
        try:
            await asyncio.sleep(30)
            await relay.sweep(ttl=ttl)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"[relay] 清理房间出错: {e}")


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
