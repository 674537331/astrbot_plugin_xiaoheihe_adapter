from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest
from astrbot.api import AstrBotConfig
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
    assert request.extra_user_content_parts[0].temp is True
    assert plugin.runtime._alerts["vision_unsupported"]["level"] == "warning"
    await plugin.capture_xiaoheihe_complete_reply(
        event,
        LLMResponse(completion_text="完整回复，保留标点。\n\n第二段。"),
    )
    assert event.captured["xiaoheihe_complete_reply_text"] == ("完整回复，保留标点。\n\n第二段。")
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
            "get_extra": lambda self, key, default="": default,
            "set_extra": lambda self, key, value: None,
        },
    )()
    request = ProviderRequest()
    request.image_urls = ["https://images.example.test/a.png"]
    await plugin.inject_xiaoheihe_context(event, request)

    assert request.image_urls == ["https://images.example.test/a.png"]
    assert request.extra_user_content_parts == []
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
