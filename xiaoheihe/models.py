from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class LoginState(StrEnum):
    IDLE = "idle"
    REQUESTING_QR = "requesting_qr"
    WAITING_SCAN = "waiting_scan"
    SCANNED_WAITING_CONFIRM = "scanned_waiting_confirm"
    SUCCESS = "success"
    EXPIRED = "expired"
    FAILED = "failed"
    LOGGED_OUT = "logged_out"
    CREDENTIAL_INVALID = "credential_invalid"


class NotificationType(StrEnum):
    MENTION = "mention"
    REPLY = "reply"
    PROACTIVE_FEED = "proactive_feed"


class EventState(StrEnum):
    DISCOVERED = "discovered"
    CLAIMED = "claimed"
    CONTEXT_READY = "context_ready"
    DISPATCHED = "dispatched"
    GENERATED = "generated"
    SENT = "sent"
    DRY_RUN = "dry_run"
    IGNORED = "ignored"
    RETRY_WAIT = "retry_wait"
    SEND_UNKNOWN = "send_unknown"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True, slots=True)
class RoutingTarget:
    profile_id: str
    post_id: str
    root_comment_id: str = ""
    parent_comment_id: str = ""
    notification_id: str = ""
    incoming_event_id: int | None = None

    @property
    def session_id(self) -> str:
        if self.root_comment_id:
            return f"xhh_thread_{self.post_id}_{self.root_comment_id}"
        return f"xhh_post_{self.post_id}"

    @property
    def group_id(self) -> str:
        return f"xhh_post_{self.post_id}"

    @classmethod
    def from_session_id(cls, profile_id: str, session_id: str) -> RoutingTarget:
        if session_id.startswith("xhh_post_"):
            post_id = session_id.removeprefix("xhh_post_")
            if post_id and "_" not in post_id:
                return cls(profile_id=profile_id, post_id=post_id)
            if post_id:
                return cls(profile_id=profile_id, post_id=post_id)
        if session_id.startswith("xhh_thread_"):
            payload = session_id.removeprefix("xhh_thread_")
            post_id, separator, root_comment_id = payload.rpartition("_")
            if separator and post_id and root_comment_id:
                return cls(
                    profile_id=profile_id,
                    post_id=post_id,
                    root_comment_id=root_comment_id,
                    parent_comment_id=root_comment_id,
                )
        raise ValueError(f"无法从 session_id 恢复小黑盒目标: {session_id!r}")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.incoming_event_id is None:
            payload.pop("incoming_event_id", None)
        return payload


@dataclass(slots=True)
class Credentials:
    profile_id: str
    uid: str
    nickname: str
    cookies: dict[str, str]
    access_token: str = ""
    refresh_token: str = ""
    device_id: str = ""
    signing_key: str = ""
    logged_in_at: str = ""

    def public_dict(self) -> dict[str, str | bool]:
        return {
            "profile_id": self.profile_id,
            "uid": self.uid,
            "nickname": self.nickname,
            "logged_in_at": self.logged_in_at,
            "has_credentials": bool(self.cookies or self.access_token or self.refresh_token),
        }


@dataclass(slots=True)
class QRLoginSession:
    profile_id: str
    request_id: str
    qr_content: str
    expires_at: float
    poll_params: dict[str, str] = field(default_factory=dict)
    state: LoginState = LoginState.WAITING_SCAN
    message: str = ""

    def public_dict(self, now: float) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "expires_at": self.expires_at,
            "remaining_seconds": max(0, int(self.expires_at - now)),
            "state": self.state.value,
            "message": self.message,
        }


@dataclass(slots=True)
class Notification:
    profile_id: str
    external_event_id: str
    external_comment_id: str
    notification_id: str
    event_type: NotificationType
    sender_uid: str
    sender_nickname: str
    post_id: str
    root_comment_id: str
    parent_comment_id: str
    content: str
    created_at: float
    observed_at: float = 0.0
    post_author_uid: str = ""
    explicit_wake: bool = True
    image_urls: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def route(self) -> RoutingTarget:
        return RoutingTarget(
            profile_id=self.profile_id,
            post_id=self.post_id,
            root_comment_id=self.root_comment_id,
            parent_comment_id=self.parent_comment_id,
            notification_id=self.notification_id,
        )

    @property
    def message_id(self) -> str:
        return (
            f"xhh_{self.event_type.value}_"
            f"{self.notification_id or 'none'}_{self.external_comment_id or 'none'}"
        )


@dataclass(slots=True)
class ThreadContext:
    post_id: str
    title: str
    body: str
    author_uid: str
    author_name: str
    comments: list[dict[str, Any]]
    image_urls: list[str] = field(default_factory=list)
    post_created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    reason: str
    is_owner: bool = False
    map_as_admin: bool = False


@dataclass(slots=True)
class SendResult:
    external_comment_id: str
    confirmed: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ApiPage:
    items: list[dict[str, Any]]
    next_cursor: str = ""
    has_more: bool = False
