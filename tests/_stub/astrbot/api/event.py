"""测试用替身：astrbot.api.event"""


class AstrMessageEvent:
    def __init__(self, message_str="", umo="test:GroupMessage:100", sender_id="1", name="测试员"):
        self.message_str = message_str
        self.unified_msg_origin = umo
        self._sender_id = sender_id
        self._name = name

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return self._name

    def is_private_chat(self):
        return "Friend" in self.unified_msg_origin

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)


class MessageChain:
    def __init__(self):
        self.text = ""

    def message(self, text=""):
        self.text = text
        return self

    def file_image(self, path):
        return self


class _Filter:
    class EventMessageType:
        ALL = "all"
        PRIVATE_MESSAGE = "private"
        GROUP_MESSAGE = "group"

    class PlatformAdapterType:
        AIOCQHTTP = 1
        ALL = 0

    class PermissionType:
        ADMIN = "admin"

    @staticmethod
    def _noop(*a, **k):
        def deco(fn):
            return fn

        return deco

    command = _noop
    command_group = _noop
    event_message_type = _noop
    platform_adapter_type = _noop
    permission_type = _noop
    on_astrbot_loaded = _noop
    on_llm_request = _noop
    regex = _noop


filter = _Filter()
