from __future__ import annotations

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
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
    "1.1.0",
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

    async def terminate(self) -> None:
        await self.runtime.close()
