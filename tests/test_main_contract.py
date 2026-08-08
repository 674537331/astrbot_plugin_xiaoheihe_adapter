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
from xiaoheihe.context_compression import ThreadCompressionSource


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


async def test_plugin_semantically_compresses_long_thread_and_keeps_focus_last(
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
        def __init__(self, response="") -> None:
            self.provider_config = {"modalities": ["text"]}
            self.response = response
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(completion_text=self.response)

    compressor = Provider(
        '{"post_summary":"原帖讨论显卡价格",'
        '"thread_summary":"A 和 B 已经转而讨论电影续作",'
        '"local_topic":"电影续作","relation_to_post":"drifted"}'
    )
    main_provider = Provider()

    class Context:
        def register_web_api(self, *args):
            return None

        def get_provider_by_id(self, provider_id):
            return {"main-fixed": main_provider, "compress-fixed": compressor}.get(provider_id)

        def get_using_provider(self, umo=None):
            raise AssertionError("fixed providers should be used")

    plugin = module.XiaoheiheAdapterPlugin(
        Context(),
        AstrBotConfig(
            {
                "providers": {
                    "llm_provider_id": "main-fixed",
                    "context_provider_id": "compress-fixed",
                },
                "context": {"thread_reply_compression_trigger_chars": 500},
            }
        ),
    )
    compression_source = ThreadCompressionSource(
        post_id="post-1",
        post_author="楼主 (UID author)",
        post_title="原帖显卡",
        post_body="显卡正文" * 300,
        recent_comments="A (UID a): 最近聊电影\nB (UID b): 第二部挺好",
        reply_target="B (UID b): 第二部挺好",
        current_sender="C (UID c)",
        current_message="那第一部呢？",
    )
    extras = {
        "xiaoheihe_dynamic_context": "LEGACY-FALLBACK",
        "xiaoheihe_runtime_context": "<runtime>可信时间</runtime>",
        "xiaoheihe_community_context": "FALLBACK-SHOULD-BE-REPLACED",
        "xiaoheihe_focus_context": "<focus>FINAL-FOCUS</focus>",
        "xiaoheihe_compression_source": compression_source,
    }
    event = type(
        "Event",
        (),
        {
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "c",
            "get_extra": lambda self, key, default="": extras.get(key, default),
        },
    )()
    request = ProviderRequest()

    await plugin.inject_xiaoheihe_context(event, request)

    assert len(compressor.calls) == 1
    assert compressor.calls[0]["persist"] is False
    assert "不可信社区数据" in compressor.calls[0]["prompt"]
    assert "那第一部呢？" in compressor.calls[0]["prompt"]
    assert len(request.extra_user_content_parts) == 3
    assert request.extra_user_content_parts[0].temp is False
    compressed = request.extra_user_content_parts[1].text
    assert 'compression="llm"' in compressed
    assert "A 和 B 已经转而讨论电影续作" in compressed
    assert "已明显偏离原帖" in compressed
    assert "FALLBACK-SHOULD-BE-REPLACED" not in compressed
    assert request.extra_user_content_parts[-1].text == "<focus>FINAL-FOCUS</focus>"
    assert request.extra_user_content_parts[-1].temp is True
    await plugin.terminate()


async def test_plugin_context_compression_failure_falls_back_without_blocking(
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

    class BrokenCompressor:
        def __init__(self) -> None:
            self.provider_config = {"modalities": ["text"]}

        async def text_chat(self, **kwargs):
            raise RuntimeError("compressor unavailable")

    class MainProvider:
        def __init__(self) -> None:
            self.provider_config = {"modalities": ["text"]}

    class Context:
        def register_web_api(self, *args):
            return None

        def get_provider_by_id(self, provider_id):
            return {
                "main-fixed": MainProvider(),
                "compress-fixed": BrokenCompressor(),
            }.get(provider_id)

    plugin = module.XiaoheiheAdapterPlugin(
        Context(),
        AstrBotConfig(
            {
                "providers": {
                    "llm_provider_id": "main-fixed",
                    "context_provider_id": "compress-fixed",
                },
                "context": {"thread_reply_compression_trigger_chars": 500},
            }
        ),
    )
    source = ThreadCompressionSource(
        "post-1",
        "楼主",
        "标题",
        "正文" * 300,
        "最近评论",
        "直接回复",
        "用户",
        "当前消息",
    )
    extras = {
        "xiaoheihe_runtime_context": "RUNTIME",
        "xiaoheihe_community_context": "V1.2.12-FALLBACK",
        "xiaoheihe_focus_context": "FINAL-FOCUS",
        "xiaoheihe_compression_source": source,
    }
    event = type(
        "Event",
        (),
        {
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "user",
            "get_extra": lambda self, key, default="": extras.get(key, default),
        },
    )()
    request = ProviderRequest()

    await plugin.inject_xiaoheihe_context(event, request)

    assert "V1.2.12-FALLBACK" in request.extra_user_content_parts[1].text
    assert request.extra_user_content_parts[-1].text == "FINAL-FOCUS"
    await plugin.terminate()


async def test_plugin_short_thread_skips_context_compressor(isolated_smoke_import) -> None:
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

    class Compressor:
        def __init__(self) -> None:
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            raise AssertionError("short thread must not call the compressor")

    compressor = Compressor()

    class Context:
        def register_web_api(self, *args):
            return None

        def get_provider_by_id(self, provider_id):
            return compressor if provider_id == "compress-fixed" else None

    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())
    source = module.ThreadCompressionSource(
        "post-1",
        "楼主",
        "短标题",
        "短正文",
        "一条短评论",
        "直接回复",
        "当前用户",
        "当前消息",
    )
    selected = await plugin._compress_thread_context(
        type("Event", (), {"unified_msg_origin": "xiaoheihe:test"})(),
        source,
        provider_settings={"context_provider_id": "compress-fixed", "llm_provider_id": ""},
        context_settings=plugin.runtime.config.snapshot()["context"],
        profile_id="default",
    )

    assert selected is None
    assert compressor.calls == []
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


async def test_thread_reply_image_provider_compresses_sources_before_final_focus(
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

    class ImageProvider:
        def __init__(self) -> None:
            self.provider_config = {"modalities": ["text", "image"]}
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            value = (
                "当前评论图像信息" * 400
                if "current.png" in kwargs["image_urls"][0]
                else "原帖图像信息" * 400
            )
            return LLMResponse(completion_text=value)

    image_provider = ImageProvider()

    class Context:
        def register_web_api(self, *args):
            return None

        def get_provider_by_id(self, provider_id):
            return image_provider if provider_id == "image-fixed" else None

    plugin = module.XiaoheiheAdapterPlugin(
        Context(),
        AstrBotConfig(
            {
                "providers": {"image_provider_id": "image-fixed"},
                "context": {
                    "enable_thread_reply_compression": False,
                    "thread_reply_compressed_image_chars": 800,
                },
            }
        ),
    )
    compression_source = ThreadCompressionSource(
        "post-1",
        "楼主",
        "标题",
        "正文",
        "楼层",
        "直接回复",
        "当前用户",
        "当前消息",
    )
    extras = {
        "xiaoheihe_runtime_context": "RUNTIME",
        "xiaoheihe_community_context": "COMMUNITY",
        "xiaoheihe_focus_context": "FINAL-FOCUS",
        "xiaoheihe_compression_source": compression_source,
        "xiaoheihe_image_sources": ["current_comment", "original_post"],
    }
    event = type(
        "Event",
        (),
        {
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "user",
            "get_extra": lambda self, key, default="": extras.get(key, default),
        },
    )()
    request = ProviderRequest()
    request.image_urls = [
        "https://images.example.test/current.png",
        "https://images.example.test/post.png",
    ]

    await plugin.inject_xiaoheihe_context(event, request)

    assert request.image_urls == []
    assert len(image_provider.calls) == 2
    assert image_provider.calls[0]["image_urls"] == ["https://images.example.test/current.png"]
    assert image_provider.calls[1]["image_urls"] == ["https://images.example.test/post.png"]
    current_block = request.extra_user_content_parts[2].text
    post_block = request.extra_user_content_parts[3].text
    assert 'source="current_comment" priority="highest"' in current_block
    assert len(current_block.splitlines()[2]) == 1600
    assert 'source="original_post" priority="low"' in post_block
    assert len(post_block.splitlines()[2]) == 800
    assert request.extra_user_content_parts[-1].text == "FINAL-FOCUS"
    await plugin.terminate()


async def test_thread_reply_without_fixed_image_provider_preprocesses_with_main_provider(
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
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(completion_text="原帖视觉摘要" * 400)

    main_provider = MainProvider()

    class Context:
        def register_web_api(self, *args):
            return None

        def get_provider_by_id(self, provider_id):
            return main_provider if provider_id == "main-fixed" else None

        def get_using_provider(self, umo=None):
            raise AssertionError("fixed main provider should satisfy image preprocessing")

    plugin = module.XiaoheiheAdapterPlugin(
        Context(),
        AstrBotConfig(
            {
                "providers": {"llm_provider_id": "main-fixed"},
                "context": {
                    "enable_thread_reply_compression": False,
                    "thread_reply_compressed_image_chars": 800,
                },
            }
        ),
    )
    extras = {
        "xiaoheihe_runtime_context": "RUNTIME",
        "xiaoheihe_community_context": "COMMUNITY",
        "xiaoheihe_focus_context": "FINAL-FOCUS",
        "xiaoheihe_compression_source": ThreadCompressionSource(
            "post-1",
            "楼主",
            "标题",
            "正文",
            "楼层",
            "直接回复",
            "当前用户",
            "当前消息",
        ),
        "xiaoheihe_image_sources": ["original_post"],
    }
    event = type(
        "Event",
        (),
        {
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "user",
            "get_extra": lambda self, key, default="": extras.get(key, default),
            "set_extra": lambda self, key, value: extras.__setitem__(key, value),
        },
    )()
    request = ProviderRequest()
    request.image_urls = ["https://images.example.test/post.png"]

    await plugin.inject_xiaoheihe_context(event, request)

    assert request.image_urls == []
    assert extras["xiaoheihe_image_sources"] == []
    assert len(main_provider.calls) == 1
    assert main_provider.calls[0]["image_urls"] == ["https://images.example.test/post.png"]
    assert main_provider.calls[0]["persist"] is False
    post_block = next(
        part.text
        for part in request.extra_user_content_parts
        if 'source="original_post" priority="low"' in part.text
    )
    assert len(post_block.splitlines()[2]) == 800
    assert request.extra_user_content_parts[-1].text == "FINAL-FOCUS"
    await plugin.terminate()


async def test_thread_reply_fixed_image_failure_falls_through_to_main_preprocessor(
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

    class BrokenImageProvider:
        def __init__(self) -> None:
            self.provider_config = {"modalities": ["text", "image"]}
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            raise RuntimeError("image provider unavailable")

    class MainProvider:
        def __init__(self) -> None:
            self.provider_config = {"modalities": ["text", "image"]}
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(completion_text="主模型预处理成功")

    image_provider = BrokenImageProvider()
    main_provider = MainProvider()

    class Context:
        def register_web_api(self, *args):
            return None

        def get_provider_by_id(self, provider_id):
            return {
                "image-fixed": image_provider,
                "main-fixed": main_provider,
            }.get(provider_id)

        def get_using_provider(self, umo=None):
            raise AssertionError("fixed main provider should satisfy fallback preprocessing")

    plugin = module.XiaoheiheAdapterPlugin(
        Context(),
        AstrBotConfig(
            {
                "providers": {
                    "image_provider_id": "image-fixed",
                    "llm_provider_id": "main-fixed",
                },
                "context": {"enable_thread_reply_compression": False},
            }
        ),
    )
    extras = {
        "xiaoheihe_compression_source": ThreadCompressionSource(
            "post-1",
            "楼主",
            "标题",
            "正文",
            "楼层",
            "直接回复",
            "当前用户",
            "当前消息",
        ),
        "xiaoheihe_image_sources": ["original_post"],
    }
    event = type(
        "Event",
        (),
        {
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "user",
            "get_extra": lambda self, key, default="": extras.get(key, default),
            "set_extra": lambda self, key, value: extras.__setitem__(key, value),
        },
    )()
    request = ProviderRequest()
    request.image_urls = ["https://images.example.test/post.png"]

    await plugin.inject_xiaoheihe_context(event, request)

    assert request.image_urls == []
    assert len(image_provider.calls) == 1
    assert len(main_provider.calls) == 1
    assert any("主模型预处理成功" in part.text for part in request.extra_user_content_parts)
    await plugin.terminate()


async def test_thread_reply_image_preprocess_fail_closed_for_post_but_keeps_current_raw(
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

    class BrokenMainProvider:
        def __init__(self) -> None:
            self.provider_config = {"modalities": ["text", "image"]}
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            raise RuntimeError("preprocessing failed")

    main_provider = BrokenMainProvider()

    class Context:
        def register_web_api(self, *args):
            return None

        def get_provider_by_id(self, provider_id):
            return main_provider if provider_id == "main-fixed" else None

        def get_using_provider(self, umo=None):
            return main_provider

    plugin = module.XiaoheiheAdapterPlugin(
        Context(),
        AstrBotConfig(
            {
                "providers": {"llm_provider_id": "main-fixed"},
                "context": {"enable_thread_reply_compression": False},
            }
        ),
    )
    extras = {
        "xiaoheihe_runtime_context": "RUNTIME",
        "xiaoheihe_community_context": "COMMUNITY",
        "xiaoheihe_focus_context": "FINAL-FOCUS",
        "xiaoheihe_compression_source": ThreadCompressionSource(
            "post-1",
            "楼主",
            "标题",
            "正文",
            "楼层",
            "直接回复",
            "当前用户",
            "当前消息",
        ),
        "xiaoheihe_image_sources": ["current_comment", "original_post"],
    }
    event = type(
        "Event",
        (),
        {
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "user",
            "get_extra": lambda self, key, default="": extras.get(key, default),
            "set_extra": lambda self, key, value: extras.__setitem__(key, value),
        },
    )()
    request = ProviderRequest()
    request.image_urls = [
        "https://images.example.test/current.png",
        "https://images.example.test/post.png",
    ]

    await plugin.inject_xiaoheihe_context(event, request)

    assert request.image_urls == ["https://images.example.test/current.png"]
    assert extras["xiaoheihe_image_sources"] == ["current_comment"]
    assert len(main_provider.calls) == 2
    assert any(
        "原帖图片 1 张的视觉预处理失败，原图已从最终回答模型输入中移除" in part.text
        for part in request.extra_user_content_parts
    )
    assert any(
        "当前评论图片；与当前消息同为最高优先级" in part.text
        for part in request.extra_user_content_parts
    )
    assert request.extra_user_content_parts[-1].text == "FINAL-FOCUS"
    await plugin.terminate()


async def test_thread_reply_text_only_provider_never_receives_raw_post_image(
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

    provider = type("Provider", (), {"provider_config": {"modalities": ["text"]}})()

    class Context:
        def register_web_api(self, *args):
            return None

        def get_using_provider(self, umo=None):
            return provider

    plugin = module.XiaoheiheAdapterPlugin(
        Context(),
        AstrBotConfig({"context": {"enable_thread_reply_compression": False}}),
    )
    extras = {
        "xiaoheihe_compression_source": ThreadCompressionSource(
            "post-1",
            "楼主",
            "标题",
            "正文",
            "楼层",
            "直接回复",
            "当前用户",
            "当前消息",
        ),
        "xiaoheihe_image_sources": ["original_post"],
    }
    event = type(
        "Event",
        (),
        {
            "unified_msg_origin": "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1",
            "message_obj": type(
                "Message",
                (),
                {"raw_message": {"route": {"profile_id": "default"}}},
            )(),
            "get_platform_name": lambda self: "xiaoheihe",
            "get_sender_id": lambda self: "user",
            "get_extra": lambda self, key, default="": extras.get(key, default),
            "set_extra": lambda self, key, value: extras.__setitem__(key, value),
        },
    )()
    request = ProviderRequest()
    request.image_urls = ["https://images.example.test/post.png"]

    await plugin.inject_xiaoheihe_context(event, request)

    assert request.image_urls == []
    assert extras["xiaoheihe_image_sources"] == []
    assert any(
        "原图已从最终回答模型输入中移除" in part.text for part in request.extra_user_content_parts
    )
    await plugin.terminate()


def test_raw_thread_images_keep_source_priority_without_fixed_image_provider(
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
    source = ThreadCompressionSource(
        "post-1",
        "楼主",
        "标题",
        "正文",
        "楼层",
        "直接回复",
        "当前用户",
        "当前消息",
    )
    extras = {
        "xiaoheihe_compression_source": source,
        "xiaoheihe_image_sources": ["current_comment", "original_post"],
    }
    event = type(
        "Event",
        (),
        {"get_extra": lambda self, key, default="": extras.get(key, default)},
    )()
    request = ProviderRequest()
    request.image_urls = [
        "https://images.example.test/current.png",
        "https://images.example.test/post.png",
    ]

    rendered = module.XiaoheiheAdapterPlugin._render_image_source_map(event, request)

    assert "当前评论图片；与当前消息同为最高优先级" in rendered
    assert "原帖图片；低优先级背景，不得单独决定当前话题" in rendered
    assert request.image_urls == [
        "https://images.example.test/current.png",
        "https://images.example.test/post.png",
    ]


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
