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
