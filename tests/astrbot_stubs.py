from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Plain:
    def __init__(self, text: str) -> None:
        self.text = text


class Image:
    def __init__(self, file: str = "", url: str = "") -> None:
        self.file = file
        self.url = url


class MessageChain:
    def __init__(self, chain=None) -> None:
        self.chain = list(chain or [])


class MessageType(StrEnum):
    GROUP_MESSAGE = "GroupMessage"
    FRIEND_MESSAGE = "FriendMessage"


@dataclass
class MessageMember:
    user_id: str
    nickname: str | None = None


@dataclass
class PlatformMetadata:
    name: str
    description: str
    id: str
    default_config_tmpl: dict | None = None
    adapter_display_name: str | None = None
    logo_path: str | None = None
    support_streaming_message: bool = True
    support_proactive_message: bool = True


class AstrBotMessage:
    def __init__(self) -> None:
        self.group_id = ""
        self.timestamp = 0


@dataclass
class MessageSession:
    platform_name: str
    message_type: MessageType
    session_id: str


class AstrMessageEvent:
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
    ) -> None:
        self.message_str = message_str
        self.message_obj = message_obj
        self.platform_meta = platform_meta
        self.session_id = session_id
        self.is_wake = False
        self.is_at_or_wake_command = False
        self.role = "member"
        self._extras: dict[str, Any] = {}
        self.parent_send_count = 0
        self.parent_stream_count = 0

    async def send(self, message: MessageChain) -> None:
        self.parent_send_count += 1

    async def send_streaming(self, generator, use_fallback: bool = False) -> None:
        self.parent_stream_count += 1

    def set_extra(self, key: str, value: Any) -> None:
        self._extras[key] = value

    def get_extra(self, key: str, default=None) -> Any:
        return self._extras.get(key, default)

    def get_platform_name(self) -> str:
        return self.platform_meta.name


class Platform:
    def __init__(self, config: dict, event_queue) -> None:
        self.config = config
        self._event_queue = event_queue
        self.super_send_count = 0

    def commit_event(self, event) -> None:
        self._event_queue.put_nowait(event)

    async def send_by_session(self, session, message_chain) -> None:
        self.super_send_count += 1


REGISTERED_ADAPTERS: dict[str, type] = {}


def register_platform_adapter(name: str, description: str, **kwargs):
    def decorator(cls):
        REGISTERED_ADAPTERS[name] = cls
        cls._test_registration = {
            "name": name,
            "description": description,
            **kwargs,
        }
        return cls

    return decorator


class MutableRequest:
    def __init__(self) -> None:
        self.username: str | None = "admin"
        self.query = Query({})
        self._json: Any = {}

    async def json(self, default=None):
        return self._json if self._json is not None else default


class Query:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get(self, key: str, default=None, type=None):
        value = self.values.get(key, default)
        if type is None:
            return value
        try:
            return type(value)
        except (TypeError, ValueError):
            return default


REQUEST = MutableRequest()


def json_response(payload, status_code: int = 200):
    return {"status_code": status_code, "json": payload}


def error_response(message: str, status_code: int = 400):
    return {"status_code": status_code, "json": {"status": "error", "message": message}}


def stream_response(iterator):
    return {"status_code": 200, "stream": iterator}


def install() -> None:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    components = types.ModuleType("astrbot.api.message_components")
    platform = types.ModuleType("astrbot.api.platform")
    web = types.ModuleType("astrbot.api.web")
    provider = types.ModuleType("astrbot.api.provider")
    star = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    core_platform = types.ModuleType("astrbot.core.platform")
    core_event = types.ModuleType("astrbot.core.platform.astr_message_event")
    core_agent = types.ModuleType("astrbot.core.agent")
    core_agent_message = types.ModuleType("astrbot.core.agent.message")

    event.AstrMessageEvent = AstrMessageEvent
    event.MessageChain = MessageChain

    def passthrough_filter(**kwargs):
        return lambda function: function

    event.filter = types.SimpleNamespace(
        on_llm_request=passthrough_filter,
        on_llm_response=passthrough_filter,
    )
    components.Plain = Plain
    components.Image = Image
    platform.AstrBotMessage = AstrBotMessage
    platform.MessageMember = MessageMember
    platform.MessageType = MessageType
    platform.Platform = Platform
    platform.PlatformMetadata = PlatformMetadata
    platform.register_platform_adapter = register_platform_adapter
    core_event.MessageSession = MessageSession

    web.request = REQUEST
    web.json_response = json_response
    web.error_response = error_response
    web.stream_response = stream_response

    class ProviderRequest:
        def __init__(self) -> None:
            self.extra_user_content_parts = []
            self.image_urls = []

    class LLMResponse:
        def __init__(
            self,
            role: str = "assistant",
            completion_text: str = "",
            *,
            is_chunk: bool = False,
        ) -> None:
            self.role = role
            self.completion_text = completion_text
            self.is_chunk = is_chunk

    class TextPart:
        def __init__(self, text: str) -> None:
            self.text = text
            self.temp = False

        def mark_as_temp(self):
            self.temp = True
            return self

    class AstrBotConfig(dict):
        def save_config(self) -> None:
            self.saved = True

    class Star:
        def __init__(self, context) -> None:
            self.context = context

    class StarTools:
        @classmethod
        def get_data_dir(cls, plugin_name):
            from pathlib import Path

            return Path.cwd() / "work" / "test-data" / plugin_name

    def register(*args, **kwargs):
        return lambda cls: cls

    provider.ProviderRequest = ProviderRequest
    provider.LLMResponse = LLMResponse
    core_agent_message.TextPart = TextPart
    api.AstrBotConfig = AstrBotConfig
    star.Context = object
    star.Star = Star
    star.StarTools = StarTools
    star.register = register

    modules = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.message_components": components,
        "astrbot.api.platform": platform,
        "astrbot.api.web": web,
        "astrbot.api.provider": provider,
        "astrbot.api.star": star,
        "astrbot.core": core,
        "astrbot.core.platform": core_platform,
        "astrbot.core.platform.astr_message_event": core_event,
        "astrbot.core.agent": core_agent,
        "astrbot.core.agent.message": core_agent_message,
    }
    for name, module in modules.items():
        sys.modules.setdefault(name, module)
