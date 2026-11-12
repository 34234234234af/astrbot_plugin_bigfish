"""测试用替身：astrbot.api.event"""


class MessageChain:
    def __init__(self):
        self.text = ""

    def message(self, text=""):
        self.text = text
        return self

    def file_image(self, path):
        return self


class AstrMessageEvent:
    def __init__(
        self,
        message_str="",
        umo="test:GroupMessage:100",
        sender_id="1",
        name="测试员",
        is_at_or_wake_command=False,
    ):
        self.message_str = message_str
        self.unified_msg_origin = umo
        self._sender_id = sender_id
        self._name = name
        self.is_at_or_wake_command = is_at_or_wake_command
        self.stopped = False
        self.sent = []

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return self._name

    def is_private_chat(self):
        return "Friend" in self.unified_msg_origin

    def stop_event(self):
        self.stopped = True

    async def send(self, payload):
        if isinstance(payload, MessageChain):
            self.sent.append(payload.text)
        elif isinstance(payload, tuple) and len(payload) == 2:
            self.sent.append(payload[1])
        else:
            self.sent.append(str(payload))

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)


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
