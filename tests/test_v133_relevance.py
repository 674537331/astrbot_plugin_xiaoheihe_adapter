from __future__ import annotations

import asyncio

from xiaoheihe.adapter import effective_reply_timeout_seconds
from xiaoheihe.context_builder import ContextBuilder
from xiaoheihe.context_compression import (
    ThreadCompressionResult,
    ThreadCompressionSource,
    render_compressed_thread_context,
)
from xiaoheihe.context_relevance import (
    detect_explicit_original_post_reference,
    image_source_priority,
    should_preserve_original_post,
)
from xiaoheihe.models import Notification, NotificationType, ThreadContext


async def _public_resolver(_hostname: str) -> set[str]:
    return {"203.0.113.10"}


def test_relevance_defaults_fail_open_and_explicit_reference_restores_post() -> None:
    assert should_preserve_original_post("related") is True
    assert should_preserve_original_post("partial") is True
    assert should_preserve_original_post("unclear") is True
    assert should_preserve_original_post("broken-value") is True
    assert should_preserve_original_post("drifted") is False
    assert should_preserve_original_post("drifted", explicit_post_reference=True) is True
    assert detect_explicit_original_post_reference("回到原帖那张图") is True
    assert detect_explicit_original_post_reference("这个帖子楼主说的") is True
    assert detect_explicit_original_post_reference("求原图") is False
    assert detect_explicit_original_post_reference("这图是真的假的") is False


def test_image_source_distance_order_is_stable() -> None:
    sources = ["original_post", "thread_anchor", "direct_reply_target", "current_comment"]
    assert sorted(sources, key=image_source_priority) == [
        "current_comment",
        "direct_reply_target",
        "thread_anchor",
        "original_post",
    ]


def test_drifted_compressed_context_really_omits_post_payload() -> None:
    source = ThreadCompressionSource(
        post_id="post-1",
        post_author="楼主 (UID author)",
        post_title="原帖标题-不应出现",
        post_body="原帖正文-不应出现",
        recent_comments="楼层已经聊到别的话题",
        reply_target="评论 target，用户 (UID u): 图灵测试怎么做",
        current_sender="当前用户 (UID current)",
        current_message="具体怎么测试？",
        recent_participants=("用户 (UID u)",),
        post_image_caption="原帖图片描述-不应出现",
    )
    result = ThreadCompressionResult(
        post_summary="原帖摘要-不应出现",
        thread_summary="楼层正在讨论图灵测试",
        thread_items=(("用户 (UID u)", "询问图灵测试"),),
        local_topic="图灵测试",
        relation_to_post="drifted",
        post_image_summary="原帖图片摘要-不应出现",
    )
    rendered = render_compressed_thread_context(source, result, preserve_original_post=False)
    assert "原帖标题-不应出现" not in rendered
    assert "原帖摘要-不应出现" not in rendered
    assert "原帖图片摘要-不应出现" not in rendered
    assert "楼层正在讨论图灵测试" in rendered
    assert "具体怎么测试？" in rendered
    assert "已明显偏离原帖" not in rendered
    assert "歪楼" not in rendered
    assert "省略原帖文字和原帖图片摘要" not in rendered


def test_context_budget_is_reserved_separately_from_main_and_fallback() -> None:
    assert (
        effective_reply_timeout_seconds(
            base_timeout_seconds=120,
            image_count=0,
            image_timeout_seconds=15,
            provider_fallback_grace_seconds=60,
            context_timeout_seconds=30,
        )
        == 210
    )


def test_context_builder_prefers_current_target_anchor_before_post_images() -> None:
    notification = Notification(
        profile_id="default",
        external_event_id="event-current",
        external_comment_id="current",
        notification_id="event-current",
        event_type=NotificationType.REPLY,
        sender_uid="current-user",
        sender_nickname="当前用户",
        post_id="post-1",
        root_comment_id="root",
        parent_comment_id="current",
        content="这张图和上面那张有什么区别",
        created_at=1_800_000_000,
        image_urls=["https://img.example/current.png"],
        raw={"comment_b_id": "target"},
    )

    class Client:
        async def fetch_thread_context(
            self, post_id: str, *, root_comment_id: str = "", post_context=None
        ) -> ThreadContext:
            return ThreadContext(
                post_id=post_id,
                title="原帖",
                body="正文",
                author_uid="author",
                author_name="楼主",
                comments=[
                    {
                        "id": "root",
                        "user": {"uid": "root-user", "nickname": "根评论用户"},
                        "content": "根评论",
                        "images": ["https://img.example/root.png"],
                    },
                    {
                        "id": "target",
                        "user": {"uid": "target-user", "nickname": "被回复用户"},
                        "content": "被回复评论",
                        "images": ["https://img.example/target.png"],
                    },
                ],
                image_urls=["https://img.example/post.png"],
            )

    context = asyncio.run(
        ContextBuilder(max_images=4, host_resolver=_public_resolver).build(notification, Client())
    )
    assert context.image_sources == [
        "current_comment",
        "direct_reply_target",
        "thread_anchor",
        "original_post",
    ]
    assert [item.owner_uid for item in context.image_attributions] == [
        "current-user",
        "target-user",
        "root-user",
        "author",
    ]


def test_reply_target_image_falls_back_to_notification_media_when_tree_omits_it() -> None:
    notification = Notification(
        profile_id="default",
        external_event_id="event-target-media",
        external_comment_id="current",
        notification_id="event-target-media",
        event_type=NotificationType.REPLY,
        sender_uid="current-user",
        sender_nickname="当前用户",
        post_id="post-1",
        root_comment_id="root",
        parent_comment_id="current",
        content="这张图是真的假的？",
        created_at=1_800_000_000,
        raw={
            "comment_b_id": "target",
            "comment_b": {
                "id": "target",
                "images": ["https://img.example/target-from-notification.png"],
            },
            "user_b": {"uid": "target-user", "nickname": "被回复用户"},
        },
    )

    class Client:
        async def fetch_thread_context(
            self, post_id: str, *, root_comment_id: str = "", post_context=None
        ) -> ThreadContext:
            return ThreadContext(
                post_id=post_id,
                title="原帖",
                body="正文",
                author_uid="author",
                author_name="楼主",
                comments=[
                    {
                        "id": "root",
                        "user": {"uid": "root-user", "nickname": "根评论用户"},
                        "content": "根评论",
                    },
                    {
                        "id": "target",
                        "user": {"uid": "target-user", "nickname": "被回复用户"},
                        "content": "楼层树里有文字但漏了图片字段",
                    },
                ],
                image_urls=["https://img.example/post.png"],
            )

    context = asyncio.run(
        ContextBuilder(max_images=3, host_resolver=_public_resolver).build(notification, Client())
    )
    assert context.image_urls == [
        "https://img.example/target-from-notification.png",
        "https://img.example/post.png",
    ]
    assert context.image_sources == ["direct_reply_target", "original_post"]
    assert context.image_attributions[0].owner_uid == "target-user"
    assert context.image_attributions[0].owner_role == "direct_reply_target"
