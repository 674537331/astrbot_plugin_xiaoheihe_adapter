from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import suppress
from typing import Any

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Plain
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.core.platform.astr_message_event import MessageSession

from .context_builder import BuiltContext, ContextBuilder
from .event import XiaoheiheMessageEvent, aggregate_text
from .models import (
    EventState,
    Notification,
    PermissionDecision,
    RoutingTarget,
)
from .notification_service import NotificationService
from .permission_service import PermissionService
from .runtime import get_runtime

MAX_EFFECTIVE_REPLY_TIMEOUT_SECONDS = 900
MIN_IMAGE_REPLY_GRACE_SECONDS = 15
MAX_IMAGE_REPLY_GRACE_SECONDS = 60


def effective_reply_timeout_seconds(
    *,
    base_timeout_seconds: int,
    image_count: int,
    image_timeout_seconds: int,
) -> int:
    """Add bounded processing time for AstrBot's per-image vision preprocessing."""
    base_timeout = max(5, int(base_timeout_seconds))
    count = max(0, int(image_count))
    if count == 0:
        return base_timeout
    per_image_grace = min(
        MAX_IMAGE_REPLY_GRACE_SECONDS,
        max(MIN_IMAGE_REPLY_GRACE_SECONDS, int(image_timeout_seconds) * 2),
    )
    return min(
        MAX_EFFECTIVE_REPLY_TIMEOUT_SECONDS,
        base_timeout + count * per_image_grace,
    )


def proactive_delivery_settings(config: dict[str, Any]) -> tuple[bool, bool]:
    """Return the dry-run and review gates for one proactive event."""
    return (
        bool(config.get("dry_run", True)),
        bool(config.get("review_required", True)),
    )


DEFAULT_PLATFORM_CONFIG = {
    "id": "xiaoheihe",
    "enable": False,
    "profile_id": "default",
}
ADAPTER_LOGO_PATH = "../logo.png"


@register_platform_adapter(
    "xiaoheihe",
    "小黑盒平台适配器",
    default_config_tmpl=DEFAULT_PLATFORM_CONFIG,
    adapter_display_name="小黑盒",
    logo_path=ADAPTER_LOGO_PATH,
    support_streaming_message=False,
)
class XiaoheihePlatformAdapter(Platform):
    def __init__(
        self,
        platform_config: dict,
        platform_settings: dict,
        event_queue: asyncio.Queue,
    ) -> None:
        super().__init__(platform_config or {}, event_queue)
        self.settings = platform_settings or {}
        self._stop = asyncio.Event()
        self._refresh = asyncio.Event()
        self._service: NotificationService | None = None
        self._context_builder: ContextBuilder | None = None
        self._feed_task: asyncio.Task[Any] | None = None
        self.running = False
        self._refresh.set()

    @property
    def queue_length(self) -> int:
        if self._service is None:
            return 0
        return self._service._queue.qsize()

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="xiaoheihe",
            description="小黑盒平台适配器",
            id=str(self.config.get("id", "xiaoheihe")),
            default_config_tmpl={**DEFAULT_PLATFORM_CONFIG, **self.config},
            adapter_display_name="小黑盒",
            logo_path=ADAPTER_LOGO_PATH,
            support_streaming_message=False,
        )

    def request_refresh(self) -> None:
        self._refresh.set()

    async def run(self) -> None:
        runtime = get_runtime()
        await runtime.ensure_started()
        runtime.register_adapter(self)
        self.running = True
        try:
            while not self._stop.is_set():
                await self._refresh.wait()
                self._refresh.clear()
                await self._stop_profile_services()
                if self._stop.is_set():
                    break
                try:
                    await self._start_profile_services()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    runtime.logging.emit(
                        "ERROR",
                        f"适配器后台服务启动失败: {exc}",
                        profile_id=str(self.config.get("profile_id", "default")),
                    )
                refresh_wait = asyncio.create_task(self._refresh.wait())
                stop_wait = asyncio.create_task(self._stop.wait())
                done, pending = await asyncio.wait(
                    {refresh_wait, stop_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    with suppress(asyncio.CancelledError):
                        task.result()
        finally:
            self.running = False
            await self._stop_profile_services()
            runtime.unregister_adapter(self)

    async def _start_profile_services(self) -> None:
        runtime = get_runtime()
        profile_id = str(self.config.get("profile_id", "default"))
        profile = runtime.config.profile(profile_id)
        if not profile.get("enabled", True):
            runtime.logging.emit("INFO", "账号档案已禁用", profile_id=profile_id)
            return
        credentials = runtime.credentials.load(profile_id)
        if credentials is None:
            runtime.logging.emit(
                "WARNING",
                "账号尚未扫码登录，适配器保持待命",
                profile_id=profile_id,
            )
            return
        account = await runtime.repository.account_state(profile_id)
        circuit_open_until = float(account.get("circuit_open_until") or 0)
        if account.get("status") == "credential_invalid" or circuit_open_until > time.time():
            runtime.logging.emit(
                "WARNING",
                "账号凭证失效或熔断仍在生效，适配器暂停网络任务",
                profile_id=profile_id,
                details={"circuit_open_until": circuit_open_until},
            )
            return
        client = await runtime.get_client(profile_id)
        config = runtime.config.snapshot()
        context_config = config["context"]
        self._context_builder = ContextBuilder(
            max_post_chars=int(context_config["max_post_chars"]),
            max_thread_comments=int(context_config["max_thread_comments"]),
            max_images=int(context_config["max_images_per_event"]),
            cache_ttl_seconds=int(context_config["context_cache_ttl_seconds"]),
            cache_max_entries=int(context_config["context_cache_max_entries"]),
        )
        permissions = PermissionService(
            config["permissions"],
            owner_uid=str(profile.get("owner_uid", "")),
            self_uid=credentials.uid,
            only_explicit_mentions=bool(config["reply"]["only_explicit_mentions"]),
            reply_to_direct_replies=bool(config["reply"]["reply_to_direct_replies"]),
        )
        self._service = NotificationService(
            profile,
            config,
            client,
            runtime.repository,
            runtime.tasks,
            runtime.logging,
            permissions,
            self._context_builder,
            self._dispatch,
        )
        await runtime.tasks.start_profile_poller(profile_id, self._service.run())
        proactive_config = config["proactive_feed"]
        if proactive_config.get("enabled", False):
            dry_run, review_required = proactive_delivery_settings(proactive_config)
            if not dry_run and not review_required:
                runtime.logging.emit(
                    "WARNING",
                    "主动刷帖无审核真实发送已启用，AI 回复将直接发表评论",
                    profile_id=profile_id,
                )
            feed = runtime.feed_service(profile_id, client, self._dispatch_synthetic)
            self._feed_task = await runtime.tasks.start(
                f"xhh-feed-{profile_id}",
                self._feed_loop(
                    feed,
                    int(proactive_config["interval_seconds"]),
                    int(proactive_config["jitter_seconds"]),
                ),
                replace=True,
            )

    async def _feed_loop(self, feed, interval: int, jitter: int) -> None:
        while not self._stop.is_set() and not self._refresh.is_set():
            try:
                await feed.run_once()
                get_runtime().clear_proactive_circuit(str(self.config.get("profile_id", "default")))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                get_runtime().report_proactive_circuit(
                    str(self.config.get("profile_id", "default")),
                    exc,
                )
                await asyncio.sleep(max(300, interval))
            try:
                jitter_ms = secrets.randbelow(max(0, jitter) * 1000 + 1)
                delay = max(300, interval) + jitter_ms / 1000
                await asyncio.wait_for(self._refresh.wait(), timeout=delay)
            except TimeoutError:
                continue

    async def _stop_profile_services(self) -> None:
        profile_id = str(self.config.get("profile_id", "default"))
        runtime = get_runtime()
        if self._service is not None:
            await self._service.stop()
            self._service = None
        await runtime.tasks.cancel(f"xhh-poll-{profile_id}")
        await runtime.tasks.cancel_prefix(f"xhh-worker-{profile_id}-")
        await runtime.tasks.cancel(f"xhh-feed-{profile_id}")
        if self._context_builder is not None:
            await self._context_builder.clear()
            self._context_builder = None
        self._feed_task = None

    async def _dispatch(
        self,
        event_id: int,
        notification: Notification,
        context: BuiltContext,
        permission: PermissionDecision,
        *,
        capture_candidate: bool = False,
        candidate_metadata: dict[str, Any] | None = None,
        dry_run_override: bool | None = None,
        proactive: bool = False,
    ) -> None:
        runtime = get_runtime()
        runtime_config = runtime.config.snapshot()
        credentials = runtime.credentials.load(notification.profile_id)
        if credentials is None:
            raise RuntimeError("分发事件时账号凭证不存在")
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE
        message.self_id = credentials.uid
        message.session_id = notification.route.session_id
        message.message_id = notification.message_id
        message.group_id = notification.route.group_id
        message.sender = MessageMember(
            user_id=str(notification.sender_uid),
            nickname=notification.sender_nickname,
        )
        components = [Plain(context.user_text)]
        image_understanding_enabled = bool(runtime_config["context"]["enable_image_understanding"])
        if image_understanding_enabled:
            components.extend(Image(file=url, url=url) for url in context.image_urls)
        base_reply_timeout = int(runtime_config["reply"]["reply_timeout_seconds"])
        effective_reply_timeout = effective_reply_timeout_seconds(
            base_timeout_seconds=base_reply_timeout,
            image_count=len(context.image_urls) if image_understanding_enabled else 0,
            image_timeout_seconds=int(runtime_config["context"]["image_timeout_seconds"]),
        )
        message.message = components
        message.message_str = context.user_text
        message.timestamp = int(notification.created_at or time.time())
        message.raw_message = {
            "event_type": notification.event_type.value,
            "route": notification.route.as_dict(),
            "external_event_id": notification.external_event_id,
            "external_comment_id": notification.external_comment_id,
            "sender_uid": str(notification.sender_uid),
            "post_author_uid": str(notification.post_author_uid),
            "image_urls": list(context.image_urls),
            "warnings": list(context.warnings),
            "reply_timeout_base_seconds": base_reply_timeout,
            "reply_timeout_effective_seconds": effective_reply_timeout,
        }
        profile = runtime.config.profile(notification.profile_id)
        dry_run = (
            bool(profile.get("dry_run", True))
            if dry_run_override is None
            else bool(dry_run_override)
        )
        event = XiaoheiheMessageEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            runtime=runtime,
            route=notification.route,
            event_id=event_id,
            dry_run=dry_run,
            proactive=proactive,
            capture_candidate=capture_candidate,
            candidate_metadata=candidate_metadata,
            reply_timeout_seconds=effective_reply_timeout,
        )
        event.is_wake = True
        event.is_at_or_wake_command = True
        if permission.map_as_admin:
            event.role = "admin"
        event.set_extra("xiaoheihe_dynamic_context", context.dynamic_context)
        event.set_extra("xiaoheihe_route", notification.route.as_dict())
        fixed_llm_provider_id = str(runtime_config["providers"]["llm_provider_id"]).strip()
        if fixed_llm_provider_id:
            event.set_extra("selected_provider", fixed_llm_provider_id)
        self.commit_event(event)
        try:
            await asyncio.wait_for(
                event.wait_finished(),
                timeout=event.reply_timeout_seconds,
            )
        except TimeoutError:
            await event.expire()

    async def _dispatch_synthetic(
        self, notification: Notification, metadata: dict[str, Any]
    ) -> None:
        runtime = get_runtime()
        if self._context_builder is None:
            return
        event_id = await runtime.repository.claim_event(notification)
        if event_id is None:
            return
        client = await runtime.get_client(notification.profile_id)
        context = await self._context_builder.build(notification, client)
        await runtime.repository.mark_event(event_id, EventState.CONTEXT_READY)
        await runtime.repository.record_session(notification.route)
        await runtime.repository.mark_event(event_id, EventState.DISPATCHED)
        feed_config = runtime.config.snapshot()["proactive_feed"]
        dry_run, review_required = proactive_delivery_settings(feed_config)
        await self._dispatch(
            event_id,
            notification,
            context,
            PermissionDecision(True, "主动帖子合成事件"),
            capture_candidate=review_required,
            candidate_metadata={
                **metadata,
                "post_author_uid": notification.post_author_uid,
            },
            dry_run_override=dry_run,
            proactive=True,
        )

    async def send_by_session(self, session: MessageSession, message_chain: MessageChain) -> None:
        profile_id = str(self.config.get("profile_id", "default"))
        route = RoutingTarget.from_session_id(profile_id, session.session_id)
        text = aggregate_text(message_chain)
        if not text:
            raise ValueError("主动发送消息链不包含文本")
        profile = get_runtime().config.profile(profile_id)
        await get_runtime().deliver(
            event_id=None,
            route=route,
            content=text,
            dry_run=bool(profile.get("dry_run", True)),
        )
        await super().send_by_session(session, message_chain)

    async def terminate(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._refresh.set()
        await self._stop_profile_services()
        self.running = False
