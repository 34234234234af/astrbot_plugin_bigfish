"""本地冒烟测试：不启动 AstrBot，直接跑无头对局。

    python tools/selftest.py

输出几局模拟结果 + 排版后的战报文本，用来验证规则/AI/排版是否正常。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "_stub"))
sys.path.insert(0, PLUGIN_ROOT)

from bigfish_core.engine import Sim  # noqa: E402
from bigfish_core.report import format_report  # noqa: E402


def play(seed: int = 1, duration: float = 90.0) -> dict:
    sim = Sim(
        players=[
            {"id": "bot", "name": "小鱼苗", "skill": 0.75},
            {"id": "rival", "name": "水母帮凶", "skill": 0.6},
        ],
        duration=duration,
        seed=seed,
    )
    while not sim.step():
        pass
    return sim.result()


def main() -> int:
    for seed in (1, 2, 3):
        res = play(seed)
        rows = res["players"]
        print(f"--- seed={seed} ---")
        for r in rows:
            print(
                f"  {r['name']:<8} score={r['score']:<5} eaten={r['eaten']:<3} "
                f"deaths={r['deaths']} max_size={r['max_size']}"
            )
        print(f"  winner={res['winner']} events={len(res['events'])}")

    res = play(7)
    print()
    print(format_report(res, "bot", title="🐟 我自己玩了一把", flavor="今天手感不错。"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
