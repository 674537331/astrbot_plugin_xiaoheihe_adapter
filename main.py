from __future__ import annotations

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.message import TextPart

from .xiaoheihe.adapter import XiaoheihePlatformAdapter  # noqa: F401
from .xiaoheihe.runtime import PLUGIN_NAME, RuntimeServices, bind_runtime

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
    "1.1.3",
)
class XiaoheiheAdapterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.runtime = RuntimeServices(config, data_dir)
        bind_runtime(self.runtime)
        self.web = WebApiController(self.runtime) if WebApiController is not None else None
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
                str(event.message_obj.raw_message.get("route", {}).get("profile_id", "")),
                image_count,
            )
        else:
            self.runtime.clear_vision_alert()

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

    async def terminate(self) -> None:
        await self.runtime.close()
