from __future__ import annotations

import uuid

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.message import TextPart

from .xiaoheihe.adapter import XiaoheihePlatformAdapter  # noqa: F401
from .xiaoheihe.runtime import PLUGIN_NAME, RuntimeServices, bind_runtime

IMAGE_PROVIDER_PROMPT = (
    "请逐张准确描述这些图片的可见内容，并完整提取与用户问题相关的文字和关键数据。"
    "只提供图片描述，不回答用户问题；多张图片请按顺序区分。"
)
GROK_WEB_SEARCH_TOOL = "grok_web_search"
GROK_IMAGE_ISOLATION_EXTRA = "xiaoheihe_grok_image_isolation"
IMAGE_SEARCH_INTENT_MARKERS = (
    "这张图",
    "这幅图",
    "图中",
    "图里",
    "图片中",
    "图片里",
    "图片出处",
    "图片来源",
    "照片",
    "截图",
    "搜图",
    "识图",
    "以图搜",
    "image",
    "photo",
    "picture",
    "screenshot",
    "meme",
    "reverse image",
)
GROK_QUERY_REQUIREMENT = (
    "[Xiaoheihe search requirement] "
    "请直接围绕原始查询检索并返回能回答问题的实时事实；若检索不到可靠结果，请明确说明。"
    "不要只描述随事件附带的图片，也不要返回‘稍后再查’之类的占位回答。"
)

try:
    from .xiaoheihe.web_api import WebApiController
except ModuleNotFoundError as exc:
    if exc.name != "astrbot.api.web":
        raise
    WebApiController = None  # type: ignore[misc,assignment]


@register(
    PLUGIN_NAME,
    "RyanVaderAn",
    "AstrBot 的小黑盒原生平台适配器",
    "1.2.10",
)
class XiaoheiheAdapterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.runtime = RuntimeServices(config, data_dir)
        bind_runtime(self.runtime)
        self.web = (
            WebApiController(self.runtime, provider_supplier=self._provider_options)
            if WebApiController is not None
            else None
        )
        if self.web is not None:
            self.web.register(context)
        self.runtime.set_configured_adapters(self._platform_configs())

    def _platform_configs(self) -> list[dict]:
        manager = getattr(self.context, "platform_manager", None)
        configs = getattr(manager, "platforms_config", ())
        if not isinstance(configs, (list, tuple)):
            return []
        return [
            config
            for config in configs
            if isinstance(config, dict) and config.get("type") == "xiaoheihe"
        ]

    def _provider_options(self) -> list[dict[str, str]]:
        get_all_providers = getattr(self.context, "get_all_providers", None)
        if not callable(get_all_providers):
            return []
        try:
            providers = get_all_providers()
        except Exception:
            return []
        options: list[dict[str, str]] = []
        for provider in providers if isinstance(providers, (list, tuple)) else ():
            config = getattr(provider, "provider_config", {})
            if not isinstance(config, dict):
                continue
            provider_id = str(config.get("id", "")).strip()
            if not provider_id:
                continue
            model = ""
            get_model = getattr(provider, "get_model", None)
            if callable(get_model):
                try:
                    model = str(get_model() or "").strip()
                except Exception:
                    model = ""
            options.append(
                {
                    "value": provider_id,
                    "label": f"{provider_id} · {model}" if model else provider_id,
                }
            )
        return options

    async def initialize(self) -> None:
        """Restore enabled adapter instances after AstrBot hot-reloads the plugin."""
        configs = self._platform_configs()
        self.runtime.set_configured_adapters(configs)
        if not any(bool(config.get("enable", False)) for config in configs):
            return

        manager = getattr(self.context, "platform_manager", None)
        get_insts = getattr(manager, "get_insts", None)
        reload_platform = getattr(manager, "reload", None)
        if not callable(get_insts) or not callable(reload_platform):
            self.runtime.report_adapter_reconcile_failure(
                "xiaoheihe",
                RuntimeError("当前 AstrBot 平台管理器缺少热重载接口"),
            )
            return

        # During a cold start AstrBot loads plugins before initializing platforms.
        # A non-empty instance list means the platform manager is already active,
        # which is the update/reload case that requires explicit reconciliation.
        if not list(get_insts()):
            return

        for config in configs:
            if not bool(config.get("enable", False)):
                continue
            adapter_id = str(config.get("id", "xiaoheihe"))
            try:
                await reload_platform(config)
            except Exception as exc:
                self.runtime.report_adapter_reconcile_failure(adapter_id, exc)
                continue
            self.runtime.clear_adapter_reconcile_failure(adapter_id)
            self.runtime.logging.emit(
                "INFO",
                "插件更新后已重新加载小黑盒适配器实例",
                profile_id=str(config.get("profile_id", "default")),
                details={"adapter_id": adapter_id},
            )

    @filter.on_llm_request()
    async def inject_xiaoheihe_context(
        self, event: AstrMessageEvent, request: ProviderRequest
    ) -> None:
        if event.get_platform_name() != "xiaoheihe":
            return
        dynamic_context = event.get_extra("xiaoheihe_dynamic_context", "")
        if dynamic_context:
            request.extra_user_content_parts.append(
                TextPart(text=str(dynamic_context)).mark_as_temp()
            )
        config = self.runtime.config.snapshot()
        provider_settings = config["providers"]
        profile_id = str(event.message_obj.raw_message.get("route", {}).get("profile_id", ""))
        image_provider_id = str(provider_settings["image_provider_id"]).strip()
        if request.image_urls and image_provider_id:
            captioned = await self._caption_images(
                request,
                provider_id=image_provider_id,
                profile_id=profile_id,
            )
            if captioned:
                self.runtime.clear_vision_alert()
                return

        llm_provider_id = str(provider_settings["llm_provider_id"]).strip()
        if llm_provider_id:
            provider = self.context.get_provider_by_id(llm_provider_id)
        else:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
        provider_config = getattr(provider, "provider_config", {}) if provider else {}
        modalities = (
            provider_config.get("modalities") if isinstance(provider_config, dict) else None
        )
        if (
            request.image_urls
            and isinstance(modalities, list)
            and modalities
            and "image" not in modalities
        ):
            image_count = len(request.image_urls)
            request.image_urls.clear()
            self.runtime.report_vision_degraded(
                profile_id,
                image_count,
            )
        else:
            self.runtime.clear_vision_alert()

    async def _caption_images(
        self,
        request: ProviderRequest,
        *,
        provider_id: str,
        profile_id: str,
    ) -> bool:
        provider = self.context.get_provider_by_id(provider_id)
        if provider is None:
            self.runtime.logging.emit(
                "WARNING",
                "固定图片 Provider 不存在，回退当前 LLM 图片流程",
                profile_id=profile_id,
                details={"provider_id": provider_id},
            )
            return False
        try:
            response = await provider.text_chat(
                prompt=IMAGE_PROVIDER_PROMPT,
                session_id=f"xiaoheihe-image-{uuid.uuid4().hex}",
                image_urls=list(request.image_urls),
                persist=False,
            )
        except Exception as exc:
            self.runtime.logging.emit(
                "WARNING",
                f"固定图片 Provider 处理失败，回退当前 LLM 图片流程: {exc}",
                profile_id=profile_id,
                details={"provider_id": provider_id},
            )
            return False
        caption = str(getattr(response, "completion_text", "") or "").strip()
        if not caption:
            self.runtime.logging.emit(
                "WARNING",
                "固定图片 Provider 返回空描述，回退当前 LLM 图片流程",
                profile_id=profile_id,
                details={"provider_id": provider_id},
            )
            return False
        request.extra_user_content_parts.append(
            TextPart(
                text=(
                    '<xiaoheihe_image_context trust="untrusted">\n'
                    f"{caption}\n"
                    "</xiaoheihe_image_context>"
                )
            ).mark_as_temp()
        )
        request.image_urls.clear()
        return True

    @staticmethod
    def _grok_query_needs_event_images(tool_args: dict | None) -> bool:
        args = tool_args if isinstance(tool_args, dict) else {}
        explicit_images = str(args.get("image_urls", "") or "").strip()
        if explicit_images:
            return False
        query = str(args.get("query", "") or "").casefold()
        return any(marker.casefold() in query for marker in IMAGE_SEARCH_INTENT_MARKERS)

    @staticmethod
    def _grok_tool_name(tool: object) -> str:
        return str(getattr(tool, "name", "") or "").strip()

    def _restore_grok_event_images(self, event: AstrMessageEvent, *, force: bool) -> int:
        state = event.get_extra(GROK_IMAGE_ISOLATION_EXTRA, None)
        if not isinstance(state, dict):
            return 0
        active_calls = max(1, int(state.get("active_calls", 1)))
        if not force and active_calls > 1:
            state["active_calls"] = active_calls - 1
            return 0

        messages = event.get_messages()
        hidden = state.get("hidden", [])
        restored = 0
        if isinstance(messages, list) and isinstance(hidden, list):
            for item in hidden:
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                index, component = item
                if any(component is current for current in messages):
                    continue
                try:
                    position = max(0, min(int(index), len(messages)))
                except (TypeError, ValueError):
                    position = len(messages)
                messages.insert(position, component)
                restored += 1
        event.set_extra(GROK_IMAGE_ISOLATION_EXTRA, None)
        return restored

    @filter.on_using_llm_tool(priority=-1000)
    async def isolate_xiaoheihe_images_for_grok(
        self,
        event: AstrMessageEvent,
        tool: object,
        tool_args: dict | None,
    ) -> None:
        if event.get_platform_name() != "xiaoheihe":
            return
        if self._grok_tool_name(tool) != GROK_WEB_SEARCH_TOOL:
            return
        needs_event_images = self._grok_query_needs_event_images(tool_args)
        if isinstance(tool_args, dict):
            query = str(tool_args.get("query", "") or "").strip()
            if query and GROK_QUERY_REQUIREMENT not in query:
                tool_args["query"] = f"{query}\n\n{GROK_QUERY_REQUIREMENT}"
        if needs_event_images:
            return

        state = event.get_extra(GROK_IMAGE_ISOLATION_EXTRA, None)
        if isinstance(state, dict):
            state["active_calls"] = max(1, int(state.get("active_calls", 1))) + 1
            return

        messages = event.get_messages()
        if not isinstance(messages, list):
            return
        hidden = [
            (index, component)
            for index, component in enumerate(messages)
            if isinstance(component, Image)
        ]
        if not hidden:
            return
        messages[:] = [component for component in messages if not isinstance(component, Image)]
        event.set_extra(
            GROK_IMAGE_ISOLATION_EXTRA,
            {"hidden": hidden, "active_calls": 1},
        )
        self.runtime.logging.emit(
            "DEBUG",
            "Grok 网页查询期间已临时隔离小黑盒原图",
            details={"image_count": len(hidden)},
        )

    @filter.on_llm_tool_respond(priority=1000)
    async def restore_xiaoheihe_images_after_grok(
        self,
        event: AstrMessageEvent,
        tool: object,
        tool_args: dict | None,
        tool_result: object | None,
    ) -> None:
        if event.get_platform_name() != "xiaoheihe":
            return
        if self._grok_tool_name(tool) != GROK_WEB_SEARCH_TOOL:
            return
        restored = self._restore_grok_event_images(event, force=False)
        if restored:
            self.runtime.logging.emit(
                "DEBUG",
                "Grok 网页查询完成后已恢复小黑盒原图",
                details={"image_count": restored},
            )

    @filter.on_agent_done(priority=1000)
    async def restore_xiaoheihe_images_on_agent_done(
        self,
        event: AstrMessageEvent,
        run_context: object,
        response: LLMResponse,
    ) -> None:
        if event.get_platform_name() != "xiaoheihe":
            return
        restored = self._restore_grok_event_images(event, force=True)
        if restored:
            self.runtime.logging.emit(
                "WARNING",
                "Agent 完成时兜底恢复了 Grok 调用期间隔离的小黑盒原图",
                details={"image_count": restored},
            )

    @filter.on_llm_response(priority=-1000)
    async def capture_xiaoheihe_complete_reply(
        self,
        event: AstrMessageEvent,
        response: LLMResponse,
    ) -> None:
        if event.get_platform_name() != "xiaoheihe":
            return
        if response.role != "assistant" or response.is_chunk:
            return
        text = str(response.completion_text or "").strip()
        if text:
            event.set_extra("xiaoheihe_complete_reply_text", text)

    @filter.on_agent_begin(priority=1000)
    async def mark_xiaoheihe_agent_started(
        self,
        event: AstrMessageEvent,
        run_context: object,
    ) -> None:
        if event.get_platform_name() != "xiaoheihe":
            return
        mark_started = getattr(event, "mark_agent_started", None)
        if callable(mark_started):
            mark_started()

    @filter.on_agent_done(priority=-1000)
    async def mark_xiaoheihe_agent_done(
        self,
        event: AstrMessageEvent,
        run_context: object,
        response: LLMResponse,
    ) -> None:
        if event.get_platform_name() != "xiaoheihe":
            return
        final_text = str(getattr(response, "completion_text", "") or "").strip()
        mark_done = getattr(event, "mark_agent_done", None)
        if callable(mark_done):
            mark_done(final_text)

    async def terminate(self) -> None:
        await self.runtime.close()
