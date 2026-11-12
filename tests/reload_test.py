"""回归测试：AstrBot 热重载插件时，子模块必须重新加载。

    python tests/reload_test.py

背景：AstrBot 的「重载插件」只重新导入 main.py，已进入 sys.modules 的
bigfish_core / bigfish_net 不会重新执行，于是出现过
「main.py 已是新版、botclient.py 还在跑旧版 → BotPlayer.__init__() 报 unexpected keyword」。
main.py 开头的清理逻辑就是为这个加的，这里专门验证它。
"""

from __future__ import annotations

import importlib
import inspect
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
shutil.rmtree(os.path.join(HERE, "_stub_data"), ignore_errors=True)

sys.path.insert(0, os.path.join(HERE, "_stub"))
sys.path.insert(0, PLUGIN_ROOT)


def params_of(cls) -> set[str]:
    return set(inspect.signature(cls).parameters)


def main() -> int:
    print("1) 首次加载")
    import main as m1  # noqa: F401

    p1 = params_of(m1.BotPlayer)
    assert "max_players" in p1, f"当前版本的 BotPlayer 应该有 max_players：{p1}"
    assert "on_end" in p1, f"当前版本的 BotPlayer 应该有 on_end：{p1}"
    print(f"   [ok] BotPlayer 参数: {sorted(p1)}")

    print("2) 伪造一个「旧版」子模块留在 sys.modules 里")
    fake = types.ModuleType("bigfish_net.botclient")

    class OldBotPlayer:
        """模拟 v0.2.0 的旧类：没有 max_players / on_end。"""

        def __init__(self, relay_url, room, name, skill=0.7, on_close=None):
            pass

    fake.BotPlayer = OldBotPlayer
    sys.modules["bigfish_net.botclient"] = fake
    print("   [ok] 已注入旧模块（这就是热重载时真实发生的状态）")

    print("3) 重新导入 main.py（等价于点一次「重载插件」）")
    assert "main" in sys.modules
    del sys.modules["main"]
    m2 = importlib.import_module("main")

    p2 = params_of(m2.BotPlayer)
    assert m2.BotPlayer is not OldBotPlayer, "重载后还在用旧模块，清理逻辑失效了"
    assert "max_players" in p2, f"重载后 BotPlayer 缺少 max_players：{p2}"
    assert "on_end" in p2, f"重载后 BotPlayer 缺少 on_end：{p2}"
    print(f"   [ok] 重载后 BotPlayer 参数: {sorted(p2)}")

    print("4) 旧模块已从 sys.modules 清除")
    stale = [
        k
        for k, v in sys.modules.items()
        if (k == "bigfish_net.botclient" or k.startswith("bigfish_net.") or k.startswith("bigfish_core."))
        and getattr(v, "BotPlayer", None) is OldBotPlayer
    ]
    assert not stale, f"还有残留的旧模块：{stale}"
    print("   [ok] 没有残留")

    print("\n热重载回归测试通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
