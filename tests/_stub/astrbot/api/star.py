"""测试用替身：astrbot.api.star"""


class Context:
    def __init__(self):
        self.sent = []
        self._provider = None

    async def send_message(self, umo, chain):
        self.sent.append((umo, getattr(chain, "text", str(chain))))

    def get_using_provider(self, umo=None):
        return self._provider


class Star:
    def __init__(self, context):
        self.context = context
        self.name = "astrbot_plugin_bigfish"


def register(*args, **kwargs):
    def deco(cls):
        cls.__plugin_meta__ = args
        return cls

    return deco
