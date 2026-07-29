from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit

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
    if not qr_content:
        raise ResponseShapeError("二维码响应缺少 qrcode/qr_url")
    poll_params = {
        str(key): str(value)
        for key, value in parse_qsl(
            urlsplit(qr_content).query,
            keep_blank_values=True,
        )
    }
    request_id = _id(body, "request_id", "qr_id", "token", "nonce")
    if not request_id:
        request_id = next(
            (
                poll_params[key]
                for key in ("request_id", "qr_id", "token", "nonce")
                if poll_params.get(key)
            ),
            "",
        )
    if not request_id:
        request_id = hashlib.sha256(qr_content.encode("utf-8")).hexdigest()[:32]
    started = now if now is not None else time.time()
    raw_expiry = _first(body, "expires_in", "ttl", "expire", default=180)
    try:
        expiry = float(raw_expiry)
    except (TypeError, ValueError) as exc:
        raise ResponseShapeError("二维码过期时间不是数字") from exc
    if expiry > 10_000_000_000:
        expiry /= 1000
    ttl = expiry - started if expiry > 1_000_000_000 else expiry
    return QRLoginSession(
        profile_id=profile_id,
        request_id=request_id,
        qr_content=qr_content,
        expires_at=started + max(10, min(ttl, 600)),
        poll_params=poll_params,
    )


def parse_login_state(payload: Mapping[str, Any]) -> tuple[LoginState, str]:
    body = _data(payload)
    message = str(
        _first(
            body,
            "message",
            "msg",
            "error_msg",
            "err_msg",
        )
    )
    result_marker = str(_first(body, "error", "err", default="")).strip().lower()
    raw_state = str(_first(body, "state", "status", "qr_state", default="")).strip().lower()
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
    if raw_state in state_map:
        return state_map[raw_state], message
    if result_marker in {"ok", "success", "confirmed"}:
        return LoginState.SUCCESS, message
    if result_marker in {"ready", "scanned"}:
        return LoginState.SCANNED_WAITING_CONFIRM, message
    if result_marker in {"wait", "waiting"}:
        return LoginState.WAITING_SCAN, message

    hint = f"{result_marker} {message}".casefold()
    if any(token in hint for token in ("expired", "timeout", "过期", "失效", "超时")):
        return LoginState.EXPIRED, message
    if any(
        token in hint
        for token in (
            "scanned",
            "confirm",
            "已扫码",
            "已扫描",
            "待确认",
            "请确认",
            "确认",
        )
    ):
        return LoginState.SCANNED_WAITING_CONFIRM, message

    # The reference web login contract keeps returning a non-"ok" error marker
    # while it waits. Unknown waiting markers must not terminate a valid QR
    # session merely because the private API changed its wording.
    if result_marker or message:
        return LoginState.WAITING_SCAN, message
    return LoginState.WAITING_SCAN, ""


def parse_credentials(
    profile_id: str,
    payload: Mapping[str, Any],
    response_cookies: Mapping[str, str],
    *,
    logged_in_at: str,
    fallback_payloads: tuple[Mapping[str, Any], ...] = (),
) -> Credentials:
    bodies = [_data(item) for item in (payload, *fallback_payloads)]
    containers: list[Mapping[str, Any]] = []
    for body in bodies:
        for candidate in (
            body,
            body.get("user"),
            body.get("account"),
            body.get("profile"),
            body.get("account_detail"),
        ):
            if isinstance(candidate, Mapping):
                containers.append(candidate)

    uid = _first_from_mappings(
        containers,
        "uid",
        "heybox_id",
        "user_heybox_id",
        "heyboxid",
        "user_id",
        "userid",
        "id",
    )
    nickname = _first_from_mappings(
        containers,
        "nickname",
        "username",
        "name",
    )
    cookies = {str(key): str(value) for key, value in response_cookies.items()}
    for body in bodies:
        embedded_cookies = body.get("cookies")
        if isinstance(embedded_cookies, Mapping):
            cookies.update({str(key): str(value) for key, value in embedded_cookies.items()})
    _copy_login_cookie(cookies, containers, "pkey", "pkey", "user_pkey", "key")
    _copy_login_cookie(
        cookies,
        containers,
        "heybox_id",
        "heybox_id",
        "user_heybox_id",
        "heyboxid",
        "user_id",
        "userid",
    )
    _copy_login_cookie(
        cookies,
        containers,
        "x_xhh_tokenid",
        "x_xhh_tokenid",
    )
    if not uid:
        uid = _first_from_mappings(
            (cookies,),
            "user_heybox_id",
            "heybox_id",
            "heyboxid",
            "user_id",
            "userid",
            "uid",
        )
    access_token = _first_from_mappings(bodies, "access_token", "token")
    refresh_token = _first_from_mappings(bodies, "refresh_token")
    device_id = _first_from_mappings(bodies, "device_id")
    signing_key = _first_from_mappings(bodies, "signing_key", "sign_key")
    identity_cookie_names = {
        "heybox_id",
        "user_heybox_id",
        "heyboxid",
        "uid",
        "userid",
        "user_id",
    }
    has_cookie_credentials = any(name.casefold() not in identity_cookie_names for name in cookies)
    has_credentials = bool(has_cookie_credentials or access_token or refresh_token)
    if not uid:
        suffix = "（已收到凭证）" if has_credentials else "和登录凭证"
        raise ResponseShapeError(f"登录成功响应缺少 UID{suffix}")
    if not has_credentials:
        raise ResponseShapeError("登录成功响应缺少登录凭证")
    return Credentials(
        profile_id=profile_id,
        uid=uid,
        nickname=nickname,
        cookies=cookies,
        access_token=access_token,
        refresh_token=refresh_token,
        device_id=device_id,
        signing_key=signing_key,
        logged_in_at=logged_in_at,
    )


def _first_from_mappings(
    mappings: Any,
    *keys: str,
) -> str:
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            continue
        value = _first(mapping, *keys)
        if value is not None and value != "":
            return str(value)
    return ""


def _copy_login_cookie(
    destination: dict[str, str],
    containers: list[Mapping[str, Any]],
    normalized_name: str,
    *aliases: str,
) -> None:
    if destination.get(normalized_name):
        return
    value = _first_from_mappings(containers, *aliases)
    if value:
        destination[normalized_name] = value


def parse_notifications(
    profile_id: str,
    payload: Mapping[str, Any],
    event_type: NotificationType,
    *,
    page_size: int = 20,
    offset: int = 0,
) -> ApiPage:
    candidate = payload.get("result", payload.get("data", payload))
    if isinstance(candidate, Mapping):
        body = candidate
        raw_items = _first(body, "items", "messages", "list", default=[])
    elif isinstance(candidate, list):
        body = payload
        raw_items = candidate
    else:
        raise ResponseShapeError("响应 data/result 应为对象或数组")
    if not isinstance(raw_items, list):
        raise ResponseShapeError("通知列表字段不是数组")
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        message_type = str(_first(raw, "message_type", "type")).casefold()
        if event_type is NotificationType.MENTION and message_type:
            if message_type not in {"16", "17", "at", "mention"}:
                continue
        elif event_type is NotificationType.REPLY and message_type:
            if message_type not in {"1", "2", "comment", "reply"}:
                continue
        comment = raw.get("comment")
        comment_obj = comment if isinstance(comment, Mapping) else raw
        sender = raw.get(
            "sender",
            raw.get("user_a", raw.get("user", comment_obj.get("user", {}))),
        )
        sender_obj = sender if isinstance(sender, Mapping) else {}
        post = raw.get("post", raw.get("link", {}))
        post_obj = post if isinstance(post, Mapping) else {}
        event_id = _id(raw, "notification_id", "message_id", "id")
        comment_id = _id(
            comment_obj,
            "comment_a_id",
            "comment_id",
            "commentid",
            "replyid",
            "reply_id",
            "id",
        )
        post_id = _id(raw, "post_id", "link_id", "linkid") or _id(
            post_obj,
            "post_id",
            "link_id",
            "linkid",
            "id",
        )
        sender_uid = _id(
            sender_obj,
            "uid",
            "heybox_id",
            "heyboxid",
            "user_id",
            "userid",
            "id",
        ) or _id(raw, "userid_a", "user_id_a", "sender_uid")
        # The message center returns both post mentions (16) and comment
        # mentions (17) for the message_type=16 query. Post mentions have no
        # comment target: they are routed to the deterministic post session and
        # use the notification ID as a stable deduplication surrogate.
        is_post_mention = event_type is NotificationType.MENTION and message_type == "16"
        if not event_id or not post_id:
            raise ResponseShapeError("通知项缺少稳定通知 ID 或帖子 ID")
        if not is_post_mention and not comment_id:
            raise ResponseShapeError("评论通知项缺少稳定评论 ID")
        if not sender_uid:
            raise ResponseShapeError("通知项缺少发送者 UID")
        external_comment_id = comment_id
        root_id = ""
        parent_id = ""
        content_obj = post_obj if is_post_mention else comment_obj
        if is_post_mention:
            external_comment_id = f"post_message_{event_id}"
        else:
            root_id = _id(raw, "root_comment_id", "root_id", "rootCommentId") or _id(
                comment_obj,
                "root_comment_id",
                "root_id",
                "rootCommentId",
            )
            root_id = root_id or comment_id
            # The incoming comment becomes the parent of the bot's reply.
            # comment_b_id describes the earlier quoted comment instead.
            parent_id = comment_id
        image_values = _first(
            content_obj,
            "images",
            "image_urls",
            "comment_a_images",
            default=[],
        )
        if not isinstance(image_values, list):
            image_values = []
        image_urls = [
            str(item.get("url", item.get("src", ""))) if isinstance(item, Mapping) else str(item)
            for item in image_values
            if item
        ]
        post_author = post_obj.get("author", post_obj.get("user", {}))
        post_author_obj = post_author if isinstance(post_author, Mapping) else {}
        notification = Notification(
            profile_id=profile_id,
            external_event_id=event_id,
            external_comment_id=external_comment_id,
            notification_id=event_id,
            event_type=event_type,
            sender_uid=sender_uid,
            sender_nickname=str(_first(sender_obj, "nickname", "username", "name")),
            post_id=post_id,
            root_comment_id=root_id,
            parent_comment_id=parent_id,
            content=str(
                _first(
                    content_obj,
                    "comment_a_text",
                    "comment_text",
                    "content",
                    "text",
                    "message",
                    "description",
                )
            ),
            created_at=_timestamp(
                _first(raw, "created_at", "timestamp", "time", default=time.time())
            ),
            post_author_uid=(
                _id(raw, "post_author_uid")
                or _id(post_obj, "author_uid", "user_id", "userid", "uid")
                or _id(
                    post_author_obj,
                    "uid",
                    "heybox_id",
                    "user_id",
                    "userid",
                    "id",
                )
            ),
            explicit_wake=True,
            image_urls=[url for url in image_urls if url],
            raw=dict(raw),
        )
        items.append({"notification": notification})
    cursor = str(_first(body, "next_cursor", "cursor", "next", default=""))
    if "has_more" in body or "more" in body:
        has_more = _boolean(_first(body, "has_more", "more"))
    elif "no_more" in body:
        has_more = not _boolean(body.get("no_more"))
    else:
        has_more = len(raw_items) >= max(1, page_size)
    if has_more and not cursor:
        cursor = str(max(0, offset) + len(raw_items))
    return ApiPage(items=items, next_cursor=cursor, has_more=has_more)


def _boolean(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"false", "0", "no", "off", ""}:
            return False
        if normalized in {"true", "1", "yes", "on"}:
            return True
    return bool(value)


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
    post_body, embedded_images = _rich_post_content(
        _first(post_obj, "content", "text", "description")
    )
    raw_images = _first(post_obj, "images", "image_urls", default=[])
    if not isinstance(raw_images, list):
        raw_images = []
    images = [
        str(item.get("url", item.get("src", ""))) if isinstance(item, Mapping) else str(item)
        for item in raw_images
        if item
    ]
    images.extend(embedded_images)
    return ThreadContext(
        post_id=post_id,
        title=str(_first(post_obj, "title", "subject")),
        body=post_body,
        author_uid=_id(
            author_obj,
            "uid",
            "heybox_id",
            "heyboxid",
            "user_id",
            "userid",
            "id",
        ),
        author_name=str(_first(author_obj, "nickname", "username", "name")),
        comments=comments,
        image_urls=list(dict.fromkeys(url for url in images if url)),
    )


def parse_send_result(payload: Mapping[str, Any]) -> SendResult:
    status = str(_first(payload, "status", "stat")).casefold()
    if status and status not in {"ok", "success"}:
        raise ResponseShapeError("评论发送响应未返回成功状态")
    comment_id = _find_comment_id(payload)
    if not status and not comment_id:
        raise ResponseShapeError("评论发送响应缺少成功状态或评论 ID")
    return SendResult(external_comment_id=comment_id, confirmed=True, raw=dict(payload))


def _rich_post_content(value: Any) -> tuple[str, list[str]]:
    decoded = value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "", []
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return value, []
    if isinstance(decoded, Mapping):
        decoded = _first(decoded, "items", "content", "list", default=decoded)
    if not isinstance(decoded, list):
        return str(value or ""), []

    text_parts: list[str] = []
    image_urls: list[str] = []
    for segment in decoded:
        if not isinstance(segment, Mapping):
            if segment is not None:
                text_parts.append(str(segment))
            continue
        kind = str(_first(segment, "type", "kind")).casefold()
        text = str(_first(segment, "text", "content", "html"))
        url = str(_first(segment, "url", "src", "image_url"))
        if text and kind in {"", "text", "html"}:
            text_parts.append(text)
        elif text and not url:
            text_parts.append(text)
        if url and kind not in {"text", "html"}:
            image_urls.append(url)
    return "\n".join(part for part in text_parts if part), image_urls


def _find_comment_id(value: Any) -> str:
    if isinstance(value, Mapping):
        direct = _id(value, "commentid", "comment_id")
        if direct:
            return direct
        for key in ("result", "data", "comment", "comments"):
            if key in value:
                nested = _find_comment_id(value[key])
                if nested:
                    return nested
    elif isinstance(value, list):
        for item in value:
            nested = _find_comment_id(item)
            if nested:
                return nested
    return ""
