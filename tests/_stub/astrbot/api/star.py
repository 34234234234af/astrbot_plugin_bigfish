"""测试用替身：astrbot.api.star"""


class FakePersonaManager:
    """模拟 AstrBot 的人格对象（Personality 是 dict，人格正文在 prompt 键）。"""

    def __init__(self, prompt: str = "", name: str = "测试人格"):
        self._persona = {"prompt": prompt, "name": name}

    async def get_default_persona_v3(self, umo=None):
        return self._persona

    def get_persona_v3_by_id(self, persona_id):
        return self._persona if persona_id else None


class FakeLLMResponse:
    def __init__(self, text: str):
        self.completion_text = text


class Context:
    def __init__(self):
        self.sent = []
        self._provider = None
        self.persona_manager = FakePersonaManager("你是一只嘴很毒的小水母。")
        self.llm_calls = []
        self.llm_reply = "这把我赢得挺轻松，对面是在游还是在散步？"

    async def send_message(self, umo, chain):
        self.sent.append((umo, getattr(chain, "text", str(chain))))

    def get_using_provider(self, umo=None):
        return self._provider

    async def get_current_chat_provider_id(self, umo=None):
        return "fake-provider"

    async def llm_generate(self, *, chat_provider_id, prompt=None, system_prompt=None, **kwargs):
        self.llm_calls.append(
            {
                "provider_id": chat_provider_id,
                "prompt": prompt,
                "system_prompt": system_prompt,
            }
        )
        return FakeLLMResponse(self.llm_reply)


class Star:
    def __init__(self, context):
        self.context = context
        self.name = "astrbot_plugin_bigfish"


def register(*args, **kwargs):
    def deco(cls):
        cls.__plugin_meta__ = args
        return cls

    return deco
