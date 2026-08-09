from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

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
    is_unusable_image_caption,
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
MAX_IMAGE_PREPROCESS_BUDGET_SECONDS = 120.0
MIN_IMAGE_REPLY_GRACE_SECONDS = 15.0
IMAGE_CAPTION_CACHE_TTL_SECONDS = 24 * 60 * 60
IMAGE_CAPTION_CACHE_MAX_ENTRIES = 512


@dataclass(frozen=True, slots=True)
class ImageCaptionResult:
    caption: str
    rendered: str
    provider_label: str
    model: str
    cache_hit: str = ""
    visual_context_id: int | None = None


@dataclass(frozen=True, slots=True)
class ImageCaptionCacheEntry:
    expires_at: float
    caption: str
    provider_label: str
    model: str
    visual_context_id: int | None = None


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
    "1.2.15",
)
class XiaoheiheAdapterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.runtime = RuntimeServices(config, data_dir)
        self._image_caption_cache: OrderedDict[str, ImageCaptionCacheEntry] = OrderedDict()
        self._image_caption_cache_lock = asyncio.Lock()
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
        if compression_source is not None:
            cached_visual = await self._prepare_visual_context_for_reply(
                event,
                request,
                profile_id=profile_id,
                post_id=compression_source.post_id,
            )
            if cached_visual is not None:
                compression_source = replace(
                    compression_source,
                    post_image_caption=cached_visual.caption,
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
        elif request.image_urls and bool(self._event_raw_message(event).get("proactive", False)):
            await self._caption_proactive_images(
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

        self._append_cached_visual_context_if_needed(
            event,
            request,
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
        image_chars = int(context_settings["thread_reply_compressed_image_chars"])
        image_requires_compression = len(source.post_image_caption) > image_chars
        if source.compressible_chars <= trigger_chars and not image_requires_compression:
            return None

        post_chars = int(context_settings["thread_reply_compressed_post_chars"])
        comments_chars = int(context_settings["thread_reply_compressed_comments_chars"])
        prompt = build_thread_compression_prompt(
            source,
            post_chars=post_chars,
            comments_chars=comments_chars,
            image_chars=image_chars,
        )
        timeout = int(context_settings["thread_reply_compression_timeout_seconds"])
        deadline = asyncio.get_running_loop().time() + timeout
        providers = self._auxiliary_provider_candidates(
            event,
            provider_settings,
            keys=("context_provider_id", "llm_provider_id"),
            purpose="context",
            profile_id=profile_id,
        )
        for provider, provider_label in providers:
            if not self.runtime.auxiliary_provider_available(
                profile_id,
                "context",
                provider_label,
            ):
                continue
            remaining_seconds = deadline - asyncio.get_running_loop().time()
            if remaining_seconds <= 0:
                break
            attempt_started = time.monotonic()
            attempt_details = {
                **self._event_image_diagnostics(event),
                "stage": "thread_context_compression",
                "model": self._provider_model(provider),
                "input_chars": source.compressible_chars,
                "cached_image_input_chars": len(source.post_image_caption),
                "configured_timeout_seconds": timeout,
                "attempt_timeout_seconds": round(remaining_seconds, 3),
            }
            try:
                response = await asyncio.wait_for(
                    provider.text_chat(
                        prompt=prompt,
                        session_id=f"xiaoheihe-context-{uuid.uuid4().hex}",
                        persist=False,
                    ),
                    timeout=remaining_seconds,
                )
                result = parse_thread_compression(
                    str(getattr(response, "completion_text", "") or ""),
                    post_chars=post_chars,
                    comments_chars=comments_chars,
                    image_chars=image_chars,
                )
                if source.post_image_caption and not result.post_image_summary:
                    result = replace(
                        result,
                        post_image_summary=clean_untrusted_text(
                            source.post_image_caption,
                            max_chars=image_chars,
                        ),
                    )
            except TimeoutError as exc:
                timeout_error = TimeoutError(f"楼层上下文压缩总预算耗尽（{timeout} 秒）")
                timeout_error.__cause__ = exc
                self.runtime.report_auxiliary_provider_failure(
                    profile_id,
                    "context",
                    provider_label,
                    timeout_error,
                    details={
                        **attempt_details,
                        "elapsed_ms": round((time.monotonic() - attempt_started) * 1000),
                    },
                )
                break
            except Exception as exc:
                self.runtime.report_auxiliary_provider_failure(
                    profile_id,
                    "context",
                    provider_label,
                    exc,
                    details={
                        **attempt_details,
                        "elapsed_ms": round((time.monotonic() - attempt_started) * 1000),
                    },
                )
                continue
            self.runtime.report_auxiliary_provider_success(
                profile_id,
                "context",
                provider_label,
            )
            rendered = render_compressed_thread_context(source, result)
            if result.post_image_summary:
                set_extra = getattr(event, "set_extra", None)
                if callable(set_extra):
                    set_extra("xiaoheihe_visual_context_consumed", True)
            self.runtime.logging.emit(
                "DEBUG",
                "楼层上下文已按来源完成 LLM 语义压缩",
                profile_id=profile_id,
                details={
                    "provider_id": provider_label,
                    "model": self._provider_model(provider),
                    "input_chars": source.compressible_chars,
                    "output_chars": len(rendered),
                    "cached_image_input_chars": len(source.post_image_caption),
                    "cached_image_output_chars": len(result.post_image_summary),
                    "relation_to_post": result.relation_to_post,
                    "elapsed_ms": round((time.monotonic() - attempt_started) * 1000),
                },
            )
            return rendered
        return None

    def _auxiliary_provider_candidates(
        self,
        event: AstrMessageEvent,
        provider_settings: dict,
        *,
        keys: tuple[str, ...],
        purpose: str,
        profile_id: str,
    ) -> list[tuple[object, str]]:
        candidates: list[tuple[object, str]] = []
        seen: set[int] = set()
        for key in keys:
            configured_id = str(provider_settings.get(key, "") or "").strip()
            if not configured_id:
                continue
            if not self.runtime.auxiliary_provider_available(
                profile_id,
                purpose,
                configured_id,
            ):
                continue
            try:
                provider = self.context.get_provider_by_id(configured_id)
            except Exception as exc:
                self.runtime.report_auxiliary_provider_failure(
                    profile_id,
                    purpose,
                    configured_id,
                    exc,
                    details={
                        **self._event_image_diagnostics(event),
                        "stage": "resolve_provider",
                    },
                )
                continue
            if provider is None:
                self.runtime.report_auxiliary_provider_failure(
                    profile_id,
                    purpose,
                    configured_id,
                    RuntimeError("Provider 不存在或当前未启用"),
                )
                continue
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            candidates.append((provider, self._provider_runtime_label(provider, configured_id)))

        get_using_provider = getattr(self.context, "get_using_provider", None)
        if callable(get_using_provider):
            try:
                provider = get_using_provider(umo=event.unified_msg_origin)
            except Exception as exc:
                self.runtime.logging.emit(
                    "WARNING",
                    f"无法获取当前会话辅助 Provider: {exc}",
                    profile_id=profile_id,
                    details={
                        **self._event_image_diagnostics(event),
                        "purpose": purpose,
                        "stage": "resolve_current_session_provider",
                        "exception_type": type(exc).__name__,
                    },
                )
            else:
                if provider is not None and id(provider) not in seen:
                    label = self._provider_runtime_label(provider, "current-session")
                    if self.runtime.auxiliary_provider_available(profile_id, purpose, label):
                        candidates.append((provider, label))
        return candidates

    @staticmethod
    def _provider_runtime_label(provider: object, fallback: str) -> str:
        provider_config = getattr(provider, "provider_config", {})
        if isinstance(provider_config, dict):
            configured_id = str(provider_config.get("id", "") or "").strip()
            if configured_id:
                return configured_id[:256]
        return str(fallback or "current-session")[:256]

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
        self.runtime.logging.emit(
            "DEBUG",
            "已按事件图片数量分配视觉预处理总预算",
            profile_id=profile_id,
            details={
                **self._event_image_diagnostics(event),
                "image_count": len(request.image_urls),
                "configured_image_limit": int(context_settings.get("max_images_per_event", 6)),
                "image_timeout_seconds": int(context_settings.get("image_timeout_seconds", 15)),
                "preprocess_budget_seconds": round(budget_seconds, 3),
                "event_timeout_base_seconds": self._event_raw_message(event).get(
                    "reply_timeout_base_seconds",
                    0,
                ),
                "event_timeout_effective_seconds": self._event_raw_message(event).get(
                    "reply_timeout_effective_seconds",
                    0,
                ),
            },
        )
        deadline = asyncio.get_running_loop().time() + budget_seconds
        remaining_urls: list[str] = []
        remaining_sources: list[str] = []
        for source in ("current_comment", "original_post", "event_image"):
            urls = grouped[source]
            if not urls:
                continue
            if (
                source == "original_post"
                and str(event.get_extra("xiaoheihe_cached_visual_caption", "") or "").strip()
            ):
                self.runtime.logging.emit(
                    "DEBUG",
                    "原帖图片已由视觉上下文缓存覆盖，跳过重复识图",
                    profile_id=profile_id,
                    details={
                        "image_count": len(urls),
                        "visual_context_id": event.get_extra(
                            "xiaoheihe_cached_visual_context_id",
                            None,
                        ),
                        **self._image_url_diagnostics(urls),
                    },
                )
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
            provider_available = self.runtime.auxiliary_provider_available(
                profile_id,
                "image",
                provider_id,
            )
            try:
                provider = self.context.get_provider_by_id(provider_id)
            except Exception as exc:
                if provider_available:
                    self.runtime.report_auxiliary_provider_failure(
                        profile_id,
                        "image",
                        provider_id,
                        exc,
                        details={
                            "stage": "resolve_provider",
                            "source": source,
                            "image_count": len(urls),
                            **self._image_url_diagnostics(urls),
                        },
                    )
                continue
            if provider is None:
                if provider_available:
                    self.runtime.report_auxiliary_provider_failure(
                        profile_id,
                        "image",
                        provider_id,
                        RuntimeError("Provider 不存在或当前未启用"),
                        details={
                            "stage": "resolve_provider",
                            "source": source,
                            "image_count": len(urls),
                            **self._image_url_diagnostics(urls),
                        },
                    )
                continue
            if id(provider) in tried_providers:
                continue
            tried_providers.add(id(provider))
            if not provider_available:
                continue
            result = await self._try_caption_thread_image_group(
                provider,
                event=event,
                provider_label=provider_id,
                source=source,
                urls=urls,
                max_chars=max_chars,
                priority=priority,
                profile_id=profile_id,
                deadline=deadline,
                attempt_timeout_seconds=self._image_attempt_timeout_seconds(
                    context_settings=context_settings,
                    image_count=len(urls),
                ),
            )
            if result:
                return result.rendered

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
                    result = await self._try_caption_thread_image_group(
                        provider,
                        event=event,
                        provider_label="current-session",
                        source=source,
                        urls=urls,
                        max_chars=max_chars,
                        priority=priority,
                        profile_id=profile_id,
                        deadline=deadline,
                        attempt_timeout_seconds=self._image_attempt_timeout_seconds(
                            context_settings=context_settings,
                            image_count=len(urls),
                        ),
                    )
                    if result:
                        return result.rendered
        return None

    async def _try_caption_thread_image_group(
        self,
        provider: object,
        *,
        event: AstrMessageEvent,
        provider_label: str,
        source: str,
        urls: list[str],
        max_chars: int,
        priority: str,
        profile_id: str,
        deadline: float,
        attempt_timeout_seconds: float,
    ) -> ImageCaptionResult | None:
        provider_label = self._provider_runtime_label(provider, provider_label)
        model = self._provider_model(provider)
        cache_layer = "memory"
        cached = await self._get_cached_image_caption(
            profile_id=profile_id,
            source=source,
            urls=urls,
        )
        if cached is None and source == "original_post" and self.runtime.started:
            raw = self._event_raw_message(event)
            route = raw.get("route", {})
            route = route if isinstance(route, dict) else {}
            post_id = str(route.get("post_id", "") or "")
            try:
                record = await self.runtime.repository.visual_context_for_post(
                    profile_id,
                    post_id,
                    self._image_fingerprint(urls),
                )
                persistent_caption, rejection = self._validated_visual_record_caption(record)
                if record and persistent_caption:
                    cache_layer = "sqlite"
                    cached = ImageCaptionCacheEntry(
                        expires_at=asyncio.get_running_loop().time()
                        + max(
                            1.0,
                            float(record.get("expires_at", time.time())) - time.time(),
                        ),
                        caption=persistent_caption,
                        provider_label=str(record.get("provider_id", "") or "persistent-cache"),
                        model=str(record.get("model", "") or ""),
                        visual_context_id=int(record["id"]),
                    )
                    await self._store_cached_image_caption(
                        provider_label=cached.provider_label,
                        model=cached.model,
                        profile_id=profile_id,
                        source=source,
                        urls=urls,
                        caption=cached.caption,
                        visual_context_id=cached.visual_context_id,
                        ttl_seconds=max(
                            1.0,
                            float(record.get("expires_at", time.time())) - time.time(),
                        ),
                    )
                elif record:
                    self.runtime.logging.emit(
                        "ERROR",
                        "图片指纹视觉缓存校验失败，已拒绝复用并继续调用 Provider",
                        profile_id=profile_id,
                        details={
                            **self._event_image_diagnostics(event),
                            "source": source,
                            "visual_context_id": record.get("id"),
                            "rejection_reason": rejection,
                            **self._image_url_diagnostics(urls),
                        },
                    )
            except Exception as exc:
                self.runtime.logging.emit(
                    "ERROR",
                    f"读取图片指纹视觉缓存失败，继续调用图片 Provider: {exc}",
                    profile_id=profile_id,
                    details={
                        **self._event_image_diagnostics(event),
                        "exception_type": type(exc).__name__,
                        "source": source,
                        "image_count": len(urls),
                        **self._image_url_diagnostics(urls),
                    },
                )
        if cached:
            caption = clean_untrusted_text(cached.caption, max_chars=max_chars)
            self.runtime.logging.emit(
                "DEBUG",
                "命中原帖视觉描述缓存，跳过图片 Provider 调用",
                profile_id=profile_id,
                details={
                    **self._event_image_diagnostics(event),
                    "source": source,
                    "image_count": len(urls),
                    "provider_id": cached.provider_label,
                    "model": cached.model,
                    "caption_chars": len(cached.caption),
                    "cache_hit": cache_layer,
                    "visual_context_id": cached.visual_context_id,
                    **self._image_url_diagnostics(urls),
                },
            )
            result = ImageCaptionResult(
                caption=cached.caption,
                rendered=render_image_context(
                    source=source,
                    caption=caption,
                    priority=priority,
                ),
                provider_label=cached.provider_label,
                model=cached.model,
                cache_hit=cache_layer,
                visual_context_id=cached.visual_context_id,
            )
            return await self._persist_visual_caption(
                event,
                profile_id=profile_id,
                source=source,
                urls=urls,
                result=result,
            )
        if not self._provider_may_accept_images(provider):
            self.runtime.logging.emit(
                "DEBUG",
                "图片预处理 Provider 明确不支持图片，尝试下一 Provider",
                profile_id=profile_id,
                details={
                    **self._event_image_diagnostics(event),
                    "provider_id": provider_label,
                    "model": model,
                    "source": source,
                    "image_count": len(urls),
                },
            )
            return None
        text_chat = getattr(provider, "text_chat", None)
        if not callable(text_chat):
            return None
        if not self.runtime.auxiliary_provider_available(
            profile_id,
            "image",
            provider_label,
        ):
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
        call_timeout = min(remaining_seconds, max(1.0, float(attempt_timeout_seconds)))
        canonical_max_chars = max_chars if source != "original_post" else max(2400, max_chars)
        diagnostics = {
            **self._event_image_diagnostics(event),
            **self._image_url_diagnostics(urls),
        }
        started_at = time.monotonic()
        self.runtime.logging.emit(
            "DEBUG",
            "开始图片视觉预处理",
            profile_id=profile_id,
            details={
                "provider_id": provider_label,
                "model": model,
                "source": source,
                "image_count": len(urls),
                "caption_limit_chars": canonical_max_chars,
                "attempt_timeout_seconds": round(call_timeout, 3),
                "event_budget_remaining_seconds": round(remaining_seconds, 3),
                **diagnostics,
            },
        )
        try:
            response = await asyncio.wait_for(
                text_chat(
                    prompt=build_image_compression_prompt(
                        source=source,
                        max_chars=canonical_max_chars,
                    ),
                    session_id=f"xiaoheihe-image-{uuid.uuid4().hex}",
                    image_urls=urls,
                    persist=False,
                    request_max_retries=1,
                ),
                timeout=call_timeout,
            )
            caption = clean_untrusted_text(
                str(getattr(response, "completion_text", "") or ""),
                max_chars=canonical_max_chars,
            )
            if not caption:
                raise ValueError("图片预处理 Provider 返回空描述")
            if is_unusable_image_caption(caption):
                raise ValueError("图片预处理 Provider 返回无法访问图片的占位描述")
        except TimeoutError as exc:
            timeout_error = TimeoutError(
                "图片预处理在本事件总时间预算内的单次调用超时"
                f"（{round(max(0.0, call_timeout), 3)} 秒）"
            )
            timeout_error.__cause__ = exc
            self.runtime.report_auxiliary_provider_failure(
                profile_id,
                "image",
                provider_label,
                timeout_error,
                details={
                    "model": model,
                    "source": source,
                    "image_count": len(urls),
                    "elapsed_ms": round((time.monotonic() - started_at) * 1000),
                    "attempt_timeout_seconds": round(call_timeout, 3),
                    **diagnostics,
                },
            )
            return None
        except Exception as exc:
            self.runtime.report_auxiliary_provider_failure(
                profile_id,
                "image",
                provider_label,
                exc,
                details={
                    "model": model,
                    "source": source,
                    "image_count": len(urls),
                    "elapsed_ms": round((time.monotonic() - started_at) * 1000),
                    "caption_rejected": isinstance(exc, ValueError),
                    **diagnostics,
                },
            )
            return None
        self.runtime.report_auxiliary_provider_success(profile_id, "image", provider_label)
        await self._store_cached_image_caption(
            provider_label=provider_label,
            model=model,
            profile_id=profile_id,
            source=source,
            urls=urls,
            caption=caption,
        )
        result = ImageCaptionResult(
            caption=caption,
            rendered=render_image_context(
                source=source,
                caption=clean_untrusted_text(caption, max_chars=max_chars),
                priority=priority,
            ),
            provider_label=provider_label,
            model=model,
        )
        result = await self._persist_visual_caption(
            event,
            profile_id=profile_id,
            source=source,
            urls=urls,
            result=result,
        )
        self.runtime.logging.emit(
            "DEBUG",
            "图片视觉预处理完成",
            profile_id=profile_id,
            details={
                "provider_id": provider_label,
                "model": model,
                "source": source,
                "image_count": len(urls),
                "caption_chars": len(caption),
                "elapsed_ms": round((time.monotonic() - started_at) * 1000),
                "cache_hit": result.cache_hit or "miss",
                "visual_context_id": result.visual_context_id,
                "cache_ttl_seconds": IMAGE_CAPTION_CACHE_TTL_SECONDS,
                **diagnostics,
            },
        )
        return result

    @staticmethod
    def _provider_model(provider: object) -> str:
        get_model = getattr(provider, "get_model", None)
        if callable(get_model):
            try:
                return str(get_model() or "").strip()[:256]
            except Exception:
                return ""
        return ""

    @staticmethod
    def _normalized_image_identity(url: str) -> str:
        parsed = urlsplit(str(url or ""))
        return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{parsed.path}"

    @classmethod
    def _image_fingerprint(cls, urls: list[str]) -> str:
        identities = [cls._normalized_image_identity(url) for url in urls if str(url).strip()]
        if not identities:
            return ""
        return hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()

    @classmethod
    def _image_url_diagnostics(cls, urls: list[str]) -> dict[str, object]:
        hosts = [urlsplit(str(url)).hostname or "" for url in urls]
        fingerprint = cls._image_fingerprint(urls)
        return {
            "image_hosts": [host[:160] for host in hosts[:20]],
            "image_fingerprint_prefix": fingerprint[:16],
        }

    @staticmethod
    def _validated_visual_record_caption(record: dict | None) -> tuple[str, str]:
        if not record:
            return "", "missing"
        raw_caption = str(record.get("caption", "") or "")[:4000]
        expected_hash = str(record.get("caption_hash", "") or "")
        actual_hash = hashlib.sha256(raw_caption.encode("utf-8")).hexdigest()
        if not raw_caption:
            return "", "empty"
        if not expected_hash or expected_hash != actual_hash:
            return "", "hash_mismatch"
        caption = clean_untrusted_text(raw_caption, max_chars=4000)
        if not caption:
            return "", "empty_after_cleaning"
        if is_unusable_image_caption(caption):
            return "", "provider_placeholder"
        return caption, ""

    @classmethod
    def _image_caption_cache_key(
        cls,
        *,
        profile_id: str,
        source: str,
        urls: list[str],
    ) -> str | None:
        if source != "original_post" or not urls:
            return None
        fingerprint = cls._image_fingerprint(urls)
        if not fingerprint:
            return None
        return f"{profile_id}:{source}:{fingerprint}"

    async def _get_cached_image_caption(
        self,
        *,
        profile_id: str,
        source: str,
        urls: list[str],
    ) -> ImageCaptionCacheEntry | None:
        key = self._image_caption_cache_key(
            profile_id=profile_id,
            source=source,
            urls=urls,
        )
        if key is None:
            return None
        now = asyncio.get_running_loop().time()
        async with self._image_caption_cache_lock:
            cached = self._image_caption_cache.get(key)
            if cached is None:
                return None
            if cached.expires_at <= now:
                self._image_caption_cache.pop(key, None)
                return None
            self._image_caption_cache.move_to_end(key)
            return cached

    async def _store_cached_image_caption(
        self,
        *,
        provider_label: str,
        model: str,
        profile_id: str,
        source: str,
        urls: list[str],
        caption: str,
        visual_context_id: int | None = None,
        ttl_seconds: float = IMAGE_CAPTION_CACHE_TTL_SECONDS,
    ) -> None:
        key = self._image_caption_cache_key(
            profile_id=profile_id,
            source=source,
            urls=urls,
        )
        if key is None:
            return
        expires_at = asyncio.get_running_loop().time() + max(
            1.0,
            min(float(ttl_seconds), float(IMAGE_CAPTION_CACHE_TTL_SECONDS)),
        )
        async with self._image_caption_cache_lock:
            self._image_caption_cache[key] = ImageCaptionCacheEntry(
                expires_at=expires_at,
                caption=caption,
                provider_label=provider_label,
                model=model,
                visual_context_id=visual_context_id,
            )
            self._image_caption_cache.move_to_end(key)
            while len(self._image_caption_cache) > IMAGE_CAPTION_CACHE_MAX_ENTRIES:
                self._image_caption_cache.popitem(last=False)

    @staticmethod
    def _event_raw_message(event: AstrMessageEvent) -> dict:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", {})
        return raw if isinstance(raw, dict) else {}

    @classmethod
    def _event_image_diagnostics(cls, event: AstrMessageEvent) -> dict[str, object]:
        raw = cls._event_raw_message(event)
        route = raw.get("route", {})
        route = route if isinstance(route, dict) else {}
        return {
            "event_type": str(raw.get("event_type", "") or ""),
            "incoming_event_id": raw.get("incoming_event_id"),
            "post_id": str(route.get("post_id", "") or ""),
            "root_comment_id": str(route.get("root_comment_id", "") or ""),
            "reply_target_comment_id": str(raw.get("reply_target_comment_id", "") or ""),
            "proactive": bool(raw.get("proactive", False)),
        }

    @staticmethod
    def _set_event_extra(event: AstrMessageEvent, key: str, value: object) -> None:
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            setter(key, value)

    async def _link_visual_context_to_current_event(
        self,
        event: AstrMessageEvent,
        visual_context_id: int | None,
    ) -> bool:
        if visual_context_id is None or not self.runtime.started:
            return False
        raw = self._event_raw_message(event)
        try:
            event_id = int(raw.get("incoming_event_id") or 0)
        except (TypeError, ValueError):
            return False
        if event_id > 0:
            return await self.runtime.repository.link_visual_context_to_event(
                event_id,
                visual_context_id,
            )
        return False

    async def _persist_visual_caption(
        self,
        event: AstrMessageEvent,
        *,
        profile_id: str,
        source: str,
        urls: list[str],
        result: ImageCaptionResult,
    ) -> ImageCaptionResult:
        if source != "original_post" or not urls or not self.runtime.started:
            return result
        raw = self._event_raw_message(event)
        route = raw.get("route", {})
        route = route if isinstance(route, dict) else {}
        post_id = str(route.get("post_id", "") or "").strip()
        try:
            event_id = int(raw.get("incoming_event_id") or 0)
        except (TypeError, ValueError):
            return result
        if event_id <= 0 or not post_id:
            return result
        fingerprint = self._image_fingerprint(urls)
        diagnostics = self._image_url_diagnostics(urls)
        try:
            if result.visual_context_id is not None:
                visual_context_id = result.visual_context_id
                linked = await self.runtime.repository.link_visual_context_to_event(
                    event_id,
                    visual_context_id,
                )
                if not linked:
                    self.runtime.logging.emit(
                        "INFO",
                        "视觉快照已到期或被存储压力清理，本轮只使用内存描述且不延长期限",
                        profile_id=profile_id,
                        details={
                            "event_id": event_id,
                            "post_id": post_id,
                            "visual_context_id": visual_context_id,
                            **diagnostics,
                        },
                    )
            else:
                record = await self.runtime.repository.cache_visual_context(
                    profile_id=profile_id,
                    post_id=post_id,
                    source=source,
                    image_fingerprint=fingerprint,
                    image_hosts=[urlsplit(str(url)).hostname or "" for url in urls],
                    image_count=len(urls),
                    caption=result.caption,
                    provider_id=result.provider_label,
                    model=result.model,
                    ttl_seconds=IMAGE_CAPTION_CACHE_TTL_SECONDS,
                )
                visual_context_id = int(record["id"])
                linked = await self.runtime.repository.link_visual_context_to_event(
                    event_id,
                    visual_context_id,
                )
                if not linked:
                    raise RuntimeError("新视觉快照未能绑定当前入站事件")
                await self._store_cached_image_caption(
                    provider_label=result.provider_label,
                    model=result.model,
                    profile_id=profile_id,
                    source=source,
                    urls=urls,
                    caption=result.caption,
                    visual_context_id=visual_context_id,
                    ttl_seconds=max(
                        1.0,
                        float(record.get("expires_at", time.time())) - time.time(),
                    ),
                )
        except Exception as exc:
            self.runtime.logging.emit(
                "ERROR",
                f"视觉上下文持久化失败，当前回复继续使用内存描述: {exc}",
                profile_id=profile_id,
                details={
                    "exception_type": type(exc).__name__,
                    "event_id": event_id,
                    "post_id": post_id,
                    "source": source,
                    "image_count": len(urls),
                    "provider_id": result.provider_label,
                    "model": result.model,
                    **diagnostics,
                },
            )
            return result
        return replace(result, visual_context_id=visual_context_id)

    async def _prepare_visual_context_for_reply(
        self,
        event: AstrMessageEvent,
        request: ProviderRequest,
        *,
        profile_id: str,
        post_id: str,
    ) -> ImageCaptionCacheEntry | None:
        sources = self._normalized_image_sources(event, request)
        post_urls = [
            url
            for url, source in zip(request.image_urls, sources, strict=True)
            if source == "original_post"
        ]
        raw = self._event_raw_message(event)
        route = raw.get("route", {})
        route = route if isinstance(route, dict) else {}
        candidates = [
            str(raw.get("reply_target_comment_id", "") or ""),
            str(route.get("root_comment_id", "") or ""),
            str(route.get("parent_comment_id", "") or ""),
        ]
        record: dict | None = None
        hit = ""
        try:
            if self.runtime.started:
                record = await self.runtime.repository.visual_context_for_comment(
                    profile_id,
                    candidates,
                )
                if record is not None:
                    if str(record.get("post_id", "")) == post_id:
                        hit = "comment_binding"
                    else:
                        self.runtime.logging.emit(
                            "ERROR",
                            "机器人评论绑定命中了其他帖子，已拒绝复用视觉上下文",
                            profile_id=profile_id,
                            details={
                                "requested_post_id": post_id,
                                "bound_post_id": record.get("post_id", ""),
                                "visual_context_id": record.get("id"),
                            },
                        )
                        record = None
            if record is None and post_urls:
                cached = await self._get_cached_image_caption(
                    profile_id=profile_id,
                    source="original_post",
                    urls=post_urls,
                )
                if cached is not None:
                    if self.runtime.started:
                        persisted = await self._persist_visual_caption(
                            event,
                            profile_id=profile_id,
                            source="original_post",
                            urls=post_urls,
                            result=ImageCaptionResult(
                                caption=cached.caption,
                                rendered="",
                                provider_label=cached.provider_label,
                                model=cached.model,
                                cache_hit="memory",
                                visual_context_id=cached.visual_context_id,
                            ),
                        )
                        cached = replace(
                            cached,
                            visual_context_id=persisted.visual_context_id,
                        )
                    self._set_event_extra(
                        event,
                        "xiaoheihe_cached_visual_caption",
                        cached.caption,
                    )
                    self._set_event_extra(
                        event,
                        "xiaoheihe_cached_visual_context_id",
                        cached.visual_context_id,
                    )
                    self.runtime.logging.emit(
                        "DEBUG",
                        "命中原帖视觉上下文内存缓存",
                        profile_id=profile_id,
                        details={
                            "post_id": post_id,
                            "image_count": len(post_urls),
                            "visual_context_id": cached.visual_context_id,
                            **self._image_url_diagnostics(post_urls),
                        },
                    )
                    return cached
                if self.runtime.started:
                    record = await self.runtime.repository.visual_context_for_post(
                        profile_id,
                        post_id,
                        self._image_fingerprint(post_urls),
                    )
                    if record is not None:
                        hit = "post_fingerprint"
        except Exception as exc:
            self.runtime.logging.emit(
                "ERROR",
                f"读取持久化视觉上下文失败，继续走正常识图/纯文本路径: {exc}",
                profile_id=profile_id,
                details={
                    "exception_type": type(exc).__name__,
                    "post_id": post_id,
                    "candidate_comment_ids": [value for value in candidates if value],
                    "image_count": len(post_urls),
                    **self._image_url_diagnostics(post_urls),
                },
            )
            return None
        if record is None:
            return None
        caption, rejection = self._validated_visual_record_caption(record)
        if not caption:
            self.runtime.logging.emit(
                "ERROR",
                "持久化视觉上下文校验失败，已拒绝复用",
                profile_id=profile_id,
                details={
                    "post_id": post_id,
                    "visual_context_id": record.get("id"),
                    "cache_hit": hit,
                    "caption_chars": len(caption),
                    "rejection_reason": rejection,
                },
            )
            return None
        entry = ImageCaptionCacheEntry(
            expires_at=asyncio.get_running_loop().time()
            + max(1.0, float(record.get("expires_at", time.time())) - time.time()),
            caption=caption,
            provider_label=str(record.get("provider_id", "") or "persistent-cache"),
            model=str(record.get("model", "") or ""),
            visual_context_id=int(record["id"]),
        )
        if post_urls:
            await self._store_cached_image_caption(
                provider_label=entry.provider_label,
                model=entry.model,
                profile_id=profile_id,
                source="original_post",
                urls=post_urls,
                caption=entry.caption,
                visual_context_id=entry.visual_context_id,
                ttl_seconds=max(
                    1.0,
                    float(record.get("expires_at", time.time())) - time.time(),
                ),
            )
        self._set_event_extra(event, "xiaoheihe_cached_visual_caption", entry.caption)
        self._set_event_extra(
            event,
            "xiaoheihe_cached_visual_context_id",
            entry.visual_context_id,
        )
        try:
            await self._link_visual_context_to_current_event(
                event,
                entry.visual_context_id,
            )
        except Exception as exc:
            self.runtime.logging.emit(
                "ERROR",
                f"持久化视觉缓存绑定当前事件失败，当前回复继续: {exc}",
                profile_id=profile_id,
                details={
                    "exception_type": type(exc).__name__,
                    "post_id": post_id,
                    "visual_context_id": entry.visual_context_id,
                },
            )
        self.runtime.logging.emit(
            "DEBUG",
            "命中持久化原帖视觉上下文",
            profile_id=profile_id,
            details={
                "post_id": post_id,
                "cache_hit": hit,
                "bound_comment_id": record.get("bound_comment_id", ""),
                "visual_context_id": entry.visual_context_id,
                "caption_chars": len(entry.caption),
                "image_count": int(record.get("image_count", 0) or 0),
                "expires_in_seconds": round(
                    max(0.0, float(record.get("expires_at", 0) or 0) - time.time())
                ),
            },
        )
        return entry

    def _append_cached_visual_context_if_needed(
        self,
        event: AstrMessageEvent,
        request: ProviderRequest,
        *,
        context_settings: dict,
    ) -> None:
        if bool(event.get_extra("xiaoheihe_visual_context_consumed", False)):
            return
        caption = clean_untrusted_text(
            str(event.get_extra("xiaoheihe_cached_visual_caption", "") or ""),
            max_chars=int(context_settings["thread_reply_compressed_image_chars"]),
        )
        if not caption:
            return
        request.extra_user_content_parts.append(
            TextPart(
                text=render_image_context(
                    source="original_post",
                    caption=caption,
                    priority="low",
                )
            ).mark_as_temp()
        )
        self._set_event_extra(event, "xiaoheihe_visual_context_consumed", True)

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
        configured_limit = max(0, min(20, int(context_settings.get("max_images_per_event", 6))))
        count = min(max(0, int(image_count)), configured_limit)
        if count == 0:
            return 0.0

        image_timeout = max(1.0, float(context_settings.get("image_timeout_seconds", 15)))
        per_image_grace = min(60.0, max(MIN_IMAGE_REPLY_GRACE_SECONDS, image_timeout * 2))
        count_linked_budget = min(
            MAX_IMAGE_PREPROCESS_BUDGET_SECONDS,
            per_image_grace * count,
            per_image_grace * configured_limit,
        )

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
                return min(count_linked_budget, grace_seconds)
        return count_linked_budget

    @staticmethod
    def _image_attempt_timeout_seconds(
        *,
        context_settings: dict,
        image_count: int,
    ) -> float:
        count = max(1, min(20, int(image_count)))
        per_image = max(1.0, float(context_settings.get("image_timeout_seconds", 15)))
        return min(
            60.0,
            max(MIN_IMAGE_REPLY_GRACE_SECONDS, per_image * count),
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

    async def _caption_proactive_images(
        self,
        event: AstrMessageEvent,
        request: ProviderRequest,
        *,
        provider_settings: dict,
        profile_id: str,
        context_settings: dict,
    ) -> bool:
        """Create a reusable proactive visual snapshot, with AstrBot providers as fallback."""
        sources = self._normalized_image_sources(event, request)
        groups: list[tuple[str, list[str]]] = []
        for source in ("current_comment", "original_post", "event_image"):
            urls = [
                url
                for url, image_source in zip(request.image_urls, sources, strict=True)
                if image_source == source
            ]
            if urls:
                groups.append((source, urls))
        budget_seconds = self._image_preprocess_budget_seconds(
            event,
            context_settings=context_settings,
            image_count=len(request.image_urls),
        )
        deadline = asyncio.get_running_loop().time() + budget_seconds
        candidates = self._auxiliary_provider_candidates(
            event,
            provider_settings,
            keys=("image_provider_id", "llm_provider_id"),
            purpose="image",
            profile_id=profile_id,
        )
        self.runtime.logging.emit(
            "DEBUG",
            "主动帖子图片将按辅助 Provider 链生成可复用视觉快照",
            profile_id=profile_id,
            details={
                **self._event_image_diagnostics(event),
                "image_count": len(request.image_urls),
                "group_count": len(groups),
                "provider_candidates": [label for _, label in candidates],
                "preprocess_budget_seconds": round(budget_seconds, 3),
                "configured_image_limit": int(context_settings.get("max_images_per_event", 6)),
            },
        )
        for provider, provider_label in candidates:
            rendered: list[str] = []
            complete = True
            for source, urls in groups:
                result = await self._try_caption_thread_image_group(
                    provider,
                    event=event,
                    provider_label=provider_label,
                    source=source,
                    urls=urls,
                    max_chars=max(
                        2400,
                        int(context_settings["thread_reply_compressed_image_chars"]),
                    ),
                    priority="primary",
                    profile_id=profile_id,
                    deadline=deadline,
                    attempt_timeout_seconds=self._image_attempt_timeout_seconds(
                        context_settings=context_settings,
                        image_count=len(urls),
                    ),
                )
                if result is None:
                    complete = False
                    break
                rendered.append(result.rendered)
            if not complete:
                continue
            for block in rendered:
                request.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
            request.image_urls.clear()
            self._set_event_extra(event, "xiaoheihe_image_sources", [])
            return True
        self.runtime.logging.emit(
            "WARNING",
            "主动帖子视觉快照生成失败，保留原图交给 AstrBot 主对话视觉路径",
            profile_id=profile_id,
            details={
                **self._event_image_diagnostics(event),
                "image_count": len(request.image_urls),
                "provider_candidates": [label for _, label in candidates],
                **self._image_url_diagnostics(request.image_urls),
            },
        )
        return False

    async def _caption_images(
        self,
        event: AstrMessageEvent,
        request: ProviderRequest,
        *,
        provider_id: str,
        profile_id: str,
        context_settings: dict,
    ) -> bool:
        if not self.runtime.auxiliary_provider_available(profile_id, "image", provider_id):
            return False
        try:
            provider = self.context.get_provider_by_id(provider_id)
        except Exception as exc:
            self.runtime.report_auxiliary_provider_failure(
                profile_id,
                "image",
                provider_id,
                exc,
                details={
                    "stage": "resolve_fixed_image_provider",
                    "image_count": len(request.image_urls),
                    **self._image_url_diagnostics(request.image_urls),
                },
            )
            return False
        if provider is None:
            self.runtime.report_auxiliary_provider_failure(
                profile_id,
                "image",
                provider_id,
                RuntimeError("固定图片 Provider 不存在或当前未启用"),
                details={
                    "stage": "resolve_fixed_image_provider",
                    "image_count": len(request.image_urls),
                    **self._image_url_diagnostics(request.image_urls),
                },
            )
            return False
        provider_label = self._provider_runtime_label(provider, provider_id)
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
            result = await self._try_caption_thread_image_group(
                provider,
                event=event,
                provider_label=provider_label,
                source=source,
                urls=urls,
                max_chars=max_chars,
                priority=priority,
                profile_id=profile_id,
                deadline=deadline,
                attempt_timeout_seconds=self._image_attempt_timeout_seconds(
                    context_settings=context_settings,
                    image_count=len(urls),
                ),
            )
            if not result:
                return False
            rendered.append(result.rendered)
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
        participants: list[str] = []
        raw_participants = getattr(value, "recent_participants", ())
        if isinstance(raw_participants, (list, tuple)):
            for item in raw_participants[:64]:
                identity = clean_untrusted_text(str(item), max_chars=180).replace("\n", " ")
                if identity:
                    participants.append(identity)
        image_caption = clean_untrusted_text(
            str(getattr(value, "post_image_caption", "") or ""),
            max_chars=4000,
        )
        return ThreadCompressionSource(
            **values,
            recent_participants=tuple(participants),
            post_image_caption=image_caption,
        )

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
        async with self._image_caption_cache_lock:
            self._image_caption_cache.clear()
        await self.runtime.close()
