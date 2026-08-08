from __future__ import annotations

import asyncio
import time

from tests.helpers import load_fixture
from xiaoheihe.context_builder import ContextBuilder
from xiaoheihe.models import Notification, NotificationType, ThreadContext
from xiaoheihe.parsers import parse_notifications
from xiaoheihe.security import clean_untrusted_text


class FakeClient:
    calls = 0

    async def fetch_thread_context(
        self, post_id: str, *, root_comment_id: str = "", post_context=None
    ):
        from xiaoheihe.parsers import parse_thread_context

        self.calls += 1
        return parse_thread_context(load_fixture("thread_context.json"), post_id)


async def public_resolver(hostname: str) -> set[str]:
    return {"203.0.113.10"}


async def test_context_is_clean_bounded_and_cached() -> None:
    page = parse_notifications(
        "default", load_fixture("notifications_mentions.json"), NotificationType.MENTION
    )
    notification = page.items[0]["notification"]
    client = FakeClient()
    builder = ContextBuilder(max_images=3, host_resolver=public_resolver)
    result = await builder.build(notification, client, bot_name="Robot")
    result_again = await builder.build(notification, client, bot_name="Robot")
    assert result.user_text == "你好"
    assert '<xiaoheihe_context trust="untrusted">' in result.dynamic_context
    assert "帖子正文" in result.dynamic_context
    assert "第一条" in result.dynamic_context
    assert len(result.image_urls) == 2
    assert client.calls == 1
    assert '<xiaoheihe_runtime_metadata trust="trusted">' in result.dynamic_context
    assert "作者发帖时间:" in result.dynamic_context
    assert "本轮触发内容发布时间:" in result.dynamic_context
    assert "触发评论发布时间:" in result.dynamic_context
    assert "插件发现并读取时间:" in result.dynamic_context
    assert "AI 开始生成回复时间:" in result.dynamic_context
    assert f"当前触发发言人 UID: {notification.sender_uid}" in result.dynamic_context
    assert "不得因共享会话历史把不同 UID 当成同一人" in result.dynamic_context
    assert "不得把系统处理时间归因给作者" in result.dynamic_context
    assert result_again.thread.post_id == result.thread.post_id


async def test_context_cache_singleflights_same_floor_misses() -> None:
    notification = Notification(
        profile_id="default",
        external_event_id="singleflight",
        external_comment_id="comment-current",
        notification_id="singleflight",
        event_type=NotificationType.REPLY,
        sender_uid="speaker",
        sender_nickname="当前用户",
        post_id="post-singleflight",
        root_comment_id="root-singleflight",
        parent_comment_id="root-singleflight",
        content="并发消息",
        created_at=1_800_000_000,
    )

    class SlowClient:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_thread_context(
            self, post_id: str, *, root_comment_id: str = "", post_context=None
        ):
            self.calls += 1
            await asyncio.sleep(0.02)
            return ThreadContext(post_id, "标题", "正文", "author", "楼主", [])

    client = SlowClient()
    builder = ContextBuilder(host_resolver=public_resolver)
    results = await asyncio.gather(*(builder.build(notification, client) for _ in range(6)))

    assert client.calls == 1
    assert all(result.thread.post_id == "post-singleflight" for result in results)
    await builder.clear()


async def test_active_floor_cache_refreshes_for_notification_observed_after_fetch() -> None:
    base = time.time()

    def make_notification(identifier: str, observed_at: float) -> Notification:
        return Notification(
            profile_id="default",
            external_event_id=identifier,
            external_comment_id=f"comment-{identifier}",
            notification_id=identifier,
            event_type=NotificationType.REPLY,
            sender_uid="speaker",
            sender_nickname="用户",
            post_id="post-refresh",
            root_comment_id="root-refresh",
            parent_comment_id="root-refresh",
            content=identifier,
            created_at=base,
            observed_at=observed_at,
        )

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_thread_context(
            self, post_id: str, *, root_comment_id: str = "", post_context=None
        ):
            self.calls += 1
            return ThreadContext(post_id, "标题", "正文", "author", "楼主", [])

    client = Client()
    builder = ContextBuilder(cache_ttl_seconds=300, host_resolver=public_resolver)
    await builder.build(make_notification("first", base), client)
    await builder.build(make_notification("second", base + 60), client)

    assert client.calls == 2


async def test_image_host_resolution_is_reused_within_one_event() -> None:
    calls: list[str] = []

    async def counting_resolver(hostname: str) -> set[str]:
        calls.append(hostname)
        return {"203.0.113.10"}

    builder = ContextBuilder(max_images=4, host_resolver=counting_resolver)
    urls, sources, warnings = await builder._collect_images(
        [
            ("https://cdn.example.com/a.png", "current_comment"),
            ("https://cdn.example.com/b.png", "original_post"),
            ("https://cdn.example.com/a.png", "original_post"),
        ]
    )

    assert urls == ["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"]
    assert sources == ["current_comment", "original_post"]
    assert warnings == []
    assert calls == ["cdn.example.com"]


async def test_comment_mention_includes_comment_and_original_post_media() -> None:
    page = parse_notifications(
        "default",
        {
            "status": "ok",
            "result": {
                "messages": [
                    {
                        "message_id": "mention-with-post",
                        "message_type": 17,
                        "comment_a_id": "comment-1",
                        "comment_a_text": "@Robot 看看评论和原帖",
                        "comment_a_images": [
                            "https://cdn.example.com/comment-1.png",
                            "https://cdn.example.com/comment-2.png",
                        ],
                        "root_comment_id": "root-1",
                        "linkid": "post-1",
                        "userid_a": "user-1",
                        "user_a": {"userid": "user-1", "username": "用户"},
                        "link": {
                            "linkid": "post-1",
                            "title": "通知内原帖标题",
                            "text": (
                                '[{"type":"text","text":"通知内原帖正文"},'
                                '{"type":"image","url":"https://cdn.example.com/post-1.png"},'
                                '{"type":"image","url":"https://cdn.example.com/post-2.png"}]'
                            ),
                            "user": {"userid": "author-1", "username": "作者"},
                        },
                    }
                ]
            },
        },
        NotificationType.MENTION,
    )

    class CommentTreeOnlyClient:
        async def fetch_thread_context(
            self, post_id: str, *, root_comment_id: str = "", post_context=None
        ):
            return ThreadContext(
                post_id=post_id,
                title="",
                body="",
                author_uid="",
                author_name="",
                comments=[{"content": "楼层文本", "user": {"userid": "user-1"}}],
                image_urls=[],
            )

    result = await ContextBuilder(max_images=2, host_resolver=public_resolver).build(
        page.items[0]["notification"],
        CommentTreeOnlyClient(),
        bot_name="Robot",
    )

    assert result.user_text == "看看评论和原帖"
    assert "通知内原帖标题" in result.dynamic_context
    assert "通知内原帖正文" in result.dynamic_context
    assert "当前评论图片: 2 张" in result.dynamic_context
    assert "原帖图片: 2 张" in result.dynamic_context
    assert result.image_urls == [
        "https://cdn.example.com/comment-1.png",
        "https://cdn.example.com/post-1.png",
    ]
    assert result.image_sources == ["current_comment", "original_post"]


async def test_proactive_context_strictly_separates_author_and_system_times(
    monkeypatch,
) -> None:
    post_created_at = 1_754_035_200  # 2025-08-01 16:00:00+08:00
    observed_at = post_created_at + 11 * 3600 + 20 * 60
    reply_started_at = observed_at + 60
    monkeypatch.setattr("xiaoheihe.context_builder.time.time", lambda: reply_started_at)

    notification = Notification(
        profile_id="default",
        external_event_id="feed:post-time",
        external_comment_id="feed:post-time",
        notification_id="feed:post-time",
        event_type=NotificationType.PROACTIVE_FEED,
        sender_uid="author",
        sender_nickname="作者",
        post_id="post-time",
        root_comment_id="",
        parent_comment_id="",
        content="教程正文",
        created_at=post_created_at,
        observed_at=observed_at,
    )

    class TimedClient:
        async def fetch_thread_context(
            self, post_id: str, *, root_comment_id: str = "", post_context=None
        ):
            return ThreadContext(
                post_id=post_id,
                title="教程",
                body="正文",
                author_uid="author",
                author_name="作者",
                comments=[
                    {
                        "content": "历史评论",
                        "created_at": post_created_at + 3600,
                        "user": {"uid": "commenter", "nickname": "评论者"},
                    }
                ],
                post_created_at=post_created_at,
            )

    result = await ContextBuilder(host_resolver=public_resolver).build(notification, TimedClient())

    assert "插件主动浏览推荐流（没有作者新评论触发）" in result.dynamic_context
    assert "触发评论发布时间: 不适用（本轮没有作者评论触发）" in result.dynamic_context
    assert "作者发帖时间: 2025-08-01T16:00:00+08:00" in result.dynamic_context
    assert "插件发现并读取时间: 2025-08-02T03:20:00+08:00" in result.dynamic_context
    assert "AI 开始生成回复时间: 2025-08-02T03:21:00+08:00" in result.dynamic_context
    assert "帖子在插件读取时已发布: 11小时20分钟" in result.dynamic_context
    assert "1. [2025-08-01T17:00:00+08:00] 评论者" in result.dynamic_context


async def test_thread_reply_focus_bounds_post_and_recent_comments_around_direct_target() -> None:
    post_body = " ".join(f"post-{index:04d}" for index in range(500))
    comments = [
        {
            "id": f"comment-{index:02d}",
            "content": f"topic-{index:02d}",
            "user": {"userid": f"user-{index:02d}", "nickname": f"用户{index:02d}"},
        }
        for index in range(20)
    ]
    comments.append(
        {
            "id": "comment-current",
            "content": "电影第二部到底怎么样？",
            "user": {"userid": "speaker", "nickname": "当前用户"},
        }
    )
    notification = Notification(
        profile_id="default",
        external_event_id="reply-focus",
        external_comment_id="comment-current",
        notification_id="reply-focus",
        event_type=NotificationType.REPLY,
        sender_uid="speaker",
        sender_nickname="当前用户",
        post_id="post-focus",
        root_comment_id="comment-00",
        parent_comment_id="comment-current",
        content="电影第二部到底怎么样？",
        created_at=1_800_000_000,
        raw={
            "comment_b_id": "comment-03",
            "comment_b_text": "我们已经歪楼聊到电影了",
            "user_b": {"userid": "movie-user", "username": "电影党"},
        },
    )

    class FocusClient:
        async def fetch_thread_context(
            self, post_id: str, *, root_comment_id: str = "", post_context=None
        ):
            return ThreadContext(
                post_id=post_id,
                title="原帖是显卡讨论",
                body=post_body,
                author_uid="author",
                author_name="楼主",
                comments=comments,
            )

    result = await ContextBuilder(
        max_post_chars=6000,
        max_thread_comments=40,
        thread_reply_post_chars=220,
        thread_reply_recent_comments=3,
        host_resolver=public_resolver,
    ).build(notification, FocusClient())

    assert '<xiaoheihe_reply_focus trust="trusted" mode="thread_reply">' in (result.dynamic_context)
    assert "原帖背景（低相关性" in result.dynamic_context
    assert "当前消息直接回复对象（高相关性）" in result.dynamic_context
    assert "评论 comment-03，电影党 (UID movie-user): 我们已经歪楼聊到电影了" in (
        result.dynamic_context
    )
    assert "topic-17" in result.dynamic_context
    assert "topic-18" in result.dynamic_context
    assert "topic-19" in result.dynamic_context
    assert "topic-16" not in result.dynamic_context
    assert result.dynamic_context.count("电影第二部到底怎么样？") == 1
    assert "post-0000" in result.dynamic_context
    assert "post-0100" not in result.dynamic_context
    assert "不得为了迎合原帖而强行建立关联" in result.dynamic_context
    assert result.compression_source is not None
    assert "post-0100" in result.compression_source.post_body
    assert "topic-00" in result.compression_source.recent_comments
    assert "topic-16" in result.compression_source.recent_comments
    assert "用户00 (UID user-00)" in result.compression_source.recent_participants
    assert "用户19 (UID user-19)" in result.compression_source.recent_participants
    assert "用户03 (UID user-03)" not in result.compression_source.recent_participants
    assert "当前用户 (UID speaker)" not in result.compression_source.recent_participants
    assert result.compression_source.current_message == "电影第二部到底怎么样？"
    assert result.compression_source.reply_target.endswith("我们已经歪楼聊到电影了")


async def test_proactive_feed_keeps_full_post_budget_and_post_focus() -> None:
    post_body = " ".join(f"feed-{index:04d}" for index in range(220))
    notification = Notification(
        profile_id="default",
        external_event_id="feed-focus",
        external_comment_id="feed-focus",
        notification_id="feed-focus",
        event_type=NotificationType.PROACTIVE_FEED,
        sender_uid="author",
        sender_nickname="楼主",
        post_id="post-feed-focus",
        root_comment_id="",
        parent_comment_id="",
        content="主动浏览帖子",
        created_at=1_800_000_000,
        image_urls=["https://cdn.example.com/feed-focus.png"],
    )

    class FeedFocusClient:
        async def fetch_thread_context(
            self, post_id: str, *, root_comment_id: str = "", post_context=None
        ):
            return ThreadContext(
                post_id=post_id,
                title="主动帖子",
                body=post_body,
                author_uid="author",
                author_name="楼主",
                comments=[
                    {
                        "id": f"feed-comment-{index}",
                        "content": f"feed-topic-{index}",
                        "user": {"userid": f"user-{index}", "nickname": f"用户{index}"},
                    }
                    for index in range(5)
                ],
            )

    result = await ContextBuilder(
        max_post_chars=6000,
        max_thread_comments=40,
        thread_reply_post_chars=200,
        thread_reply_recent_comments=1,
        host_resolver=public_resolver,
    ).build(notification, FeedFocusClient())

    assert '<xiaoheihe_reply_focus trust="trusted" mode="proactive_feed">' in (
        result.dynamic_context
    )
    assert "原帖主题（主要背景）" in result.dynamic_context
    assert "feed-0219" in result.dynamic_context
    assert "feed-topic-0" in result.dynamic_context
    assert "feed-topic-4" in result.dynamic_context
    assert "原帖标题、正文和原帖图片是本轮主要话题" in result.dynamic_context
    assert result.compression_source is None
    assert result.image_sources == ["original_post"]


async def test_thread_compression_input_stays_hard_bounded_beyond_fallback_window() -> None:
    post_body = " ".join(f"body-{index:05d}" for index in range(6000))
    comments = [
        {
            "id": f"comment-{index:02d}",
            "content": " ".join(f"topic-{index:02d}-{part:03d}" for part in range(80)),
            "user": {"userid": f"user-{index:02d}", "nickname": f"用户{index:02d}"},
        }
        for index in range(60)
    ]
    notification = Notification(
        profile_id="default",
        external_event_id="compression-bounds",
        external_comment_id="current-comment",
        notification_id="compression-bounds",
        event_type=NotificationType.REPLY,
        sender_uid="speaker",
        sender_nickname="当前用户",
        post_id="post-bounds",
        root_comment_id="root-comment",
        parent_comment_id="",
        content="当前独立话题",
        created_at=1_800_000_000,
    )

    class LargeThreadClient:
        async def fetch_thread_context(
            self, post_id: str, *, root_comment_id: str = "", post_context=None
        ):
            return ThreadContext(
                post_id=post_id,
                title="超长原帖",
                body=post_body,
                author_uid="author",
                author_name="楼主",
                comments=comments,
            )

    result = await ContextBuilder(
        max_post_chars=50_000,
        max_thread_comments=200,
        host_resolver=public_resolver,
    ).build(notification, LargeThreadClient())

    assert result.compression_source is not None
    assert 1600 < len(result.compression_source.post_body) <= 8000
    assert len(result.compression_source.recent_comments) <= 8000
    assert "topic-59" in result.compression_source.recent_comments
    assert "topic-00" not in result.compression_source.recent_comments
    assert "body-00500" in result.compression_source.post_body
    assert "body-00500" not in result.community_context


def test_html_emoji_duplicate_and_tracking_cleanup() -> None:
    value = "<b>Hello</b>\nHello\n\n\n[表情:笑] https://x.test/a?utm_source=x&id=2"
    cleaned = clean_untrusted_text(value)
    assert "<b>" not in cleaned
    assert "[表情" not in cleaned
    assert "utm_source" not in cleaned
    assert cleaned.count("Hello") == 1


async def test_image_visual_degradation_is_nonfatal() -> None:
    page = parse_notifications(
        "default", load_fixture("notifications_mentions.json"), NotificationType.MENTION
    )
    notification = page.items[0]["notification"]
    notification.image_urls.extend(["http://insecure.example/a.png", "https://127.0.0.1/a.png"])
    result = await ContextBuilder(max_images=6, host_resolver=public_resolver).build(
        notification, FakeClient()
    )
    assert len(result.image_urls) == 2
    assert result.warnings


async def test_dns_rebinding_to_private_address_is_nonfatal() -> None:
    page = parse_notifications(
        "default", load_fixture("notifications_mentions.json"), NotificationType.MENTION
    )

    async def private_resolver(hostname: str) -> set[str]:
        from xiaoheihe.security import SecurityError

        raise SecurityError("private DNS result")

    result = await ContextBuilder(max_images=6, host_resolver=private_resolver).build(
        page.items[0]["notification"], FakeClient()
    )
    assert result.image_urls == []
    assert any("private DNS result" in warning for warning in result.warnings)
