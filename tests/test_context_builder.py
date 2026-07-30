from __future__ import annotations

from tests.helpers import load_fixture
from xiaoheihe.context_builder import ContextBuilder
from xiaoheihe.models import NotificationType, ThreadContext
from xiaoheihe.parsers import parse_notifications
from xiaoheihe.security import clean_untrusted_text


class FakeClient:
    calls = 0

    async def fetch_thread_context(self, post_id: str, *, root_comment_id: str = ""):
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
    assert result_again.dynamic_context == result.dynamic_context


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
        async def fetch_thread_context(self, post_id: str, *, root_comment_id: str = ""):
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
