from __future__ import annotations

import asyncio
from pathlib import Path

from tests.astrbot_stubs import (
    REGISTERED_ADAPTERS,
    MessageChain,
    MessageSession,
    MessageType,
    Plain,
)
from xiaoheihe.adapter import (
    XiaoheihePlatformAdapter,
    effective_reply_timeout_seconds,
    proactive_delivery_settings,
)
from xiaoheihe.context_builder import BuiltContext
from xiaoheihe.event import XiaoheiheMessageEvent
from xiaoheihe.models import (
    Credentials,
    Notification,
    NotificationType,
    PermissionDecision,
    ThreadContext,
)
from xiaoheihe.runtime import bind_runtime, unbind_runtime


class FakeConfigService:
    def __init__(
        self,
        llm_provider_id: str = "",
        *,
        proactive_dry_run: bool = True,
        proactive_review_required: bool = True,
    ) -> None:
        self.llm_provider_id = llm_provider_id
        self.proactive_dry_run = proactive_dry_run
        self.proactive_review_required = proactive_review_required

    def profile(self, profile_id):
        return {"profile_id": profile_id, "dry_run": True}

    def snapshot(self):
        return {
            "providers": {"llm_provider_id": self.llm_provider_id},
            "context": {
                "enable_image_understanding": True,
                "image_timeout_seconds": 15,
            },
            "reply": {"reply_timeout_seconds": 120},
            "proactive_feed": {
                "dry_run": self.proactive_dry_run,
                "review_required": self.proactive_review_required,
            },
        }


class FakeCredentials:
    def load(self, profile_id):
        return Credentials(
            profile_id=profile_id,
            uid="bot-uid",
            nickname="bot",
            cookies={"pkey": "test"},
        )


class FakeRuntime:
    def __init__(
        self,
        llm_provider_id: str = "",
        *,
        proactive_dry_run: bool = True,
        proactive_review_required: bool = True,
    ) -> None:
        self.config = FakeConfigService(
            llm_provider_id,
            proactive_dry_run=proactive_dry_run,
            proactive_review_required=proactive_review_required,
        )
        self.credentials = FakeCredentials()
        self.deliveries = []

    async def deliver(self, **kwargs):
        self.deliveries.append(kwargs)


def test_adapter_is_registered_and_nonstreaming() -> None:
    assert REGISTERED_ADAPTERS["xiaoheihe"] is XiaoheihePlatformAdapter
    registration = XiaoheihePlatformAdapter._test_registration
    assert registration["support_streaming_message"] is False
    assert registration["default_config_tmpl"]["profile_id"] == "default"
    assert registration["logo_path"] == "../logo.png"
    assert (Path("xiaoheihe") / registration["logo_path"]).resolve() == Path("logo.png").resolve()
    assert (Path("xiaoheihe") / registration["logo_path"]).is_file()


def test_image_events_receive_bounded_vision_processing_grace() -> None:
    assert (
        effective_reply_timeout_seconds(
            base_timeout_seconds=120,
            image_count=0,
            image_timeout_seconds=15,
        )
        == 120
    )
    assert (
        effective_reply_timeout_seconds(
            base_timeout_seconds=120,
            image_count=6,
            image_timeout_seconds=15,
        )
        == 300
    )
    assert (
        effective_reply_timeout_seconds(
            base_timeout_seconds=600,
            image_count=6,
            image_timeout_seconds=120,
        )
        == 900
    )


def test_proactive_delivery_settings_keep_dry_run_and_review_independent() -> None:
    assert proactive_delivery_settings({}) == (True, True)
    assert proactive_delivery_settings({"dry_run": True, "review_required": False}) == (
        True,
        False,
    )
    assert proactive_delivery_settings({"dry_run": False, "review_required": True}) == (
        False,
        True,
    )
    assert proactive_delivery_settings({"dry_run": False, "review_required": False}) == (
        False,
        False,
    )


async def test_send_by_session_recovers_exact_route() -> None:
    runtime = FakeRuntime()
    bind_runtime(runtime)
    adapter = XiaoheihePlatformAdapter(
        {"id": "xhh-1", "profile_id": "default"},
        {},
        asyncio.Queue(),
    )
    session = MessageSession(
        platform_name="xhh-1",
        message_type=MessageType.GROUP_MESSAGE,
        session_id="xhh_thread_123_456",
    )
    await adapter.send_by_session(session, MessageChain([Plain("hello")]))
    route = runtime.deliveries[0]["route"]
    assert route.post_id == "123"
    assert route.root_comment_id == "456"
    assert adapter.super_send_count == 1
    unbind_runtime(runtime)


async def test_dispatch_sets_fixed_llm_provider_before_commit(monkeypatch) -> None:
    async def finish_immediately(self):
        return None

    monkeypatch.setattr(XiaoheiheMessageEvent, "wait_finished", finish_immediately)
    runtime = FakeRuntime("provider-fixed")
    bind_runtime(runtime)
    queue = asyncio.Queue()
    adapter = XiaoheihePlatformAdapter(
        {"id": "xhh-1", "profile_id": "default"},
        {},
        queue,
    )
    notification = Notification(
        profile_id="default",
        external_event_id="event-1",
        external_comment_id="comment-1",
        notification_id="notification-1",
        event_type=NotificationType.MENTION,
        sender_uid="user-1",
        sender_nickname="user",
        post_id="post-1",
        root_comment_id="",
        parent_comment_id="",
        content="测试",
        created_at=123.0,
    )
    context = BuiltContext(
        user_text="测试",
        dynamic_context="背景",
        image_urls=[],
        warnings=[],
        thread=ThreadContext(
            post_id="post-1",
            title="标题",
            body="正文",
            author_uid="author-1",
            author_name="author",
            comments=[],
        ),
    )
    try:
        await adapter._dispatch(
            1,
            notification,
            context,
            PermissionDecision(True, "测试"),
            dry_run_override=False,
            proactive=True,
        )
        event = queue.get_nowait()
        assert event.get_extra("selected_provider") == "provider-fixed"
        assert event.message_obj.timestamp == 123
        assert event.dry_run is False
        assert event.proactive is True
    finally:
        unbind_runtime(runtime)


async def test_synthetic_dispatch_routes_unreviewed_mode_to_real_delivery() -> None:
    runtime = FakeRuntime(
        proactive_dry_run=False,
        proactive_review_required=False,
    )

    class Repository:
        async def claim_event(self, notification):
            return 7

        async def mark_event(self, *args):
            return None

        async def record_session(self, route):
            return None

    async def get_client(profile_id):
        return object()

    runtime.repository = Repository()
    runtime.get_client = get_client
    bind_runtime(runtime)
    queue = asyncio.Queue()
    adapter = XiaoheihePlatformAdapter(
        {"id": "xhh-1", "profile_id": "default"},
        {},
        queue,
    )
    context = BuiltContext(
        user_text="主动内容",
        dynamic_context="背景",
        image_urls=[],
        warnings=[],
        thread=ThreadContext(
            post_id="post-direct",
            title="标题",
            body="正文",
            author_uid="author-1",
            author_name="author",
            comments=[],
        ),
    )

    class ContextBuilder:
        async def build(self, notification, client):
            return context

    adapter._context_builder = ContextBuilder()
    captured = {}

    async def capture_dispatch(*args, **kwargs):
        captured.update(kwargs)

    adapter._dispatch = capture_dispatch
    notification = Notification(
        profile_id="default",
        external_event_id="feed:post-direct",
        external_comment_id="feed:post-direct",
        notification_id="feed:post-direct",
        event_type=NotificationType.PROACTIVE_FEED,
        sender_uid="author-1",
        sender_nickname="author",
        post_id="post-direct",
        root_comment_id="",
        parent_comment_id="",
        content="主动内容",
        created_at=123.0,
        post_author_uid="author-1",
    )
    try:
        await adapter._dispatch_synthetic(notification, {"post_title": "标题"})
        assert captured["capture_candidate"] is False
        assert captured["dry_run_override"] is False
        assert captured["proactive"] is True
        assert captured["candidate_metadata"]["post_author_uid"] == "author-1"
    finally:
        unbind_runtime(runtime)


def test_adapter_meta_uses_instance_id() -> None:
    adapter = XiaoheihePlatformAdapter(
        {"id": "instance-a", "profile_id": "default"},
        {},
        asyncio.Queue(),
    )
    assert adapter.meta().name == "xiaoheihe"
    assert adapter.meta().id == "instance-a"
    assert adapter.meta().logo_path == "../logo.png"
    assert adapter.meta().support_streaming_message is False
