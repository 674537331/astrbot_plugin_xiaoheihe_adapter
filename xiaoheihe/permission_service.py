from __future__ import annotations

from .models import Notification, PermissionDecision


class PermissionService:
    def __init__(
        self,
        permissions: dict,
        *,
        owner_uid: str,
        self_uid: str,
        only_explicit_mentions: bool,
        reply_to_direct_replies: bool,
    ) -> None:
        self.owner_uid = str(owner_uid or "")
        self.self_uid = str(self_uid or "")
        self.whitelist_mode = bool(permissions.get("whitelist_mode", False))
        self.user_whitelist = _uid_set(permissions.get("user_whitelist", []))
        self.user_blacklist = _uid_set(permissions.get("user_blacklist", []))
        self.author_whitelist = _uid_set(permissions.get("author_whitelist", []))
        self.author_blacklist = _uid_set(permissions.get("author_blacklist", []))
        self.keyword_blacklist = tuple(
            str(item).casefold()
            for item in permissions.get("keyword_blacklist", [])
            if str(item).strip()
        )
        self.map_owner_to_admin = bool(permissions.get("map_owner_to_astrbot_admin", False))
        self.only_explicit_mentions = only_explicit_mentions
        self.reply_to_direct_replies = reply_to_direct_replies

    def decide(self, notification: Notification) -> PermissionDecision:
        sender_uid = str(notification.sender_uid)
        author_uid = str(notification.post_author_uid)
        if self.self_uid and sender_uid == self.self_uid:
            return PermissionDecision(False, "机器人自身消息")
        if sender_uid in self.user_blacklist:
            return PermissionDecision(False, "用户 UID 黑名单")
        if author_uid and author_uid in self.author_blacklist:
            return PermissionDecision(False, "帖子作者 UID 黑名单")
        content_folded = notification.content.casefold()
        if any(keyword in content_folded for keyword in self.keyword_blacklist):
            return PermissionDecision(False, "关键词黑名单")

        is_owner = bool(self.owner_uid and sender_uid == self.owner_uid)
        if is_owner:
            return PermissionDecision(
                True,
                "主人 UID",
                is_owner=True,
                map_as_admin=self.map_owner_to_admin,
            )

        if self.whitelist_mode and sender_uid not in self.user_whitelist:
            return PermissionDecision(False, "不在用户白名单")
        if self.author_whitelist and author_uid not in self.author_whitelist:
            return PermissionDecision(False, "帖子作者不在白名单")
        if self.only_explicit_mentions and not notification.explicit_wake:
            return PermissionDecision(False, "未明确 @ 机器人")
        if notification.event_type.value == "reply" and not self.reply_to_direct_replies:
            return PermissionDecision(False, "已关闭直接回复触发")
        return PermissionDecision(True, "普通触发规则")


def _uid_set(values) -> set[str]:
    return {str(value) for value in values if str(value).strip()}
