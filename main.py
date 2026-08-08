from __future__ import annotations

import asyncio
import uuid

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.message import TextPart

from .xiaoheihe.adapter import XiaoheihePlatformAdapter  # noqa: F401
from .xiaoheihe.context_compression import (
    ThreadCompressionSource,
    build_image_compression_prompt,
    build_thread_compression_prompt,
    parse_thread_compression,
    render_compressed_thread_context,
    render_image_context,
)
from .xiaoheihe.runtime import PLUGIN_NAME, RuntimeServices, bind_runtime
from .xiaoheihe.security import clean_untrusted_text

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
SENDER_IDENTITY_TAG = "xiaoheihe_sender_identity"
MAX_IMAGE_PREPROCESS_BUDGET_SECONDS = 60.0
MIN_IMAGE_REPLY_GRACE_SECONDS = 15.0

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
    "1.2.13",
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
        sender_uid = str(event.get_sender_id() or "").strip()
        if sender_uid:
            # Keep the floor as one shared AstrBot conversation while persisting
            # the real author of every user turn.  Unlike the large dynamic
            # thread context below this part is intentionally NOT temporary, so
            # later turns cannot collapse different users into one anonymous
            # ``role=user`` history stream.
            request.extra_user_content_parts.append(
                TextPart(
                    text=(
                        f'<{SENDER_IDENTITY_TAG} uid="{sender_uid}">\n'
                        "This UID is the author of this user turn in a shared "
                        "Xiaoheihe thread. First-person references in this turn "
                        "belong only to this UID.\n"
                        f"</{SENDER_IDENTITY_TAG}>"
                    )
                )
            )
        config = self.runtime.config.snapshot()
        provider_settings = config["providers"]
        context_settings = config["context"]
        profile_id = str(event.message_obj.raw_message.get("route", {}).get("profile_id", ""))

        dynamic_context = str(event.get_extra("xiaoheihe_dynamic_context", "") or "")
        runtime_context = str(event.get_extra("xiaoheihe_runtime_context", "") or "")
        community_context = str(event.get_extra("xiaoheihe_community_context", "") or "")
        focus_context = str(event.get_extra("xiaoheihe_focus_context", "") or "")
        compression_source = self._coerce_compression_source(
            event.get_extra("xiaoheihe_compression_source", None)
        )
        has_split_context = bool(runtime_context or community_context or focus_context)
        if has_split_context:
            selected_community = community_context
            if compression_source is not None:
                compressed = await self._compress_thread_context(
                    event,
                    compression_source,
                    provider_settings=provider_settings,
                    context_settings=context_settings,
                    profile_id=profile_id,
                )
                if compressed:
                    selected_community = compressed
            dynamic_context = "\n".join(
                part for part in (runtime_context, selected_community) if part
            )
        if dynamic_context:
            request.extra_user_content_parts.append(TextPart(text=dynamic_context).mark_as_temp())

        is_thread_reply = compression_source is not None
        image_provider_id = str(provider_settings["image_provider_id"]).strip()
        if request.image_urls and is_thread_reply:
            await self._preprocess_thread_images(
                event,
                request,
                provider_settings=provider_settings,
                profile_id=profile_id,
                context_settings=context_settings,
            )
        elif request.image_urls and image_provider_id:
            await self._caption_images(
                event,
                request,
                provider_id=image_provider_id,
                profile_id=profile_id,
                context_settings=context_settings,
            )

        if request.image_urls:
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
            image_source_map = self._render_image_source_map(event, request)
            if image_source_map:
                request.extra_user_content_parts.append(
                    TextPart(text=image_source_map).mark_as_temp()
                )
        else:
            self.runtime.clear_vision_alert()

        if has_split_context and focus_context:
            # Keep the trusted routing rule after both text compression and image
            # descriptions so low-capability chat models see the priority rule last.
            request.extra_user_content_parts.append(TextPart(text=focus_context).mark_as_temp())

    async def _compress_thread_context(
        self,
        event: AstrMessageEvent,
        source: ThreadCompressionSource,
        *,
        provider_settings: dict,
        context_settings: dict,
        profile_id: str,
    ) -> str | None:
        if not bool(context_settings.get("enable_thread_reply_compression", True)):
            return None
        trigger_chars = int(context_settings["thread_reply_compression_trigger_chars"])
        if source.compressible_chars <= trigger_chars:
            return None

        configured_provider_id = str(provider_settings.get("context_provider_id", "")).strip()
        fallback_provider_id = str(provider_settings.get("llm_provider_id", "")).strip()
        provider_id = configured_provider_id or fallback_provider_id
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
        else:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
        if provider is None:
            self.runtime.logging.emit(
                "WARNING",
                "楼层上下文压缩 Provider 不存在，回退 v1.2.12 硬截断上下文",
                profile_id=profile_id,
                details={"provider_id": provider_id or "current-session"},
            )
            return None

        post_chars = int(context_settings["thread_reply_compressed_post_chars"])
        comments_chars = int(context_settings["thread_reply_compressed_comments_chars"])
        prompt = build_thread_compression_prompt(
            source,
            post_chars=post_chars,
            comments_chars=comments_chars,
        )
        timeout = int(context_settings["thread_reply_compression_timeout_seconds"])
        try:
            response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    session_id=f"xiaoheihe-context-{uuid.uuid4().hex}",
                    persist=False,
                ),
                timeout=timeout,
            )
            result = parse_thread_compression(
                str(getattr(response, "completion_text", "") or ""),
                post_chars=post_chars,
                comments_chars=comments_chars,
            )
        except Exception as exc:
            self.runtime.logging.emit(
                "WARNING",
                f"楼层上下文 LLM 压缩失败，回退 v1.2.12 硬截断上下文: {exc}",
                profile_id=profile_id,
                details={"provider_id": provider_id or "current-session"},
            )
            return None
        rendered = render_compressed_thread_context(source, result)
        self.runtime.logging.emit(
            "DEBUG",
            "楼层上下文已按来源完成 LLM 语义压缩",
            profile_id=profile_id,
            details={
                "provider_id": provider_id or "current-session",
                "input_chars": source.compressible_chars,
                "output_chars": len(rendered),
                "relation_to_post": result.relation_to_post,
            },
        )
        return rendered

    async def _preprocess_thread_images(
        self,
        event: AstrMessageEvent,
        request: ProviderRequest,
        *,
        provider_settings: dict,
        profile_id: str,
        context_settings: dict,
    ) -> None:
        sources = self._normalized_image_sources(event, request)
        grouped: dict[str, list[str]] = {
            "current_comment": [],
            "original_post": [],
            "event_image": [],
        }
        for url, source in zip(request.image_urls, sources, strict=True):
            grouped[source].append(url)

        budget_seconds = self._image_preprocess_budget_seconds(
            event,
            context_settings=context_settings,
            image_count=len(request.image_urls),
        )
        deadline = asyncio.get_running_loop().time() + budget_seconds
        remaining_urls: list[str] = []
        remaining_sources: list[str] = []
        for source in ("current_comment", "original_post", "event_image"):
            urls = grouped[source]
            if not urls:
                continue
            rendered = await self._caption_thread_image_group(
                event,
                source=source,
                urls=urls,
                provider_settings=provider_settings,
                profile_id=profile_id,
                context_settings=context_settings,
                deadline=deadline,
            )
            if rendered:
                request.extra_user_content_parts.append(TextPart(text=rendered).mark_as_temp())
                continue

            if source == "current_comment":
                # The user's own image is part of the highest-priority current
                # message.  Preserve it only as the last-resort AstrBot native
                # vision fallback; low-priority post images never get this path.
                remaining_urls.extend(urls)
                remaining_sources.extend([source] * len(urls))
                self.runtime.logging.emit(
                    "WARNING",
                    "当前评论图片预处理失败，保留原图作为最终视觉兜底",
                    profile_id=profile_id,
                    details={"image_count": len(urls)},
                )
                continue

            request.extra_user_content_parts.append(
                TextPart(
                    text=self._render_blocked_thread_image_notice(
                        source=source,
                        image_count=len(urls),
                    )
                ).mark_as_temp()
            )
            self.runtime.logging.emit(
                "WARNING",
                "低优先级楼层图片预处理失败，已阻止原图进入最终 LLM",
                profile_id=profile_id,
                details={"source": source, "image_count": len(urls)},
            )

        request.image_urls[:] = remaining_urls
        set_extra = getattr(event, "set_extra", None)
        if callable(set_extra):
            set_extra("xiaoheihe_image_sources", remaining_sources)

    async def _caption_thread_image_group(
        self,
        event: AstrMessageEvent,
        *,
        source: str,
        urls: list[str],
        provider_settings: dict,
        profile_id: str,
        context_settings: dict,
        deadline: float,
    ) -> str | None:
        compressed_image_chars = int(context_settings["thread_reply_compressed_image_chars"])
        if source == "original_post":
            max_chars = compressed_image_chars
            priority = "low"
        elif source == "current_comment":
            max_chars = max(1600, compressed_image_chars)
            priority = "highest"
        else:
            # Unknown provenance is deliberately treated as low-priority in a
            # passive floor reply: if it cannot be summarized, it is fail-closed.
            max_chars = compressed_image_chars
            priority = "low"

        tried_providers: set[int] = set()
        provider_ids = []
        for key in ("image_provider_id", "llm_provider_id"):
            provider_id = str(provider_settings.get(key, "") or "").strip()
            if provider_id and provider_id not in provider_ids:
                provider_ids.append(provider_id)
        for provider_id in provider_ids:
            provider = self.context.get_provider_by_id(provider_id)
            if provider is None or id(provider) in tried_providers:
                continue
            tried_providers.add(id(provider))
            rendered = await self._try_caption_thread_image_group(
                provider,
                provider_label=provider_id,
                source=source,
                urls=urls,
                max_chars=max_chars,
                priority=priority,
                profile_id=profile_id,
                deadline=deadline,
            )
            if rendered:
                return rendered

        get_using_provider = getattr(self.context, "get_using_provider", None)
        if callable(get_using_provider):
            try:
                provider = get_using_provider(umo=event.unified_msg_origin)
            except Exception as exc:
                self.runtime.logging.emit(
                    "WARNING",
                    f"无法获取当前会话图片预处理 Provider: {exc}",
                    profile_id=profile_id,
                    details={"source": source},
                )
            else:
                if provider is not None and id(provider) not in tried_providers:
                    rendered = await self._try_caption_thread_image_group(
                        provider,
                        provider_label="current-session",
                        source=source,
                        urls=urls,
                        max_chars=max_chars,
                        priority=priority,
                        profile_id=profile_id,
                        deadline=deadline,
                    )
                    if rendered:
                        return rendered
        return None

    async def _try_caption_thread_image_group(
        self,
        provider: object,
        *,
        provider_label: str,
        source: str,
        urls: list[str],
        max_chars: int,
        priority: str,
        profile_id: str,
        deadline: float,
    ) -> str | None:
        if not self._provider_may_accept_images(provider):
            self.runtime.logging.emit(
                "DEBUG",
                "图片预处理 Provider 明确不支持图片，尝试下一 Provider",
                profile_id=profile_id,
                details={"provider_id": provider_label, "source": source},
            )
            return None
        text_chat = getattr(provider, "text_chat", None)
        if not callable(text_chat):
            return None
        remaining_seconds = deadline - asyncio.get_running_loop().time()
        if remaining_seconds <= 0:
            self.runtime.logging.emit(
                "WARNING",
                "图片预处理总时间预算已耗尽，停止尝试其他 Provider",
                profile_id=profile_id,
                details={"provider_id": provider_label, "source": source},
            )
            return None
        try:
            response = await asyncio.wait_for(
                text_chat(
                    prompt=build_image_compression_prompt(
                        source=source,
                        max_chars=max_chars,
                    ),
                    session_id=f"xiaoheihe-image-{uuid.uuid4().hex}",
                    image_urls=urls,
                    persist=False,
                    request_max_retries=1,
                ),
                timeout=remaining_seconds,
            )
            caption = clean_untrusted_text(
                str(getattr(response, "completion_text", "") or ""),
                max_chars=max_chars,
            )
            if not caption:
                raise ValueError("图片预处理 Provider 返回空描述")
        except TimeoutError:
            self.runtime.logging.emit(
                "WARNING",
                "图片预处理达到本轮总时间预算，立即进入降级路径",
                profile_id=profile_id,
                details={
                    "provider_id": provider_label,
                    "source": source,
                    "timeout_seconds": round(max(0.0, remaining_seconds), 3),
                },
            )
            return None
        except Exception as exc:
            self.runtime.logging.emit(
                "WARNING",
                f"图片预处理 Provider 处理失败，尝试下一 Provider: {exc}",
                profile_id=profile_id,
                details={"provider_id": provider_label, "source": source},
            )
            return None
        return render_image_context(
            source=source,
            caption=caption,
            priority=priority,
        )

    @staticmethod
    def _provider_may_accept_images(provider: object) -> bool:
        provider_config = getattr(provider, "provider_config", {})
        modalities = (
            provider_config.get("modalities") if isinstance(provider_config, dict) else None
        )
        if not isinstance(modalities, list) or not modalities:
            return True
        return "image" in {str(modality).strip().casefold() for modality in modalities}

    @staticmethod
    def _image_preprocess_budget_seconds(
        event: AstrMessageEvent,
        *,
        context_settings: dict,
        image_count: int,
    ) -> float:
        """Bound image-caption calls to the extra image grace, capped per event."""
        count = max(0, int(image_count))
        if count == 0:
            return 0.0

        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", {})
        if isinstance(raw_message, dict):
            try:
                base_timeout = float(raw_message.get("reply_timeout_base_seconds", 0) or 0)
                effective_timeout = float(
                    raw_message.get("reply_timeout_effective_seconds", 0) or 0
                )
            except (TypeError, ValueError):
                base_timeout = 0.0
                effective_timeout = 0.0
            grace_seconds = effective_timeout - base_timeout
            if base_timeout > 0 and grace_seconds > 0:
                return min(MAX_IMAGE_PREPROCESS_BUDGET_SECONDS, grace_seconds)

        image_timeout = max(1.0, float(context_settings.get("image_timeout_seconds", 15)))
        per_image_grace = max(MIN_IMAGE_REPLY_GRACE_SECONDS, image_timeout * 2)
        return min(
            MAX_IMAGE_PREPROCESS_BUDGET_SECONDS,
            per_image_grace * count,
        )

    @staticmethod
    def _render_blocked_thread_image_notice(*, source: str, image_count: int) -> str:
        label = "原帖图片" if source == "original_post" else "来源无法确认的楼层图片"
        return "\n".join(
            [
                (
                    '<xiaoheihe_image_preprocess trust="trusted" '
                    f'source="{source}" status="unavailable">'
                ),
                f"{label} {image_count} 张的视觉预处理失败，原图已从最终回答模型输入中移除。",
                "不得根据这些图片的存在推断当前话题，也不得编造其内容。",
                "</xiaoheihe_image_preprocess>",
            ]
        )

    @staticmethod
    def _normalized_image_sources(event: AstrMessageEvent, request: ProviderRequest) -> list[str]:
        sources = event.get_extra("xiaoheihe_image_sources", [])
        if not isinstance(sources, list) or len(sources) != len(request.image_urls):
            return ["event_image"] * len(request.image_urls)
        return [
            source if source in {"current_comment", "original_post"} else "event_image"
            for source in sources
        ]

    async def _caption_images(
        self,
        event: AstrMessageEvent,
        request: ProviderRequest,
        *,
        provider_id: str,
        profile_id: str,
        context_settings: dict,
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
        sources = self._normalized_image_sources(event, request)
        is_thread_reply = (
            self._coerce_compression_source(event.get_extra("xiaoheihe_compression_source", None))
            is not None
        )
        groups: list[tuple[str, list[str]]] = []
        for source in ("current_comment", "original_post", "event_image"):
            urls = [
                url
                for url, image_source in zip(request.image_urls, sources, strict=True)
                if image_source == source
            ]
            if urls:
                groups.append((source, urls))

        rendered: list[str] = []
        compressed_image_chars = int(context_settings["thread_reply_compressed_image_chars"])
        budget_seconds = self._image_preprocess_budget_seconds(
            event,
            context_settings=context_settings,
            image_count=len(request.image_urls),
        )
        deadline = asyncio.get_running_loop().time() + budget_seconds
        try:
            for source, urls in groups:
                if is_thread_reply and source == "original_post":
                    max_chars = compressed_image_chars
                    priority = "low"
                elif is_thread_reply and source == "current_comment":
                    max_chars = max(1600, compressed_image_chars)
                    priority = "highest"
                else:
                    max_chars = max(2400, compressed_image_chars)
                    priority = "primary"
                remaining_seconds = deadline - asyncio.get_running_loop().time()
                if remaining_seconds <= 0:
                    raise TimeoutError("图片预处理总时间预算已耗尽")
                response = await asyncio.wait_for(
                    provider.text_chat(
                        prompt=build_image_compression_prompt(
                            source=source,
                            max_chars=max_chars,
                        ),
                        session_id=f"xiaoheihe-image-{uuid.uuid4().hex}",
                        image_urls=urls,
                        persist=False,
                        request_max_retries=1,
                    ),
                    timeout=remaining_seconds,
                )
                caption = clean_untrusted_text(
                    str(getattr(response, "completion_text", "") or ""),
                    max_chars=max_chars,
                )
                if not caption:
                    raise ValueError("固定图片 Provider 返回空描述")
                rendered.append(
                    render_image_context(
                        source=source,
                        caption=caption,
                        priority=priority,
                    )
                )
        except Exception as exc:
            self.runtime.logging.emit(
                "WARNING",
                f"固定图片 Provider 处理失败，回退当前 LLM 图片流程: {exc}",
                profile_id=profile_id,
                details={"provider_id": provider_id},
            )
            return False
        for block in rendered:
            request.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
        request.image_urls.clear()
        return True

    @staticmethod
    def _render_image_source_map(event: AstrMessageEvent, request: ProviderRequest) -> str:
        if not request.image_urls:
            return ""
        sources = event.get_extra("xiaoheihe_image_sources", [])
        if not isinstance(sources, list) or len(sources) != len(request.image_urls):
            return ""
        is_thread_reply = (
            XiaoheiheAdapterPlugin._coerce_compression_source(
                event.get_extra("xiaoheihe_compression_source", None)
            )
            is not None
        )
        lines = [
            '<xiaoheihe_image_source_map trust="trusted">',
            "以下仅标记图片来源和相关性，不包含社区内容:",
        ]
        for index, source in enumerate(sources, start=1):
            if source == "current_comment":
                label = "当前评论图片；与当前消息同为最高优先级"
            elif source == "original_post" and is_thread_reply:
                label = "原帖图片；低优先级背景，不得单独决定当前话题"
            elif source == "original_post":
                label = "原帖图片；当前事件的主要背景"
            else:
                label = "事件图片；按当前消息语义判断是否需要"
            lines.append(f"图片 {index}: {label}")
        lines.append("</xiaoheihe_image_source_map>")
        return "\n".join(lines)

    @staticmethod
    def _coerce_compression_source(value: object) -> ThreadCompressionSource | None:
        if value is None:
            return None
        fields = (
            "post_id",
            "post_author",
            "post_title",
            "post_body",
            "recent_comments",
            "reply_target",
            "current_sender",
            "current_message",
        )
        try:
            values = {field: str(getattr(value, field)) for field in fields}
        except (AttributeError, TypeError, ValueError):
            return None
        return ThreadCompressionSource(**values)

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
