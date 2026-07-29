from __future__ import annotations

import pytest

from tests.helpers import load_fixture
from xiaoheihe.models import LoginState, NotificationType
from xiaoheihe.parsers import (
    ResponseShapeError,
    parse_credentials,
    parse_login_state,
    parse_notifications,
    parse_qr_response,
    parse_send_result,
    parse_thread_context,
)


def test_login_state_matrix() -> None:
    assert parse_login_state({"result": {"state": "waiting"}})[0] is LoginState.WAITING_SCAN
    assert parse_login_state({"result": {"state": "scanned"}})[0] is (
        LoginState.SCANNED_WAITING_CONFIRM
    )
    assert parse_login_state({"result": {"state": "expired"}})[0] is LoginState.EXPIRED
    assert parse_login_state({"result": {"state": "unexpected"}})[0] is (LoginState.WAITING_SCAN)


def test_reference_login_state_error_marker_variants() -> None:
    assert parse_login_state({"status": "ok", "result": {"error": "ok"}})[0] is LoginState.SUCCESS
    assert (
        parse_login_state(
            {
                "status": "ok",
                "result": {"error": "wait", "error_msg": "请在手机端确认登录"},
            }
        )[0]
        is LoginState.WAITING_SCAN
    )
    assert (
        parse_login_state(
            {
                "status": "ok",
                "result": {"error": "wait", "error_msg": "等待扫码"},
            }
        )[0]
        is LoginState.WAITING_SCAN
    )
    assert (
        parse_login_state({"status": "ok", "result": {"error": "ready"}})[0]
        is LoginState.SCANNED_WAITING_CONFIRM
    )


def test_direct_login_credentials_support_current_result_fields() -> None:
    credentials = parse_credentials(
        "default",
        load_fixture("qr_direct_credentials_success.json"),
        {},
        logged_in_at="2026-07-28T00:00:00+00:00",
    )
    assert credentials.uid == "10001"
    assert credentials.nickname == "MockUser"
    assert credentials.cookies == {
        "pkey": "redacted-fixture-pkey",
        "heybox_id": "10001",
    }


def test_fixture_contracts() -> None:
    qr = parse_qr_response("default", load_fixture("qr_response.json"), now=100)
    assert qr.expires_at == 280
    notifications = parse_notifications(
        "default", load_fixture("notifications_mentions.json"), NotificationType.MENTION
    )
    assert notifications.items[0]["notification"].post_id == "30003"
    assert notifications.items[0]["notification"].message_id == ("xhh_mention_notice-1_comment-1")
    thread = parse_thread_context(load_fixture("thread_context.json"), "30003")
    assert thread.author_uid == "40004"
    assert parse_send_result(load_fixture("send_success.json")).confirmed is True


def test_reference_notification_fields_are_normalized() -> None:
    page = parse_notifications(
        "default",
        load_fixture("notifications_reference_messages.json"),
        NotificationType.MENTION,
    )
    notification = page.items[0]["notification"]
    assert notification.external_event_id == "90001"
    assert notification.external_comment_id == "70001"
    assert notification.post_id == "60001"
    assert notification.sender_uid == "50001"
    assert notification.sender_nickname == "FixtureUser"
    assert notification.root_comment_id == "70000"
    assert notification.parent_comment_id == "70001"
    assert notification.content == "@MockBot 请介绍一下这个帖子"
    assert notification.post_author_uid == "40001"


def test_comment_mention_type_17_is_accepted_and_routed_to_floor() -> None:
    page = parse_notifications(
        "default",
        load_fixture("notifications_reference_messages.json"),
        NotificationType.MENTION,
    )
    notification = page.items[0]["notification"]
    assert notification.external_comment_id == "70001"
    assert notification.root_comment_id == "70000"
    assert notification.parent_comment_id == "70001"
    assert notification.route.session_id == "xhh_thread_60001_70000"


def test_post_mention_type_16_is_accepted_and_routed_to_post() -> None:
    page = parse_notifications(
        "default",
        load_fixture("notifications_post_mention.json"),
        NotificationType.MENTION,
    )
    notification = page.items[0]["notification"]
    assert notification.external_event_id == "post-mention-90002"
    assert notification.external_comment_id == "post_message_post-mention-90002"
    assert notification.post_id == "60002"
    assert notification.root_comment_id == ""
    assert notification.parent_comment_id == ""
    assert notification.route.session_id == "xhh_post_60002"
    assert notification.content == "@MockBot 请概括这个帖子"
    assert notification.image_urls == ["https://cdn.example.com/post-mention.png"]


def test_notification_result_array_and_reply_type_filter() -> None:
    result = [
        {
            "message_id": "reply-event",
            "message_type": 2,
            "comment_a_id": "reply-comment",
            "comment_a_text": "回复机器人",
            "root_comment_id": "root-comment",
            "linkid": "post",
            "userid_a": "user",
            "timestamp": 1_800_000_000,
        },
        {
            "message_id": "like-event",
            "message_type": 4,
            "comment_a_id": "like-comment",
            "comment_a_text": "点赞通知",
            "root_comment_id": "root-comment",
            "linkid": "post",
            "userid_a": "user",
            "timestamp": 1_800_000_000,
        },
    ]
    page = parse_notifications(
        "default",
        {"status": "ok", "result": result},
        NotificationType.REPLY,
        page_size=2,
        offset=20,
    )
    assert [item["notification"].external_event_id for item in page.items] == ["reply-event"]
    assert page.has_more is True
    assert page.next_cursor == "22"


def test_reference_rich_post_content_extracts_text_and_images() -> None:
    thread = parse_thread_context(
        {
            "status": "ok",
            "result": {
                "link": {
                    "title": "Rich post",
                    "text": (
                        '[{"type":"text","text":"第一段"},'
                        '{"type":"html","text":"<b>第二段</b>"},'
                        '{"type":"image","url":"https://cdn.example.com/post.png"}]'
                    ),
                    "user": {"userid": "author", "username": "Author"},
                }
            },
        },
        "post",
    )
    assert thread.body == "第一段\n<b>第二段</b>"
    assert thread.image_urls == ["https://cdn.example.com/post.png"]


def test_send_result_accepts_reference_top_level_comment_id() -> None:
    result = parse_send_result({"status": "ok", "commentid": "sent-reference"})
    assert result.external_comment_id == "sent-reference"
    assert parse_send_result({"status": "ok"}).external_comment_id == ""
    with pytest.raises(ResponseShapeError, match="成功状态"):
        parse_send_result({"status": "failed", "msg": "rejected"})


def test_response_shape_change_is_explicit() -> None:
    with pytest.raises(ResponseShapeError, match="缺少"):
        parse_qr_response("default", {"result": {}}, now=100)
    with pytest.raises(ResponseShapeError, match="稳定"):
        parse_notifications(
            "default",
            {"result": {"items": [{"comment": {}}]}},
            NotificationType.MENTION,
        )
    with pytest.raises(ResponseShapeError, match="发送者 UID"):
        parse_notifications(
            "default",
            {
                "result": {
                    "items": [
                        {
                            "id": "event",
                            "post_id": "post",
                            "comment": {"id": "comment", "content": "hello"},
                        }
                    ]
                }
            },
            NotificationType.MENTION,
        )


def test_millisecond_notification_timestamp_is_normalized() -> None:
    page = parse_notifications(
        "default",
        {
            "result": {
                "items": [
                    {
                        "id": "event",
                        "post_id": "post",
                        "created_at": 1_700_000_000_000,
                        "sender": {"uid": "user"},
                        "comment": {"id": "comment", "content": "hello"},
                    }
                ]
            }
        },
        NotificationType.MENTION,
    )
    assert page.items[0]["notification"].created_at == 1_700_000_000
