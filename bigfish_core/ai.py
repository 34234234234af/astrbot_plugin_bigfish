"""bot 的"鱼脑"。

这个模块只依赖快照数据（dict），不依赖 AstrBot，也不依赖 engine，所以：
- 无头自娱对局里，它同时扮演 bot 和陪练；
- 真实房间中，bot 客户端收到 web 前端发来的快照后，也用它算方向。

快照格式（与 web/index.html 保持一致）::

    {
      "t": 剩余秒数 (float),
      "w": 世界宽, "h": 世界高,
      "ents": [
        {"i": 实体id(int), "x": float, "y": float, "s": 体型(float),
         "p": "玩家id或空", "n": 名字, "a": 是否可见(bool)},
        ...
      ]
    }
"""

from __future__ import annotations

import math
import random

EAT_RATIO = 1.12      # 与 engine / 前端一致：体型差 12% 以上才能吃
VISION = 620.0        # 捕食视野
THREAT_RANGE = 420.0  # 躲避视野
WALL_MARGIN = 90.0    # 离墙多近开始往回游


def _len(x: float, y: float) -> float:
    return math.hypot(x, y)


def _norm(x: float, y: float):
    d = _len(x, y)
    if d < 1e-6:
        return (0.0, 0.0)
    return (x / d, y / d)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _wall_push(x: float, y: float, w: float, h: float):
    """贴近边界时给一个向内的推力，避免 bot 一头撞死在墙上。"""
    px = 0.0
    py = 0.0
    if x < WALL_MARGIN:
        px += (WALL_MARGIN - x) / WALL_MARGIN
    elif x > w - WALL_MARGIN:
        px -= (x - (w - WALL_MARGIN)) / WALL_MARGIN
    if y < WALL_MARGIN:
        py += (WALL_MARGIN - y) / WALL_MARGIN
    elif y > h - WALL_MARGIN:
        py -= (y - (h - WALL_MARGIN)) / WALL_MARGIN
    return (px, py)


def decide(
    snapshot: dict,
    my_pid: str,
    skill: float = 0.7,
    rng: random.Random | None = None,
) -> tuple[float, float]:
    """返回一个单位方向向量 (dx, dy)。

    skill 越大越强：视野更远、更少犯迷糊、躲避得更早。
    """
    rng = rng or random.Random()
    ents = snapshot.get("ents") or []
    w = float(snapshot.get("w") or 1600.0)
    h = float(snapshot.get("h") or 900.0)

    me = None
    for e in ents:
        if e.get("p") == my_pid and e.get("a", True):
            me = e
            break
    if me is None:
        return (0.0, 0.0)

    mx = float(me.get("x", 0.0))
    my = float(me.get("y", 0.0))
    ms = float(me.get("s", 20.0))

    vision = VISION * (0.55 + 0.75 * skill)
    threat_range = THREAT_RANGE * (0.6 + 0.8 * skill)

    # ---- 威胁评估：谁现在能一口吞了我 ----
    fx = 0.0
    fy = 0.0
    worst = 0.0
    for e in ents:
        if e is me or not e.get("a", True):
            continue
        es = float(e.get("s", 0.0))
        if es < ms * EAT_RATIO:
            continue
        dx = float(e.get("x", 0.0)) - mx
        dy = float(e.get("y", 0.0)) - my
        d = _len(dx, dy)
        if d > threat_range or d < 1e-6:
            continue
        # 越近、越大，越可怕
        wgt = ((1.0 - d / threat_range) ** 2) * (es / ms)
        ux, uy = _norm(dx, dy)
        fx -= ux * wgt
        fy -= uy * wgt
        worst = max(worst, wgt)

    # ---- 目标评估：哪个能吃的最划算 ----
    tx = 0.0
    ty = 0.0
    best = 0.0
    for e in ents:
        if e is me or not e.get("a", True):
            continue
        es = float(e.get("s", 0.0))
        if es * EAT_RATIO > ms:      # 吃不掉
            continue
        dx = float(e.get("x", 0.0)) - mx
        dy = float(e.get("y", 0.0)) - my
        d = _len(dx, dy)
        if d > vision or d < 1e-6:
            continue
        # 值钱（大）+ 近
        val = (es / ms) * 1.6 + 1.0 / (1.0 + d / 220.0)
        if val > best:
            best = val
            ux, uy = _norm(dx, dy)
            tx = ux
            ty = uy

    wx, wy = _wall_push(mx, my, w, h)

    # 吓破了胆就别贪吃
    hunt_w = 1.35 * best * (1.0 - _clamp(worst * 1.6, 0.0, 0.95))
    flee_w = 2.4 * worst

    dx = tx * hunt_w + fx * flee_w + wx * 1.2
    dy = ty * hunt_w + fy * flee_w + wy * 1.2

    if _len(dx, dy) < 1e-3:
        # 什么都没有：朝世界中心附近游，别停在角落
        dx = (w * 0.5 - mx) * 0.01
        dy = (h * 0.5 - my) * 0.01
        if _len(dx, dy) < 1e-3:
            ang = rng.uniform(0, math.tau)
            dx, dy = math.cos(ang), math.sin(ang)

    # 失误率：skill 低的时候偶尔会朝反方向冲动一下
    if rng.random() > 0.35 + 0.6 * skill:
        ang = math.atan2(dy, dx) + rng.uniform(-1.2, 1.2)
        dx, dy = math.cos(ang), math.sin(ang)

    return _norm(dx, dy)
