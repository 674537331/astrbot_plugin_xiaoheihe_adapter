from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import LLMResponse, ProviderRequest

from tests.astrbot_stubs import REGISTERED_ADAPTERS


@pytest.fixture
def isolated_smoke_import():
    previous = dict(REGISTERED_ADAPTERS)
    yield
    REGISTERED_ADAPTERS.clear()
    REGISTERED_ADAPTERS.update(previous)
    for module_name in tuple(sys.modules):
        if module_name == "xhh_plugin_smoke" or module_name.startswith("xhh_plugin_smoke."):
            sys.modules.pop(module_name, None)


def test_main_registers_no_chat_commands() -> None:
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    command_decorators = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"command", "command_group", "regex"}:
                command_decorators.append(node.func.attr)
    assert command_decorators == []


def test_no_independent_model_endpoint_configuration() -> None:
    forbidden = ("openai_api_key", "openai_base_url", "model_name")
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in Path(".").glob("xiaoheihe/*.py")
    ).lower()
    assert all(item not in source for item in forbidden)


async def test_plugin_main_import_and_explicit_vision_fallback(isolated_smoke_import) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Context:
        def register_web_api(self, *args):
            return None

        def get_using_provider(self, umo=None):
            return type("Provider", (), {"provider_config": {"modalities": ["text"]}})()

    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())
    event = type(
        "Event",
        (),
        {
            "captured": {},
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_post_1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "speaker-1",
            "get_extra": lambda self, key, default="": (
                '<xiaoheihe_context trust="untrusted">背景</xiaoheihe_context>'
                if key == "xiaoheihe_dynamic_context"
                else default
            ),
            "set_extra": lambda self, key, value: self.captured.__setitem__(key, value),
        },
    )()
    request = ProviderRequest()
    request.image_urls = ["https://images.example.test/a.png"]
    await plugin.inject_xiaoheihe_context(event, request)
    assert request.image_urls == []
    assert request.extra_user_content_parts[0].temp is False
    assert 'uid="speaker-1"' in request.extra_user_content_parts[0].text
    assert request.extra_user_content_parts[1].temp is True
    assert plugin.runtime._alerts["vision_unsupported"]["level"] == "warning"
    await plugin.capture_xiaoheihe_complete_reply(
        event,
        LLMResponse(completion_text="完整回复，保留标点。\n\n第二段。"),
    )
    assert event.captured["xiaoheihe_complete_reply_text"] == ("完整回复，保留标点。\n\n第二段。")
    await plugin.terminate()


async def test_xiaoheihe_sender_identity_persists_per_turn_in_shared_floor(
    isolated_smoke_import,
) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Context:
        def register_web_api(self, *args):
            return None

        def get_using_provider(self, umo=None):
            return type("Provider", (), {"provider_config": {"modalities": ["text"]}})()

    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())
    shared_umo = "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1"

    def make_event(uid: str, *, platform: str = "xiaoheihe"):
        return type(
            "Event",
            (),
            {
                "unified_msg_origin": shared_umo,
                "message_obj": type(
                    "Message",
                    (),
                    {"raw_message": {"route": {"profile_id": "default"}}},
                )(),
                "get_platform_name": lambda self: platform,
                "get_sender_id": lambda self: uid,
                "get_extra": lambda self, key, default="": (
                    f'<xiaoheihe_context trust="untrusted">当前发言人 UID {uid}</xiaoheihe_context>'
                    if key == "xiaoheihe_dynamic_context"
                    else default
                ),
            },
        )()

    request_a = ProviderRequest()
    request_b = ProviderRequest()
    await plugin.inject_xiaoheihe_context(make_event("111"), request_a)
    await plugin.inject_xiaoheihe_context(make_event("222"), request_b)

    assert len(request_a.extra_user_content_parts) == 2
    assert len(request_b.extra_user_content_parts) == 2
    assert request_a.extra_user_content_parts[0].temp is False
    assert request_b.extra_user_content_parts[0].temp is False
    assert 'uid="111"' in request_a.extra_user_content_parts[0].text
    assert 'uid="222"' in request_b.extra_user_content_parts[0].text
    assert request_a.extra_user_content_parts[1].temp is True
    assert request_b.extra_user_content_parts[1].temp is True

    non_xiaoheihe = ProviderRequest()
    await plugin.inject_xiaoheihe_context(
        make_event("333", platform="aiocqhttp"),
        non_xiaoheihe,
    )
    assert non_xiaoheihe.extra_user_content_parts == []
    await plugin.terminate()


async def test_plugin_tracks_agent_lifecycle_for_xiaoheihe(isolated_smoke_import) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Context:
        def register_web_api(self, *args):
            return None

    class Event:
        started = False
        done_text = ""

        def get_platform_name(self):
            return "xiaoheihe"

        def mark_agent_started(self):
            self.started = True

        def mark_agent_done(self, final_text):
            self.done_text = final_text

    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())
    event = Event()

    await plugin.mark_xiaoheihe_agent_started(event, object())
    await plugin.mark_xiaoheihe_agent_done(
        event,
        object(),
        LLMResponse(completion_text="最终回复"),
    )

    assert event.started is True
    assert event.done_text == "最终回复"
    await plugin.terminate()


async def test_plugin_isolates_images_only_during_grok_web_search(
    isolated_smoke_import,
) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Context:
        def register_web_api(self, *args):
            return None

    class Event:
        def __init__(self) -> None:
            self.extras = {}
            self.message_obj = type(
                "Message",
                (),
                {
                    "message": [
                        Image(url="https://images.example.test/context.png"),
                        Plain("猎鹰最近一次比赛是什么时候"),
                        Image(url="https://images.example.test/m0nesy.png"),
                    ]
                },
            )()

        def get_platform_name(self):
            return "xiaoheihe"

        def get_messages(self):
            return self.message_obj.message

        def get_extra(self, key, default=None):
            return self.extras.get(key, default)

        def set_extra(self, key, value):
            self.extras[key] = value

    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())
    event = Event()
    grok_tool = type("Tool", (), {"name": "grok_web_search"})()
    original_messages = list(event.get_messages())

    await plugin.isolate_xiaoheihe_images_for_grok(
        event,
        grok_tool,
        (tool_args := {"query": "Falcons CS2 latest match date August 2026"}),
    )
    assert [type(component) for component in event.get_messages()] == [Plain]
    assert module.GROK_QUERY_REQUIREMENT in tool_args["query"]

    await plugin.restore_xiaoheihe_images_after_grok(event, grok_tool, None, None)
    assert event.get_messages() == original_messages
    assert event.get_extra(module.GROK_IMAGE_ISOLATION_EXTRA) is None

    await plugin.isolate_xiaoheihe_images_for_grok(
        event,
        grok_tool,
        {
            "query": "搜索这张图",
            "image_urls": "https://images.example.test/explicit.png",
        },
    )
    assert [type(component) for component in event.get_messages()] == [Plain]
    await plugin.restore_xiaoheihe_images_after_grok(event, grok_tool, None, None)
    assert event.get_messages() == original_messages
    await plugin.terminate()


async def test_plugin_preserves_images_for_explicit_grok_image_search_and_other_tools(
    isolated_smoke_import,
) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Context:
        def register_web_api(self, *args):
            return None

    class Event:
        def __init__(self) -> None:
            self.extras = {}
            self.message_obj = type(
                "Message",
                (),
                {"message": [Plain("帮我搜图"), Image(url="https://images.example.test/a.png")]},
            )()

        def get_platform_name(self):
            return "xiaoheihe"

        def get_messages(self):
            return self.message_obj.message

        def get_extra(self, key, default=None):
            return self.extras.get(key, default)

        def set_extra(self, key, value):
            self.extras[key] = value

    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())
    event = Event()
    grok_tool = type("Tool", (), {"name": "grok_web_search"})()
    other_tool = type("Tool", (), {"name": "other_tool"})()
    original_messages = list(event.get_messages())

    image_query_args = {"query": "搜索这张图的出处"}
    await plugin.isolate_xiaoheihe_images_for_grok(
        event,
        grok_tool,
        image_query_args,
    )
    assert event.get_messages() == original_messages
    assert module.GROK_QUERY_REQUIREMENT in image_query_args["query"]

    other_tool_args = {"query": "普通查询"}
    await plugin.isolate_xiaoheihe_images_for_grok(
        event,
        other_tool,
        other_tool_args,
    )
    assert event.get_messages() == original_messages
    assert other_tool_args == {"query": "普通查询"}
    await plugin.terminate()


async def test_plugin_restores_grok_images_on_agent_done_fallback(
    isolated_smoke_import,
) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Context:
        def register_web_api(self, *args):
            return None

    class Event:
        def __init__(self) -> None:
            self.extras = {}
            self.message_obj = type(
                "Message",
                (),
                {"message": [Plain("查比赛"), Image(url="https://images.example.test/a.png")]},
            )()

        def get_platform_name(self):
            return "xiaoheihe"

        def get_messages(self):
            return self.message_obj.message

        def get_extra(self, key, default=None):
            return self.extras.get(key, default)

        def set_extra(self, key, value):
            self.extras[key] = value

    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())
    event = Event()
    grok_tool = type("Tool", (), {"name": "grok_web_search"})()
    original_messages = list(event.get_messages())

    await plugin.isolate_xiaoheihe_images_for_grok(
        event,
        grok_tool,
        {"query": "Falcons latest match"},
    )
    assert len(event.get_messages()) == 1

    await plugin.restore_xiaoheihe_images_on_agent_done(
        event,
        object(),
        LLMResponse(completion_text="最终结果"),
    )
    assert event.get_messages() == original_messages
    await plugin.terminate()


async def test_plugin_uses_fixed_image_provider_and_keeps_caption_temporary(
    isolated_smoke_import,
) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Provider:
        def __init__(self, modalities, caption="") -> None:
            self.provider_config = {"modalities": modalities}
            self.caption = caption
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(completion_text=self.caption)

    main_provider = Provider(["text"], "")
    image_provider = Provider(["text", "image"], "识别到一张测试图片")

    class Context:
        def register_web_api(self, *args):
            return None

        def get_provider_by_id(self, provider_id):
            return {"main-fixed": main_provider, "image-fixed": image_provider}.get(provider_id)

        def get_using_provider(self, umo=None):
            raise AssertionError("fixed providers should not use the session provider")

    plugin = module.XiaoheiheAdapterPlugin(
        Context(),
        AstrBotConfig(
            {
                "providers": {
                    "llm_provider_id": "main-fixed",
                    "image_provider_id": "image-fixed",
                }
            }
        ),
    )
    event = type(
        "Event",
        (),
        {
            "captured": {},
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_post_1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "speaker-1",
            "get_extra": lambda self, key, default="": default,
            "set_extra": lambda self, key, value: self.captured.__setitem__(key, value),
        },
    )()
    request = ProviderRequest()
    request.image_urls = ["https://images.example.test/a.png"]
    await plugin.inject_xiaoheihe_context(event, request)

    assert request.image_urls == []
    assert len(image_provider.calls) == 1
    assert image_provider.calls[0]["image_urls"] == ["https://images.example.test/a.png"]
    assert request.extra_user_content_parts[-1].temp is True
    assert "识别到一张测试图片" in request.extra_user_content_parts[-1].text
    await plugin.terminate()


async def test_plugin_falls_back_to_main_images_when_fixed_image_provider_fails(
    isolated_smoke_import,
) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class MainProvider:
        def __init__(self) -> None:
            self.provider_config = {"modalities": ["text", "image"]}

    class BrokenImageProvider:
        def __init__(self) -> None:
            self.provider_config = {"modalities": ["text", "image"]}

        async def text_chat(self, **kwargs):
            raise RuntimeError("vision provider unavailable")

    main_provider = MainProvider()
    image_provider = BrokenImageProvider()

    class Context:
        def register_web_api(self, *args):
            return None

        def get_provider_by_id(self, provider_id):
            return {"main-fixed": main_provider, "image-fixed": image_provider}.get(provider_id)

        def get_using_provider(self, umo=None):
            raise AssertionError("fixed providers should not use the session provider")

    plugin = module.XiaoheiheAdapterPlugin(
        Context(),
        AstrBotConfig(
            {
                "providers": {
                    "llm_provider_id": "main-fixed",
                    "image_provider_id": "image-fixed",
                }
            }
        ),
    )
    event = type(
        "Event",
        (),
        {
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_post_1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "speaker-1",
            "get_extra": lambda self, key, default="": default,
            "set_extra": lambda self, key, value: None,
        },
    )()
    request = ProviderRequest()
    request.image_urls = ["https://images.example.test/a.png"]
    await plugin.inject_xiaoheihe_context(event, request)

    assert request.image_urls == ["https://images.example.test/a.png"]
    assert len(request.extra_user_content_parts) == 1
    assert request.extra_user_content_parts[0].temp is False
    assert 'uid="speaker-1"' in request.extra_user_content_parts[0].text
    await plugin.terminate()


async def test_plugin_hot_reload_reconciles_enabled_xiaoheihe_adapter(
    isolated_smoke_import,
) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    enabled = {
        "id": "xiaoheihe-main",
        "type": "xiaoheihe",
        "enable": True,
        "profile_id": "default",
    }
    disabled = {
        "id": "xiaoheihe-disabled",
        "type": "xiaoheihe",
        "enable": False,
        "profile_id": "secondary",
    }

    class PlatformManager:
        def __init__(self) -> None:
            self.platforms_config = [
                enabled,
                disabled,
                {"id": "other", "type": "other", "enable": True},
            ]
            self.reloaded = []

        def get_insts(self):
            return [object()]

        async def reload(self, config):
            self.reloaded.append(config)

    class Context:
        def __init__(self) -> None:
            self.platform_manager = PlatformManager()

        def register_web_api(self, *args):
            return None

    context = Context()
    plugin = module.XiaoheiheAdapterPlugin(context, AstrBotConfig())
    await plugin.initialize()

    assert context.platform_manager.reloaded == [enabled]
    assert plugin.runtime._configured_adapters["xiaoheihe-main"]["enabled"] is True
    assert plugin.runtime._configured_adapters["xiaoheihe-disabled"]["profile_id"] == "secondary"
    await plugin.terminate()


async def test_plugin_cold_start_leaves_platform_initialization_to_astrbot(
    isolated_smoke_import,
) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class PlatformManager:
        def __init__(self) -> None:
            self.platforms_config = [
                {
                    "id": "xiaoheihe",
                    "type": "xiaoheihe",
                    "enable": True,
                    "profile_id": "default",
                },
            ]
            self.reload_count = 0

        def get_insts(self):
            return []

        async def reload(self, config):
            self.reload_count += 1

    class Context:
        def __init__(self) -> None:
            self.platform_manager = PlatformManager()

        def register_web_api(self, *args):
            return None

    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())
    await plugin.initialize()
    assert plugin.context.platform_manager.reload_count == 0
    await plugin.terminate()
