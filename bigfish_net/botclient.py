"""bot 玩家客户端：以一个真实 WS 客户端的身份进房间，收快照、发方向。"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Awaitable, Callable
from urllib.parse import quote

from astrbot.api import logger

from bigfish_core.ai import decide

try:
    import aiohttp
except Exception:  # pragma: no cover
    aiohttp = None


class BotPlayer:
    def __init__(
        self,
        relay_url: str,
        room: str,
        name: str,
        skill: float = 0.7,
        max_players: int = 0,
        on_end: Callable[[dict], Awaitable[None]] | None = None,
        on_close: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.base = relay_url.rstrip("/")
        self.room = room
        self.name = name
        self.skill = max(0.0, min(1.0, float(skill)))
        self.max_players = int(max_players or 0)
        self.on_end = on_end
        self.on_close = on_close
        self.pid: str = ""
        self.role: str = "bot"
        self.playing = False
        self._task: asyncio.Task | None = None
        self._stop = False
        self._rng = random.Random()
        self._last_input = 0.0
        self._session = None
        self._connected = False
        self._fatal = False

    # ------------------------------------------------------------------
    @property
    def url(self) -> str:
        max_q = f"&max={self.max_players}" if self.max_players else ""
        return (
            f"{self.base}/ws?room={quote(self.room, safe='')}"
            f"&name={quote(self.name, safe='')}&role=bot{max_q}"
        )

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._run())
        return self._task

    async def stop(self, reason: str = "") -> None:
        self._stop = True
        if self._task and not self._task.done():
            self._task.cancel()
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
        if reason and self.on_close:
            try:
                await self.on_close(reason)
            except Exception:
                pass

    # ------------------------------------------------------------------
    async def _run(self) -> None:
        """连上房间并待着；掉线就重连，直到房间真的没了（_fatal）或被主动停止。

        之前的实现连上过一次就 return，导致网络抖一下、房主刷新一下，
        bot 就从房间里永久消失（群里看到的现象就是"bot 不见了"）。
        """
        if aiohttp is None:
            logger.error("[bigfish] 缺少 aiohttp，bot 无法进房")
            return

        backoff = 1.0
        while not self._stop:
            connected_before = self._connected
            try:
                await self._once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._stop:
                    return
                logger.warning(f"[bigfish] bot 连接房间失败: {e}")

            if self._stop or self._fatal:
                return

            # 成功连上过（拿到过 welcome）→ 重置退避，说明是中途掉线
            if connected_before:
                backoff = 1.0
                self._connected = False
                logger.info(f"[bigfish] bot 与房间 {self.room} 的连接断了，准备重连")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.6, 15.0)

    async def _once(self) -> None:
        async with aiohttp.ClientSession() as session:
            self._session = session
            async with session.ws_connect(self.url, heartbeat=25) as ws:
                self._connected = True
                logger.info(f"[bigfish] bot「{self.name}」已进入房间 {self.room}")
                async for msg in ws:
                    if self._stop:
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
                        continue
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue
                    await self._handle(ws, data)

    async def _handle(self, ws, data: dict) -> None:
        t = data.get("t")

        if t == "welcome":
            self.pid = data.get("pid") or ""
            logger.info(
                f"[bigfish] bot 就位 pid={self.pid} 房间={data.get('room')} "
                f"在线={[r.get('name') for r in data.get('roster') or []]}"
            )

        elif t == "start":
            self.playing = True
            logger.info("[bigfish] 本局开始，bot 上线操作")

        elif t == "state":
            if not self.pid:
                return
            now = time.time()
            if now - self._last_input < 0.04:
                return
            self._last_input = now
            dx, dy = decide(data, self.pid, self.skill, self._rng)
            try:
                await ws.send_str(
                    json.dumps({"t": "input", "dx": round(dx, 3), "dy": round(dy, 3)})
                )
            except Exception:
                pass

        elif t == "end":
            self.playing = False
            result = data.get("result") or {}
            if self.on_end and result:
                try:
                    await self.on_end(result)
                except Exception as e:
                    logger.error(f"[bigfish] bot on_end 回调出错: {e}")

        elif t in ("room_closed", "host_left"):
            self._stop = True
            if self.on_close:
                try:
                    await self.on_close(data.get("msg") or "房间关闭")
                except Exception:
                    pass

        elif t == "error":
            logger.warning(f"[bigfish] bot 收到错误: {data.get('msg')}")
            if data.get("fatal"):
                # 房间真的没了（被关闭 / 不存在），重连也没意义
                self._fatal = True
                self._stop = True
                if self.on_close:
                    try:
                        await self.on_close(data.get("msg") or "进房失败")
                    except Exception:
                        pass
