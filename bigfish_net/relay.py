"""轻量 WebSocket 中继。

只做三件事：
1. 把 web/index.html 发给浏览器；
2. 给房间分配角色（先到的当房主，房主的浏览器负责跑游戏）；
3. 在房主和玩家之间转发消息 —— 房主 → 全体，玩家 → 房主。

游戏模拟完全在房主浏览器里，所以这里几乎没有 CPU 开销。
"""

from __future__ import annotations

import asyncio
import json
import random
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from astrbot.api import logger

try:
    from aiohttp import WSMsgType, web
except Exception:  # pragma: no cover
    web = None
    WSMsgType = None

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_MSG = 512 * 1024


def gen_code(n: int = 6) -> str:
    return "".join(random.choice(CODE_ALPHABET) for _ in range(n))


@dataclass
class Peer:
    pid: str
    name: str
    role: str          # host | guest | bot
    ws: Any
    joined: float = field(default_factory=time.time)


@dataclass
class Room:
    code: str
    meta: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    max_players: int = 2
    host: Peer | None = None
    peers: dict[str, Peer] = field(default_factory=dict)
    started: bool = False

    def roster(self) -> list[dict]:
        out = []
        if self.host:
            out.append({"pid": self.host.pid, "name": self.host.name, "role": "host"})
        for p in self.peers.values():
            out.append({"pid": p.pid, "name": p.name, "role": p.role})
        return out

    def human_count(self) -> int:
        n = 1 if self.host else 0
        n += sum(1 for p in self.peers.values() if p.role == "guest")
        return n

    def is_full(self) -> bool:
        # host 也算一个位置；bot 不占人类位置
        return self.human_count() >= self.max_players


class Relay:
    """一个进程内复用的 aiohttp 服务。"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8317,
        web_dir: Path | None = None,
    ):
        self.bind_host = host
        self.port = int(port)
        self.web_dir = Path(web_dir) if web_dir else None
        self.rooms: dict[str, Room] = {}
        self.on_end: Callable[[str, dict], Awaitable[None]] | None = None
        self.on_event: Callable[[str, str, dict], Awaitable[None]] | None = None
        self._runner = None
        self._site = None
        self._app = None
        self._html_cache: tuple[float, str] | None = None
        self._next_pid = 1

    # ------------------------------------------------------------ 生命周期
    @property
    def running(self) -> bool:
        return self._site is not None

    async def start(self) -> bool:
        if web is None:
            logger.error("[bigfish] 缺少 aiohttp，无法启动中继")
            return False
        if self.running:
            return True
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/index.html", self._handle_index)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/ws", self._handle_ws)
        self._app.router.add_get("/favicon.ico", self._handle_favicon)
        try:
            self._runner = web.AppRunner(self._app, access_log=None)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self.bind_host, self.port)
            await self._site.start()
            logger.info(f"[bigfish] 中继已启动 http://{self.bind_host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"[bigfish] 中继启动失败（端口 {self.port}）: {e}")
            self._site = None
            return False

    async def stop(self) -> None:
        for code in list(self.rooms):
            await self.close_room(code, "服务关闭")
        self.rooms.clear()
        if self._runner:
            try:
                await self._runner.cleanup()
            except Exception:
                pass
        self._runner = None
        self._site = None
        self._app = None

    # ------------------------------------------------------------ 房间管理
    def create_room(self, code: str = "", meta: dict | None = None, max_players: int = 2) -> Room:
        code = (code or gen_code()).upper()
        while code in self.rooms:
            code = gen_code()
        room = Room(code=code, meta=meta or {}, max_players=max(1, int(max_players)))
        self.rooms[code] = room
        return room

    def get_room(self, code: str) -> Room | None:
        return self.rooms.get((code or "").upper())

    async def close_room(self, code: str, reason: str = "房间已关闭") -> None:
        room = self.rooms.pop((code or "").upper(), None)
        if not room:
            return
        targets = list(room.peers.values()) + ([room.host] if room.host else [])
        for p in targets:
            try:
                await self._send(p.ws, {"t": "room_closed", "msg": reason})
                await p.ws.close()
            except Exception:
                pass

    async def broadcast(self, room: Room, payload: dict, exclude: str = "") -> None:
        for p in list(room.peers.values()):
            if p.pid == exclude:
                continue
            await self._send(p.ws, payload)

    async def _send(self, ws, payload: dict) -> None:
        try:
            if not ws.closed:
                await ws.send_str(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            pass

    def new_pid(self, role: str) -> str:
        pid = f"{role[0]}{self._next_pid}"
        self._next_pid += 1
        return pid

    # ------------------------------------------------------------ HTTP
    async def _handle_index(self, request):
        if not self.web_dir:
            return web.Response(text="web dir missing", status=500)
        path = self.web_dir / "index.html"
        try:
            mtime = path.stat().st_mtime
            if not self._html_cache or self._html_cache[0] != mtime:
                self._html_cache = (mtime, path.read_text("utf-8"))
            return web.Response(text=self._html_cache[1], content_type="text/html", charset="utf-8")
        except Exception as e:
            return web.Response(text=f"index.html 读取失败: {e}", status=500)

    async def _handle_favicon(self, request):
        return web.Response(status=204)

    async def _handle_health(self, request):
        return web.json_response(
            {"ok": True, "rooms": len(self.rooms), "uptime": time.time()}
        )

    # ------------------------------------------------------------ WS
    async def _handle_ws(self, request):
        if WSMsgType is None:
            return web.Response(status=503)
        code = (request.query.get("room") or "").upper()
        name = (request.query.get("name") or "").strip()[:16] or "无名鱼"
        want = (request.query.get("role") or "auto").lower()

        room = self.rooms.get(code)
        if not room:
            ws = web.WebSocketResponse(heartbeat=25)
            await ws.prepare(request)
            await self._send(ws, {"t": "error", "msg": "房间不存在或已关闭", "fatal": True})
            await ws.close()
            return ws

        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=MAX_MSG)
        await ws.prepare(request)

        # ---- 角色分配 ----
        role = "guest"
        if want == "bot":
            role = "bot"
        elif room.host is None:
            role = "host"
        elif want == "host":
            role = "guest"  # 房主位已被占，降级为玩家

        if role == "guest" and room.is_full():
            await self._send(ws, {"t": "error", "msg": "房间满了", "fatal": True})
            await ws.close()
            return ws

        peer = Peer(pid=self.new_pid(role), name=name, role=role, ws=ws)
        if role == "host":
            room.host = peer
        else:
            room.peers[peer.pid] = peer
        room.last_active = time.time()

        logger.info(f"[bigfish] {name}({peer.pid}) 以 {role} 加入房间 {code}")

        await self._send(
            ws,
            {
                "t": "welcome",
                "pid": peer.pid,
                "role": role,
                "room": room.code,
                "roster": room.roster(),
                "max_players": room.max_players,
                "started": room.started,
            },
        )
        # 通知房主有新玩家
        if room.host and role != "host":
            await self._send(
                room.host.ws,
                {"t": "peer_join", "pid": peer.pid, "name": peer.name, "role": peer.role},
            )
            # 把房主名单同步给其他人
            await self.broadcast(room, {"t": "roster", "roster": room.roster()})

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._on_message(room, peer, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    break
        except Exception as e:
            logger.debug(f"[bigfish] ws 异常 {e}")
        finally:
            await self._on_leave(room, peer)
        return ws

    async def _on_message(self, room: Room, peer: Peer, raw: str) -> None:
        room.last_active = time.time()
        try:
            data = json.loads(raw)
        except Exception:
            return
        t = data.get("t")

        if t == "ping":
            await self._send(peer.ws, {"t": "pong"})
            return

        if peer.role == "host":
            if t == "start":
                room.started = True
                await self.broadcast(room, {"t": "start", "peer": peer.pid}, exclude=peer.pid)
            elif t == "state":
                data["tk"] = data.get("tk", 0)
                await self.broadcast(room, data, exclude=peer.pid)
            elif t == "end":
                room.started = False
                await self.broadcast(room, {"t": "end", "result": data.get("result") or {}})
                if self.on_end:
                    try:
                        await self.on_end(room.code, data.get("result") or {})
                    except Exception as e:
                        logger.error(f"[bigfish] on_end 回调出错: {e}")
            elif t == "event":
                # 房主转发的小播报（比如"谁吃掉了谁"），目前只用于日志
                if self.on_event:
                    try:
                        await self.on_event(room.code, "game", data)
                    except Exception:
                        pass
            return

        # ---- 玩家 → 房主 ----
        if t in ("input", "join", "leave", "ready", "chat"):
            if room.host:
                payload = dict(data)
                payload["pid"] = peer.pid
                payload["name"] = peer.name
                await self._send(room.host.ws, payload)

    async def _on_leave(self, room: Room, peer: Peer) -> None:
        if room.host is peer:
            room.host = None
            room.started = False
            # 房主走了，房间就没法继续了
            await self.broadcast(room, {"t": "host_left", "msg": "房主断线，本局结束"})
            self.rooms.pop(room.code, None)
            logger.info(f"[bigfish] 房间 {room.code} 关闭（房主离开）")
            return
        room.peers.pop(peer.pid, None)
        if room.host:
            await self._send(
                room.host.ws, {"t": "peer_leave", "pid": peer.pid, "name": peer.name}
            )
            await self.broadcast(room, {"t": "roster", "roster": room.roster()})

    # ------------------------------------------------------------ 维护
    async def sweep(self, ttl: float = 3600.0) -> None:
        now = time.time()
        for code, room in list(self.rooms.items()):
            if not room.host and not room.peers:
                self.rooms.pop(code, None)
            elif now - room.last_active > ttl:
                await self.close_room(code, "房间超时")

    def public_url(self, base: str, code: str) -> str:
        return f"{base.rstrip('/')}/?room={code}"
