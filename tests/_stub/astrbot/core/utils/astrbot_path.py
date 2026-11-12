"""测试用替身：把插件数据目录指到 tools/_stub_data 下，避免污染插件目录。"""

import os

# __file__ = tools/_stub/astrbot/core/utils/astrbot_path.py
_STUB_PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_BASE = os.path.join(os.path.dirname(_STUB_PKG), "_stub_data")


def get_astrbot_data_path() -> str:
    os.makedirs(_BASE, exist_ok=True)
    return _BASE
