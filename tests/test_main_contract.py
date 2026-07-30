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
