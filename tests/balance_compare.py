"""平衡性对比：改动前（HEAD）vs 改动后（工作区）。

    python tests/balance_compare.py
"""
from __future__ import annotations

import os
import shutil
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)

SEEDS = list(range(1, 11))


def _load(package_dir: str, tag: str):
    """从指定目录加载 engine 模块（用独立模块名避免冲突）。"""
    import importlib.util

    for name in [m for m in list(sys.modules) if m.startswith(f"_{tag}bf")]:
        del sys.modules[name]
    pkg_name = f"_{tag}bf"
    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.bigfish_core",
        os.path.join(package_dir, "bigfish_core", "__init__.py"),
        submodule_search_locations=[os.path.join(package_dir, "bigfish_core")],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.bigfish_core"] = pkg
    spec.loader.exec_module(pkg)
    # 让 engine.py 里的 `from .ai import` 能找到包
    return importlib.import_module(f"{pkg_name}.bigfish_core.engine")


def measure(engine, seeds):
    Sim = engine.Sim
    PLAYER_START, EAT_RATIO = engine.PLAYER_START, engine.EAT_RATIO

    # 分布：清空 NPC 后逐个刷、刷完就删，避免"大鱼上限"干扰
    sim = Sim(players=[{"id": "p", "name": "A", "skill": 0.7}], duration=90, seed=1)
    sim.entities = [e for e in sim.entities if e.kind == "player"]
    sizes = []
    for _ in range(1200):
        sim._spawn_npc()
        e = sim.entities[-1]
        sizes.append(e.size)
        sim.entities.pop()
    big = sum(1 for s in sizes if s >= PLAYER_START * EAT_RATIO) / len(sizes)
    small = sum(1 for s in sizes if s < PLAYER_START * 0.5) / len(sizes)

    # 对局
    deaths_total = 0
    deaths_by_npc = 0
    deaths_by_player = 0
    scores = []
    for seed in seeds:
        s = Sim(
            players=[
                {"id": "bot", "name": "B", "skill": 0.75},
                {"id": "r", "name": "R", "skill": 0.65},
            ],
            duration=90,
            seed=seed,
        )
        while not s.step():
            pass
        ps = s.players()
        deaths_total += sum(p.deaths for p in ps)
        scores.extend(p.score for p in ps)
        for ev in s.events:
            if ev.get("type") == "player_eaten":
                if ev.get("by") == "npc":
                    deaths_by_npc += 1
                else:
                    deaths_by_player += 1

    return {
        "big_pct": big * 100,
        "small_pct": small * 100,
        "median_size": statistics.median(sizes),
        "deaths": deaths_total,
        "deaths_npc": deaths_by_npc,
        "deaths_pvp": deaths_by_player,
        "score_mean": statistics.mean(scores),
        "score_max": max(scores),
    }


def main() -> int:
    # 1) 把 HEAD 版本的 engine.py 拿出来，放到临时副本里
    tmp = tempfile.mkdtemp(prefix="bfold_")
    old_pkg = os.path.join(tmp, "pkg")
    shutil.copytree(
        os.path.join(PLUGIN, "bigfish_core"),
        os.path.join(old_pkg, "bigfish_core"),
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    head_engine = subprocess.check_output(
        ["git", "-C", PLUGIN, "show", "HEAD:bigfish_core/engine.py"], text=True
    )
    with open(os.path.join(old_pkg, "bigfish_core", "engine.py"), "w", encoding="utf-8") as f:
        f.write(head_engine)

    old_engine = _load(old_pkg, "old")
    new_engine = _load(PLUGIN, "new")

    old = measure(old_engine, SEEDS)
    new = measure(new_engine, SEEDS)

    rows = [
        ("能吃掉玩家的鱼占比", f"{old['big_pct']:.1f}%", f"{new['big_pct']:.1f}%"),
        ("小鱼（<玩家一半）占比", f"{old['small_pct']:.1f}%", f"{new['small_pct']:.1f}%"),
        ("刷出鱼的中位体型", f"{old['median_size']:.1f}", f"{new['median_size']:.1f}"),
        ("10 局总死亡（2 人）", str(old["deaths"]), str(new["deaths"])),
        ("  其中被 NPC 吃掉", str(old["deaths_npc"]), str(new["deaths_npc"])),
        ("  其中被对手吃掉", str(old["deaths_pvp"]), str(new["deaths_pvp"])),
        ("平均得分", f"{old['score_mean']:.0f}", f"{new['score_mean']:.0f}"),
        ("最高分", f"{old['score_max']:.0f}", f"{new['score_max']:.0f}"),
    ]
    print(f"{'指标':<22}{'改动前':>10}{'改动后':>10}")
    print("-" * 44)
    for name, a, b in rows:
        print(f"{name:<22}{a:>10}{b:>10}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
