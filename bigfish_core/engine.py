"""大鱼吃小鱼 —— 纯逻辑内核。

只负责"世界怎么跑"，不关心 AstrBot、网络、渲染。
真实房间的权威模拟在浏览器端（web/index.html），那边是同一套规则；
这里这份用于 bot 无头自娱对局（快进几十秒算完，几乎不占 CPU）。

规则摘要：
- 体型差 >= 12% 才能吃掉对方，得分 = 对手体型 + 10；
- 吃掉后体型按 sqrt(a² + 0.5b²) 成长，上限 110；
- 被吃掉 3 秒后原地复活，体型回到初始值，得分不清零（按总分排名）；
- 时间到就结束，得分最高者获胜。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .ai import EAT_RATIO, decide as ai_decide

WORLD_W = 1600.0
WORLD_H = 900.0
TICK = 1.0 / 30.0

MAX_SIZE = 110.0
PLAYER_START = 20.0
NPC_MIN = 8.0
ACCEL = 1500.0
NPC_TARGET = 26
RESPAWN_DELAY = 2.5
RESPAWN_INVULN = 2.0
SCORE_PER_EAT = 10


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def max_speed(size: float) -> float:
    norm = clamp((size - PLAYER_START) / (MAX_SIZE - PLAYER_START), 0.0, 1.0)
    return 300.0 - 150.0 * norm


def eat_reach(a_size: float, b_size: float) -> float:
    return a_size * 0.95 + b_size * 0.30


@dataclass
class Entity:
    eid: int
    x: float
    y: float
    size: float
    kind: str = "npc"          # npc | player
    name: str = ""
    pid: str = ""
    vx: float = 0.0
    vy: float = 0.0
    dirx: float = 1.0
    diry: float = 0.0
    alive: bool = True
    respawn_at: float = 0.0
    invuln_until: float = 0.0
    eaten: int = 0
    deaths: int = 0
    score: int = 0
    max_size: float = 0.0
    color: int = 0
    skill: float = 0.7         # 仅 AI 玩家使用
    wander_ang: float = 0.0
    wander_t: float = 0.0

    def __post_init__(self):
        if not self.max_size:
            self.max_size = self.size
        if not self.wander_ang:
            self.wander_ang = random.uniform(0, math.tau)


class Sim:
    """一场对局的完整状态。step() 推进一帧，snapshot() 给 bot/前端用。"""

    def __init__(
        self,
        players: list[dict] | None = None,
        duration: float = 90.0,
        seed: int | None = None,
        npc_target: int = NPC_TARGET,
    ):
        self.rng = random.Random(seed)
        self.seed = seed
        self.duration = float(duration)
        self.elapsed = 0.0
        self.npc_target = int(npc_target)
        self.entities: list[Entity] = []
        self.events: list[dict] = []
        self._next_id = 1
        self._finished = False

        players = players or [{"id": "p1", "name": "bot", "skill": 0.7}]
        for i, p in enumerate(players):
            pid = str(p.get("id") or f"p{i + 1}")
            ang = math.tau * i / max(1, len(players))
            ent = Entity(
                eid=self._next_id,
                x=WORLD_W * 0.5 + math.cos(ang) * 260.0,
                y=WORLD_H * 0.5 + math.sin(ang) * 200.0,
                size=PLAYER_START,
                kind="player",
                name=str(p.get("name") or pid),
                pid=pid,
                color=i,
                skill=float(p.get("skill", 0.7)),
            )
            self._next_id += 1
            self.entities.append(ent)

        for _ in range(self.npc_target):
            self._spawn_npc()

    # ---------------------------------------------------------------- 生成
    def _spawn_npc(self) -> None:
        players = [e for e in self.entities if e.kind == "player"]
        base = max((e.size for e in players), default=PLAYER_START)
        # 大部分是小鱼，偶尔刷出一条比玩家大的，制造压力
        roll = self.rng.random()
        if roll < 0.10:
            size = base * self.rng.uniform(1.05, 1.6)
        elif roll < 0.52:
            size = base * self.rng.uniform(0.35, 0.9)
        else:
            size = base * self.rng.uniform(0.12, 0.35)
        size = clamp(size, NPC_MIN, MAX_SIZE)

        for _ in range(12):  # 尽量别刷在玩家脸上
            x = self.rng.uniform(40, WORLD_W - 40)
            y = self.rng.uniform(40, WORLD_H - 40)
            if all(math.hypot(x - p.x, y - p.y) > 220 for p in players):
                break

        ent = Entity(
            eid=self._next_id,
            x=x,
            y=y,
            size=size,
            kind="npc",
            color=self.rng.randrange(6),
        )
        self._next_id += 1
        self.entities.append(ent)

    # ---------------------------------------------------------------- 输入
    def set_input(self, pid: str, dx: float, dy: float) -> None:
        for e in self.entities:
            if e.kind == "player" and e.pid == pid and e.alive:
                d = math.hypot(dx, dy)
                if d > 1e-6:
                    e.dirx, e.diry = dx / d, dy / d
                else:
                    e.dirx = e.diry = 0.0
                return

    # ---------------------------------------------------------------- 推进
    def _steer_npc(self, e: Entity, dt: float) -> None:
        # 简单生态：追比自己小的，躲比自己大的
        flee_x = flee_y = 0.0
        chase_x = chase_y = 0.0
        for o in self.entities:
            if o is e or not o.alive:
                continue
            dx, dy = o.x - e.x, o.y - e.y
            d = math.hypot(dx, dy)
            if d > 360 or d < 1e-6:
                continue
            if o.size >= e.size * EAT_RATIO:
                flee_x -= dx / d
                flee_y -= dy / d
            elif e.size >= o.size * EAT_RATIO:
                chase_x += dx / d * (1.0 - d / 360)
                chase_y += dy / d * (1.0 - d / 360)

        e.wander_t -= dt
        if e.wander_t <= 0:
            e.wander_t = self.rng.uniform(0.8, 2.4)
            e.wander_ang = self.rng.uniform(0, math.tau)

        wx, wy = math.cos(e.wander_ang), math.sin(e.wander_ang)
        dx = chase_x * 1.0 + wx * 0.6 + flee_x * 2.0
        dy = chase_y * 1.0 + wy * 0.6 + flee_y * 2.0

        # 别出界
        if e.x < 70:
            dx += 0.8
        elif e.x > WORLD_W - 70:
            dx -= 0.8
        if e.y < 70:
            dy += 0.8
        elif e.y > WORLD_H - 70:
            dy -= 0.8

        d = math.hypot(dx, dy)
        if d > 1e-6:
            e.dirx, e.diry = dx / d, dy / d
        else:
            e.dirx = e.diry = 0.0

    def step(self, dt: float = TICK) -> bool:
        if self._finished:
            return True
        self.elapsed += dt

        snap = self.snapshot() if self._ai_players else None
        for e in self.entities:
            if not e.alive:
                if e.kind == "player" and self.elapsed >= e.respawn_at:
                    self._respawn(e)
                continue
            if e.kind == "npc":
                self._steer_npc(e, dt)
            elif self._ai_players:
                dx, dy = ai_decide(snap, e.pid, e.skill, self.rng)
                if dx or dy:
                    e.dirx, e.diry = dx, dy

            sp = max_speed(e.size)
            dvx = e.dirx * sp - e.vx
            dvy = e.diry * sp - e.vy
            dl = math.hypot(dvx, dvy)
            maxdv = ACCEL * dt
            if dl > maxdv and dl > 0:
                dvx = dvx / dl * maxdv
                dvy = dvy / dl * maxdv
            e.vx += dvx
            e.vy += dvy

            e.x = clamp(e.x + e.vx * dt, e.size, WORLD_W - e.size)
            e.y = clamp(e.y + e.vy * dt, e.size, WORLD_H - e.size)

        self._resolve_eat()

        # 补充 NPC
        alive_npc = sum(1 for e in self.entities if e.kind == "npc" and e.alive)
        for _ in range(min(3, self.npc_target - alive_npc)):
            self._spawn_npc()
        if len(self.entities) > 220:  # 兜底，防内存增长
            self.entities = [e for e in self.entities if e.alive or e.kind == "player"]

        if self.elapsed >= self.duration:
            self._finished = True
        return self._finished

    @property
    def _ai_players(self) -> bool:
        return getattr(self, "auto_pilot", True)

    auto_pilot: bool = True

    def _resolve_eat(self) -> None:
        alive = [e for e in self.entities if e.alive]
        for a in alive:
            if not a.alive:
                continue
            for b in alive:
                if a is b or not b.alive or not a.alive:
                    continue
                if a.size < b.size * EAT_RATIO:
                    continue
                if a.invuln_until > self.elapsed or b.invuln_until > self.elapsed:
                    continue
                if math.hypot(a.x - b.x, a.y - b.y) > eat_reach(a.size, b.size):
                    continue
                self._eat(a, b)

    def _eat(self, a: Entity, b: Entity) -> None:
        gain = int(b.size) + SCORE_PER_EAT
        a.score += gain
        a.eaten += 1
        a.size = min(MAX_SIZE, math.sqrt(a.size * a.size + 0.5 * b.size * b.size))
        a.max_size = max(a.max_size, a.size)

        b.alive = False
        self.events.append(
            {
                "t": round(self.elapsed, 1),
                "type": b.kind == "player" and "player_eaten" or "eat",
                "by": a.pid or "npc",
                "by_name": a.name if a.kind == "player" else "",
                "size": round(b.size, 1),
                "gain": gain,
                "target_player": b.pid if b.kind == "player" else "",
            }
        )
        if b.kind == "player":
            b.deaths += 1
            b.respawn_at = self.elapsed + RESPAWN_DELAY
            b.vx = b.vy = 0.0
        else:
            self.entities.remove(b)

    def _respawn(self, e: Entity) -> None:
        e.alive = True
        e.size = PLAYER_START
        e.vx = e.vy = 0.0
        e.invuln_until = self.elapsed + RESPAWN_INVULN
        x, y = WORLD_W * 0.5, WORLD_H * 0.5
        for attempt in range(40):
            cx = self.rng.uniform(60, WORLD_W - 60)
            cy = self.rng.uniform(60, WORLD_H - 60)
            safe = True
            need = 300 if attempt < 30 else 160
            for o in self.entities:
                if o is e or not o.alive:
                    continue
                if o.size >= PLAYER_START * EAT_RATIO and math.hypot(cx - o.x, cy - o.y) < need:
                    safe = False
                    break
            if safe:
                x, y = cx, cy
                break
        e.x, e.y = x, y

    # ---------------------------------------------------------------- 输出
    def snapshot(self) -> dict:
        ents = []
        for e in self.entities:
            if not e.alive:
                continue
            ents.append(
                {
                    "i": e.eid,
                    "x": round(e.x, 1),
                    "y": round(e.y, 1),
                    "s": round(e.size, 1),
                    "p": e.pid if e.kind == "player" else "",
                    "n": e.name if e.kind == "player" else "",
                    "a": True,
                }
            )
        return {
            "tl": round(max(0.0, self.duration - self.elapsed), 1),
            "w": WORLD_W,
            "h": WORLD_H,
            "ents": ents,
        }

    def players(self) -> list[Entity]:
        return [e for e in self.entities if e.kind == "player"]

    def result(self) -> dict:
        ps = sorted(self.players(), key=lambda e: e.score, reverse=True)
        rows = [
            {
                "id": e.pid,
                "name": e.name,
                "score": e.score,
                "eaten": e.eaten,
                "deaths": e.deaths,
                "max_size": round(e.max_size, 1),
            }
            for e in ps
        ]
        return {
            "duration": round(min(self.elapsed, self.duration), 1),
            "seed": self.seed,
            "players": rows,
            "winner": rows[0]["id"] if rows else "",
            "events": self.events,
        }
