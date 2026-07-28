from __future__ import annotations

from xiaoheihe.models import Notification, NotificationType
from xiaoheihe.permission_service import PermissionService


def notification(uid="user", content="hello", author="author") -> Notification:
    return Notification(
        profile_id="default",
        external_event_id="e",
        external_comment_id="c",
        notification_id="n",
        event_type=NotificationType.MENTION,
        sender_uid=uid,
        sender_nickname="name",
        post_id="p",
        root_comment_id="r",
        parent_comment_id="r",
        content=content,
        created_at=1,
        post_author_uid=author,
    )


def service(**overrides) -> PermissionService:
    config = {
        "whitelist_mode": False,
        "user_whitelist": [],
        "user_blacklist": [],
        "author_whitelist": [],
        "author_blacklist": [],
        "keyword_blacklist": [],
        "map_owner_to_astrbot_admin": False,
    }
    config.update(overrides)
    return PermissionService(
        config,
        owner_uid="owner",
        self_uid="bot",
        only_explicit_mentions=True,
        reply_to_direct_replies=True,
    )


def test_priority_self_blacklist_owner_whitelist() -> None:
    policy = service(
        whitelist_mode=True,
        user_whitelist=["allowed"],
        user_blacklist=["blocked"],
    )
    assert policy.decide(notification("bot")).reason == "机器人自身消息"
    assert policy.decide(notification("blocked")).reason == "用户 UID 黑名单"
    assert policy.decide(notification("owner")).allowed is True
    assert policy.decide(notification("outsider")).allowed is False
    assert policy.decide(notification("allowed")).allowed is True


def test_owner_admin_mapping_defaults_off() -> None:
    decision = service().decide(notification("owner"))
    assert decision.is_owner is True
    assert decision.map_as_admin is False


def test_keyword_and_author_filters() -> None:
    policy = service(keyword_blacklist=["spam"], author_blacklist=["bad-author"])
    assert policy.decide(notification(content="SPAM here")).allowed is False
    assert policy.decide(notification(author="bad-author")).allowed is False
