"""测试用的 astrbot.api 替身。"""

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

logger = logging.getLogger("astrbot.stub")


class AstrBotConfig(dict):
    pass
