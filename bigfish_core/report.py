"""把一局结果排版成人话。"""

from __future__ import annotations

import time

MEDALS = ["🥇", "🥈", "🥉"]


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def pick_highlights(result: dict, me_pid: str, limit: int = 3) -> list[str]:
    """从事件流里挑出属于我的高光/惨案。"""
    evs = result.get("events") or []
    mine: list[str] = []

    big_eat = [
        e
        for e in evs
        if e.get("by") == me_pid and e.get("type") in ("eat", "player_eaten")
    ]
    big_eat.sort(key=lambda e: e.get("gain", 0), reverse=True)
    for e in big_eat[: max(1, limit - 1)]:
        mine.append(f"第 {e['t']}s 吃掉一条 {e['size']} 的鱼（+{e['gain']}）")

    deaths = [
        e
        for e in evs
        if e.get("type") == "player_eaten" and e.get("target_player") == me_pid
    ]
    if deaths:
        mine.append(f"第 {deaths[0]['t']}s 被一条 {deaths[0]['size']} 的鱼抓走了")
    return mine[:limit]


def format_report(
    result: dict,
    me_pid: str,
    *,
    title: str = "🐟 我自己玩了一把",
    history: dict | None = None,
    flavor: str = "",
) -> str:
    rows = result.get("players") or []
    me = next((r for r in rows if r.get("id") == me_pid), None)
    others = [r for r in rows if r.get("id") != me_pid]

    lines = [f"{title}"]

    if me and others:
        rival = others[0]
        win = me["score"] >= rival["score"]
        head = "我赢了" if win else "我输了"
        lines.append(f"{me['name']} {me['score']} : {rival['score']} {rival['name']} —— {head}")
    elif me:
        lines.append(f"{me['name']} 拿了 {me['score']} 分")

    if me:
        lines.append(
            f"一共吃了 {me['eaten']} 条鱼，被吃 {me['deaths']} 次，"
            f"最大体型 {me['max_size']}，撑了 {int(result.get('duration', 0))} 秒"
        )
        hi = pick_highlights(result, me_pid)
        if hi:
            lines.append("")
            lines.extend(f"· {h}" for h in hi)

    if flavor:
        lines.append("")
        lines.append(flavor)

    if history:
        w = history.get("wins", 0)
        l = history.get("losses", 0)
        best = history.get("best_score", 0)
        if w or l:
            lines.append("")
            lines.append(f"累计战绩：{w} 胜 {l} 负，最高 {best} 分")

    return "\n".join(lines)


def format_room_result(result: dict, bot_pid: str = "") -> str:
    """群里播报的房间结果。"""
    rows = result.get("players") or []
    if not rows:
        return "这局没人玩，散了散了。"
    rows = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)
    lines = ["🏁 本局结束"]
    for i, r in enumerate(rows):
        medal = MEDALS[i] if i < len(MEDALS) else "　"
        tag = "（bot）" if bot_pid and r.get("id") == bot_pid else ""
        lines.append(
            f"{medal} {r.get('name', '?')}{tag} — {r.get('score', 0)} 分，"
            f"吃了 {r.get('eaten', 0)} 条，被吃 {r.get('deaths', 0)} 次"
        )
    return "\n".join(lines)
