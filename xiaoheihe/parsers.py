from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from .models import (
    ApiPage,
    Credentials,
    LoginState,
    Notification,
    NotificationType,
    QRLoginSession,
    SendResult,
    ThreadContext,
)


class ResponseShapeError(ValueError):
    """The upstream response no longer matches the isolated contract."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResponseShapeError(f"{name} 应为对象")
    return value


def _data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    candidate = payload.get("result", payload.get("data", payload))
    return _mapping(candidate, "响应 data/result")


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return default


def _id(mapping: Mapping[str, Any], *keys: str) -> str:
    value = _first(mapping, *keys)
    return str(value) if value is not None else ""


def _timestamp(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ResponseShapeError("通知时间字段不是数字") from exc
    if result > 10_000_000_000:
        result /= 1000
    return result


def parse_qr_response(
    profile_id: str, payload: Mapping[str, Any], *, now: float | None = None
) -> QRLoginSession:
    body = _data(payload)
    qr_content = str(_first(body, "qrcode", "qr_url", "url", "qr_content"))
    request_id = _id(body, "request_id", "qr_id", "token", "nonce")
    if not qr_content:
        raise ResponseShapeError("二维码响应缺少 qrcode/qr_url")
    if not request_id:
        request_id = qr_content.rsplit("?", 1)[-1][:160]
    ttl = int(_first(body, "expires_in", "ttl", default=180))
    started = now if now is not None else time.time()
    return QRLoginSession(
        profile_id=profile_id,
        request_id=request_id,
        qr_content=qr_content,
        expires_at=started + max(10, min(ttl, 600)),
    )


def parse_login_state(payload: Mapping[str, Any]) -> tuple[LoginState, str]:
    body = _data(payload)
    raw_state = str(_first(body, "state", "status", "qr_state", default="waiting")).lower()
    state_map = {
        "0": LoginState.WAITING_SCAN,
        "waiting": LoginState.WAITING_SCAN,
        "waiting_scan": LoginState.WAITING_SCAN,
        "1": LoginState.SCANNED_WAITING_CONFIRM,
        "scanned": LoginState.SCANNED_WAITING_CONFIRM,
        "confirm": LoginState.SCANNED_WAITING_CONFIRM,
        "2": LoginState.SUCCESS,
        "success": LoginState.SUCCESS,
        "confirmed": LoginState.SUCCESS,
        "3": LoginState.EXPIRED,
        "expired": LoginState.EXPIRED,
        "-1": LoginState.FAILED,
        "failed": LoginState.FAILED,
    }
    return state_map.get(raw_state, LoginState.FAILED), str(_first(body, "message", "msg"))


def parse_credentials(
    profile_id: str,
    payload: Mapping[str, Any],
    response_cookies: Mapping[str, str],
    *,
    logged_in_at: str,
) -> Credentials:
    body = _data(payload)
    user = _mapping(body.get("user", body.get("account", body)), "登录用户")
    uid = _id(user, "uid", "heybox_id", "user_id", "id")
    nickname = str(_first(user, "nickname", "username", "name"))
    cookies = {str(key): str(value) for key, value in response_cookies.items()}
    embedded_cookies = body.get("cookies")
    if isinstance(embedded_cookies, Mapping):
        cookies.update({str(key): str(value) for key, value in embedded_cookies.items()})
    access_token = str(_first(body, "access_token", "token"))
    if not uid or not (cookies or access_token):
        raise ResponseShapeError("登录成功响应缺少 UID 或凭证")
    return Credentials(
        profile_id=profile_id,
        uid=uid,
        nickname=nickname,
        cookies=cookies,
        access_token=access_token,
        refresh_token=str(_first(body, "refresh_token")),
        device_id=str(_first(body, "device_id")),
        signing_key=str(_first(body, "signing_key", "sign_key")),
        logged_in_at=logged_in_at,
    )


def parse_notifications(
    profile_id: str,
    payload: Mapping[str, Any],
    event_type: NotificationType,
) -> ApiPage:
    body = _data(payload)
    raw_items = _first(body, "items", "messages", "list", default=[])
    if not isinstance(raw_items, list):
        raise ResponseShapeError("通知列表字段不是数组")
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        comment = raw.get("comment")
        comment_obj = comment if isinstance(comment, Mapping) else raw
        sender = raw.get("sender", raw.get("user", comment_obj.get("user", {})))
        sender_obj = sender if isinstance(sender, Mapping) else {}
        post = raw.get("post", raw.get("link", {}))
        post_obj = post if isinstance(post, Mapping) else {}
        event_id = _id(raw, "notification_id", "message_id", "id")
        comment_id = _id(comment_obj, "comment_id", "id")
        post_id = _id(raw, "post_id", "link_id") or _id(post_obj, "post_id", "link_id", "id")
        sender_uid = _id(sender_obj, "uid", "heybox_id", "user_id", "id")
        if not event_id or not comment_id or not post_id:
            raise ResponseShapeError("通知项缺少稳定通知 ID、评论 ID 或帖子 ID")
        if not sender_uid:
            raise ResponseShapeError("通知项缺少发送者 UID")
        root_id = _id(raw, "root_comment_id", "root_id") or _id(
            comment_obj, "root_comment_id", "root_id"
        )
        parent_id = _id(raw, "parent_comment_id", "parent_id", "reply_id") or _id(
            comment_obj, "parent_comment_id", "parent_id", "reply_id"
        )
        root_id = root_id or comment_id
        parent_id = parent_id or root_id
        image_values = _first(comment_obj, "images", "image_urls", default=[])
        image_urls = [
            str(item.get("url", item.get("src", ""))) if isinstance(item, Mapping) else str(item)
            for item in image_values
            if item
        ]
        notification = Notification(
            profile_id=profile_id,
            external_event_id=event_id,
            external_comment_id=comment_id,
            notification_id=event_id,
            event_type=event_type,
            sender_uid=sender_uid,
            sender_nickname=str(_first(sender_obj, "nickname", "username", "name")),
            post_id=post_id,
            root_comment_id=root_id,
            parent_comment_id=parent_id,
            content=str(_first(comment_obj, "content", "text", "message")),
            created_at=_timestamp(
                _first(raw, "created_at", "timestamp", "time", default=time.time())
            ),
            post_author_uid=_id(post_obj, "author_uid", "user_id", "uid"),
            explicit_wake=True,
            image_urls=[url for url in image_urls if url],
            raw=dict(raw),
        )
        items.append({"notification": notification})
    cursor = str(_first(body, "next_cursor", "cursor", "next", default=""))
    has_more = bool(_first(body, "has_more", "more", default=bool(cursor)))
    return ApiPage(items=items, next_cursor=cursor, has_more=has_more)


def parse_thread_context(payload: Mapping[str, Any], post_id: str) -> ThreadContext:
    body = _data(payload)
    post = body.get("post", body.get("link", body))
    post_obj = _mapping(post, "帖子")
    author = post_obj.get("author", post_obj.get("user", {}))
    author_obj = author if isinstance(author, Mapping) else {}
    raw_comments = _first(body, "comments", "comment_list", "children", default=[])
    if not isinstance(raw_comments, list):
        raise ResponseShapeError("楼层评论字段不是数组")
    comments = [dict(item) for item in raw_comments if isinstance(item, Mapping)]
    raw_images = _first(post_obj, "images", "image_urls", default=[])
    images = [
        str(item.get("url", item.get("src", ""))) if isinstance(item, Mapping) else str(item)
        for item in raw_images
        if item
    ]
    return ThreadContext(
        post_id=post_id,
        title=str(_first(post_obj, "title", "subject")),
        body=str(_first(post_obj, "content", "text", "description")),
        author_uid=_id(author_obj, "uid", "heybox_id", "user_id", "id"),
        author_name=str(_first(author_obj, "nickname", "username", "name")),
        comments=comments,
        image_urls=[url for url in images if url],
    )


def parse_send_result(payload: Mapping[str, Any]) -> SendResult:
    body = _data(payload)
    comment = body.get("comment", body)
    comment_obj = _mapping(comment, "评论发送结果")
    comment_id = _id(comment_obj, "comment_id", "id")
    if not comment_id:
        raise ResponseShapeError("评论发送响应缺少评论 ID")
    return SendResult(external_comment_id=comment_id, confirmed=True, raw=dict(payload))
