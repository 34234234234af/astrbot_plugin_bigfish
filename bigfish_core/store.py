"""插件数据持久化：战绩、活跃时间、已记录的会话。

JSON 存在 AstrBot 的 data/plugin_data/<plugin>/ 下，重装插件不会丢。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _now() -> float:
    return time.time()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {
            "stats": {           # bot 自娱的战绩
                "plays": 0,
                "wins": 0,
                "losses": 0,
                "best_score": 0,
                "total_score": 0,
                "total_eaten": 0,
                "total_deaths": 0,
                "last_play": 0,
                "last_report": 0,
                "last_flavor": "",
            },
            "daily": {},         # {"2026-05-01": 2} 当天自娱次数
            "activity": 0,       # 最后一条（群/私聊）消息时间
            "owner_umo": "",     # 用来私聊汇报的会话
            "board": {},         # {玩家名: {"score":x,"plays":n,"wins":n}}
            "rooms": {},         # 未结束的房间，重启后可清理
        }
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text("utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if k not in self.data:
                            continue
                        cur = self.data[k]
                        if isinstance(cur, dict) and isinstance(v, dict):
                            cur.update(v)
                        else:
                            self.data[k] = v
        except Exception:
            # 文件损坏不影响插件启动
            pass

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ------------------------------------------------------- 活动/会话
    def touch_activity(self) -> None:
        self.data["activity"] = _now()

    def idle_seconds(self) -> float:
        last = self.data.get("activity") or 0
        return _now() - last if last else 1e9

    def remember_owner(self, umo: str) -> None:
        if umo and umo != self.data.get("owner_umo"):
            self.data["owner_umo"] = umo
            self.save()

    # ------------------------------------------------------- 自娱结算
    def can_self_play(self, daily_limit: int) -> bool:
        if daily_limit <= 0:
            return True
        return int(self.data["daily"].get(_today(), 0)) < daily_limit

    def record_self_play(self, result: dict, me_pid: str) -> dict:
        st = self.data["stats"]
        me = next((r for r in result.get("players", []) if r.get("id") == me_pid), None)
        rows = result.get("players") or []
        won = bool(rows) and rows[0].get("id") == me_pid and len(rows) > 1

        st["plays"] += 1
        st["last_play"] = _now()
        if len(rows) > 1:
            st["wins" if won else "losses"] += 1
        if me:
            st["best_score"] = max(st["best_score"], int(me.get("score", 0)))
            st["total_score"] += int(me.get("score", 0))
            st["total_eaten"] += int(me.get("eaten", 0))
            st["total_deaths"] += int(me.get("deaths", 0))
        d = _today()
        self.data["daily"][d] = int(self.data["daily"].get(d, 0)) + 1
        # 只留最近 30 天
        if len(self.data["daily"]) > 30:
            for k in sorted(self.data["daily"])[: len(self.data["daily"]) - 30]:
                self.data["daily"].pop(k, None)
        self.save()
        return st

    def mark_reported(self) -> None:
        self.data["stats"]["last_report"] = _now()
        self.save()

    # ------------------------------------------------------- 房间
    def add_room(self, code: str, info: dict) -> None:
        self.data["rooms"][code] = info
        self.save()

    def drop_room(self, code: str) -> None:
        self.data["rooms"].pop(code, None)
        self.save()

    # ------------------------------------------------------- 排行榜
    def record_board(self, rows: list[dict], winner_id: str = "") -> None:
        board = self.data["board"]
        for r in rows:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            slot = board.setdefault(name, {"score": 0, "plays": 0, "wins": 0})
            slot["plays"] = int(slot.get("plays", 0)) + 1
            slot["score"] = int(r.get("score", 0))
            if winner_id and r.get("id") == winner_id:
                slot["wins"] = int(slot.get("wins", 0)) + 1
        self.save()

    def top_board(self, n: int = 10) -> list[tuple[str, dict]]:
        items = list(self.data["board"].items())
        items.sort(key=lambda kv: (kv[1].get("wins", 0), kv[1].get("score", 0)), reverse=True)
        return items[:n]
