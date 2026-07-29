from __future__ import annotations

import asyncio
import email.utils
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from .endpoints import API_BASE_URL, EndpointName, endpoint
from .models import (
    ApiPage,
    Credentials,
    LoginState,
    NotificationType,
    QRLoginSession,
    RoutingTarget,
    SendResult,
    ThreadContext,
)
from .parsers import (
    ResponseShapeError,
    parse_credentials,
    parse_login_state,
    parse_notifications,
    parse_qr_response,
    parse_send_result,
    parse_thread_context,
)
from .rate_limiter import AsyncRateLimiter
from .request_signing import (
    RequestSigner,
    ensure_client_identity,
    generate_device_id,
)
from .security import redact_data

AuthInvalidCallback = Callable[[str, int], Awaitable[None]]

WEB_CLIENT_PARAMS = {
    "os_type": "web",
    "app": "web",
    "client_type": "web",
    "version": "999.0.4",
    "web_version": "2.5",
    "x_client_type": "web",
    "x_app": "heybox_website",
    "x_os_type": "Windows",
    "device_info": "Chrome",
    "_notip": "true",
}


class XiaoheiheApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        category: str = "api",
        retry_after: float | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.retry_after = retry_after
        self.details = redact_data(details)


class CredentialInvalidError(XiaoheiheApiError):
    """Credentials were rejected; real sends must stop immediately."""


class RateLimitedError(XiaoheiheApiError):
    """The service requested a slower request rate."""


class ResponseContractError(XiaoheiheApiError):
    """The upstream JSON shape changed."""


class SendUncertainError(XiaoheiheApiError):
    """A comment request timed out after dispatch and must not be blindly retried."""


@dataclass(slots=True)
class ApiResponse:
    payload: Mapping[str, Any]
    cookies: dict[str, str]
    headers: Mapping[str, str]


class XiaoheiheApiClient:
    def __init__(
        self,
        profile_id: str,
        *,
        credentials: Credentials | None = None,
        base_url: str = API_BASE_URL,
        request_timeout_seconds: float = 20,
        connect_timeout_seconds: float = 8,
        read_timeout_seconds: float = 15,
        min_request_interval_seconds: float = 1.0,
        max_retries: int = 3,
        signer: RequestSigner | None = None,
        client: httpx.AsyncClient | None = None,
        on_auth_invalid: AuthInvalidCallback | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.credentials = credentials
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            timeout=request_timeout_seconds,
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=request_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._max_retries = max(0, min(int(max_retries), 8))
        self._signer = signer or RequestSigner()
        self._client = client
        self._owns_client = client is None
        self._device_id = credentials.device_id if credentials else generate_device_id()
        if credentials is not None:
            ensure_client_identity(credentials, device_id=self._device_id)
            self._device_id = credentials.device_id
        self._limiter = AsyncRateLimiter(min_request_interval_seconds)
        self._on_auth_invalid = on_auth_invalid
        self._closed = False
        self.last_success_at: str | None = None
        self.last_error: dict[str, Any] | None = None
        self.last_notification_polls: dict[str, dict[str, Any]] = {}
        self.consecutive_status: dict[int, int] = {401: 0, 403: 0, 429: 0}

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("HTTP Client 已关闭")
        if self._client is None:
            limits = httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30,
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                limits=limits,
                follow_redirects=False,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "AstrBot-Xiaoheihe-Adapter/1.0.7",
                },
            )
        if self.credentials:
            for key, value in self.credentials.cookies.items():
                self._client.cookies.set(key, value)

    async def request_qr(self) -> QRLoginSession:
        response = await self._request(EndpointName.REQUEST_QR)
        return parse_qr_response(self.profile_id, response.payload)

    async def check_qr(
        self, qr_session: QRLoginSession
    ) -> tuple[LoginState, str, Credentials | None]:
        params = dict(qr_session.poll_params)
        if not params:
            params = dict(
                parse_qsl(
                    urlsplit(qr_session.qr_content).query,
                    keep_blank_values=True,
                )
            )
        if not params and qr_session.request_id:
            params["request_id"] = qr_session.request_id
        response = await self._request(EndpointName.QR_STATE, params=params)
        state, message = parse_login_state(response.payload)
        credentials = None
        if state is LoginState.SUCCESS:
            logged_in_at = datetime.now(UTC).isoformat()
            try:
                credentials = parse_credentials(
                    self.profile_id,
                    response.payload,
                    response.cookies,
                    logged_in_at=logged_in_at,
                )
            except ResponseShapeError as exc:
                raise ResponseContractError(
                    str(exc),
                    category="response_shape",
                    details={
                        "qr_result_fields": _result_field_names(response.payload),
                        "session_cookie_names": sorted(response.cookies)[:30],
                        "session_credential_count": len(response.cookies),
                    },
                ) from exc
            ensure_client_identity(credentials, device_id=self._device_id)
            self.credentials = credentials
        return state, message, credentials

    async def check_credentials(self) -> dict[str, Any]:
        response = await self._request(EndpointName.CURRENT_USER)
        body = response.payload.get("result", response.payload.get("data", response.payload))
        if not isinstance(body, Mapping):
            raise ResponseContractError("账号检查响应不是对象", category="response_shape")
        return dict(body)

    async def fetch_notifications(
        self,
        event_type: NotificationType,
        *,
        cursor: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> ApiPage:
        normalized_page_size = max(1, min(page_size, 100))
        offset = (max(1, page) - 1) * normalized_page_size
        if cursor:
            try:
                offset = max(0, int(cursor))
            except ValueError:
                pass
        params: dict[str, Any] = {
            "offset": offset,
            "limit": normalized_page_size,
            "no_more": "false",
            "os_type": "web",
        }
        if self.credentials and self.credentials.uid:
            params["heybox_id"] = self.credentials.uid
        if event_type is NotificationType.MENTION:
            params["message_type"] = 16
        else:
            params["list_type"] = 0
        response = await self._request(EndpointName.USER_MESSAGES, params=params)
        summary = _notification_response_summary(response.payload)
        try:
            parsed = parse_notifications(
                self.profile_id,
                response.payload,
                event_type,
                page_size=normalized_page_size,
                offset=offset,
            )
        except ResponseShapeError as exc:
            summary["error"] = str(exc)
            self.last_notification_polls[event_type.value] = summary
            error = ResponseContractError(str(exc), category="response_shape", details=summary)
            self._remember_error(error)
            raise error from exc
        summary["accepted_count"] = len(parsed.items)
        self.last_notification_polls[event_type.value] = summary
        return parsed

    async def fetch_thread_context(
        self, post_id: str, *, root_comment_id: str = ""
    ) -> ThreadContext:
        params: dict[str, Any] = {"link_id": post_id, "h_src": ""}
        if root_comment_id:
            params["root_comment_id"] = root_comment_id
        response = await self._request(EndpointName.POST_TREE, params=params)
        try:
            return parse_thread_context(response.payload, post_id)
        except ResponseShapeError as exc:
            error = ResponseContractError(
                str(exc),
                category="response_shape",
                details=_response_shape_summary(response.payload),
            )
            self._remember_error(error)
            raise error from exc

    async def send_comment(self, route: RoutingTarget, content: str) -> SendResult:
        body = {
            "is_cy": "0",
            "link_id": route.post_id,
            "reply_id": route.parent_comment_id or "-1",
            "root_id": route.root_comment_id or "-1",
            "text": content,
        }
        response = await self._request(EndpointName.CREATE_COMMENT, form_body=body)
        try:
            return parse_send_result(response.payload)
        except ResponseShapeError as exc:
            error = ResponseContractError(
                str(exc),
                category="response_shape",
                details=_response_shape_summary(response.payload),
            )
            self._remember_error(error)
            raise error from exc

    async def recent_comments(
        self, route: RoutingTarget, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        response = await self._request(
            EndpointName.RECENT_COMMENTS,
            params={
                "link_id": route.post_id,
                "root_comment_id": route.root_comment_id,
                "limit": max(1, min(limit, 50)),
            },
        )
        body = response.payload.get("result", response.payload.get("data", response.payload))
        if not isinstance(body, Mapping):
            raise ResponseContractError("近期评论响应不是对象", category="response_shape")
        items = body.get("items", body.get("comments", body.get("list", [])))
        if not isinstance(items, list):
            raise ResponseContractError("近期评论列表字段不是数组", category="response_shape")
        return [dict(item) for item in items if isinstance(item, Mapping)]

    async def fetch_feed(
        self, *, source: str = "follow", cursor: str = "", limit: int = 10
    ) -> ApiPage:
        params: dict[str, Any] = {
            "source": source,
            "limit": max(1, min(limit, 50)),
        }
        if cursor:
            params["cursor"] = cursor
        response = await self._request(EndpointName.FEED, params=params)
        body = response.payload.get("result", response.payload.get("data", response.payload))
        if not isinstance(body, Mapping):
            raise ResponseContractError("帖子流响应不是对象", category="response_shape")
        items = body.get("items", body.get("links", body.get("list", [])))
        if not isinstance(items, list):
            raise ResponseContractError("帖子流列表字段不是数组", category="response_shape")
        return ApiPage(
            items=[dict(item) for item in items if isinstance(item, Mapping)],
            next_cursor=str(body.get("next_cursor", body.get("cursor", ""))),
            has_more=bool(body.get("has_more", False)),
        )

    async def _request(
        self,
        name: EndpointName,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form_body: dict[str, Any] | None = None,
    ) -> ApiResponse:
        await self.start()
        if self._client is None:
            raise RuntimeError("HTTP Client 未初始化")
        contract = endpoint(name)
        if contract.authenticated and self.credentials is None:
            raise CredentialInvalidError(
                "账号未登录", status_code=401, category="credential_invalid"
            )
        request_params = dict(params or {})
        for key, value in WEB_CLIENT_PARAMS.items():
            request_params.setdefault(key, value)
        if self.credentials and self.credentials.uid:
            request_params.setdefault("heybox_id", self.credentials.uid)
        signed = self._signer.sign(
            contract.method,
            contract.path,
            params=request_params,
            json_body=json_body or form_body,
            signing_key=self.credentials.signing_key if self.credentials else "",
            device_id=self._device_id,
        )
        headers = dict(signed.headers)
        headers["Referer"] = "https://www.xiaoheihe.cn/"
        if self.credentials and self.credentials.access_token:
            headers["Authorization"] = f"Bearer {self.credentials.access_token}"
        if form_body is not None:
            headers.update(
                {
                    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                    "Origin": "https://www.xiaoheihe.cn",
                }
            )
        request_url = (
            contract.path
            if contract.base_url.rstrip("/") == self._base_url
            else f"{contract.base_url.rstrip('/')}{contract.path}"
        )

        retry_count = 0
        while True:
            await self._limiter.wait()
            try:
                response = await self._client.request(
                    contract.method,
                    request_url,
                    params=signed.params,
                    json=json_body,
                    data=form_body,
                    headers=headers,
                    timeout=self._timeout,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if not contract.retry_safe:
                    error = SendUncertainError(
                        "评论请求超时或连接中断，服务端可能已接收",
                        category="send_unknown",
                    )
                    self._remember_error(error)
                    raise error from exc
                if retry_count >= self._max_retries:
                    error = XiaoheiheApiError("网络请求在重试后仍失败", category="network")
                    self._remember_error(error)
                    raise error from exc
                await asyncio.sleep(_backoff(retry_count))
                retry_count += 1
                continue

            if response.status_code in {401, 403}:
                self._count_status(response.status_code)
                error_type = (
                    CredentialInvalidError if response.status_code == 401 else XiaoheiheApiError
                )
                error = error_type(
                    f"小黑盒返回 HTTP {response.status_code}",
                    status_code=response.status_code,
                    category=("credential_invalid" if response.status_code == 401 else "forbidden"),
                    details=_safe_response_payload(response),
                )
                self._remember_error(error)
                if self._on_auth_invalid is not None:
                    await self._on_auth_invalid(self.profile_id, response.status_code)
                raise error

            if response.status_code == 429:
                self._count_status(429)
                retry_after = _retry_after(response.headers.get("Retry-After"))
                if contract.retry_safe and retry_count < self._max_retries:
                    await asyncio.sleep(min(retry_after or _backoff(retry_count), 120))
                    retry_count += 1
                    continue
                error = RateLimitedError(
                    "小黑盒请求频率受限",
                    status_code=429,
                    category="rate_limited",
                    retry_after=retry_after,
                )
                self._remember_error(error)
                raise error

            if 500 <= response.status_code < 600:
                if contract.retry_safe and retry_count < self._max_retries:
                    await asyncio.sleep(_backoff(retry_count))
                    retry_count += 1
                    continue
                error = XiaoheiheApiError(
                    f"小黑盒服务暂时不可用: HTTP {response.status_code}",
                    status_code=response.status_code,
                    category="server",
                    details=_safe_response_payload(response),
                )
                self._remember_error(error)
                raise error

            if response.status_code < 200 or response.status_code >= 300:
                error = XiaoheiheApiError(
                    f"小黑盒请求失败: HTTP {response.status_code}",
                    status_code=response.status_code,
                    category="http",
                    details=_safe_response_payload(response),
                )
                self._remember_error(error)
                raise error

            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                error = ResponseContractError(
                    "小黑盒响应不是有效 JSON",
                    status_code=response.status_code,
                    category="response_shape",
                )
                self._remember_error(error)
                raise error from exc
            if not isinstance(payload, Mapping):
                error = ResponseContractError(
                    "小黑盒 JSON 顶层不是对象",
                    status_code=response.status_code,
                    category="response_shape",
                )
                self._remember_error(error)
                raise error
            upstream_error = _upstream_error(payload) if contract.authenticated else ""
            if upstream_error:
                credential_invalid = _is_relogin_response(payload)
                error_type = CredentialInvalidError if credential_invalid else XiaoheiheApiError
                error = error_type(
                    upstream_error,
                    status_code=401 if credential_invalid else response.status_code,
                    category=("credential_invalid" if credential_invalid else "upstream_rejected"),
                    details=_response_shape_summary(payload),
                )
                self._remember_error(error)
                if credential_invalid and self._on_auth_invalid is not None:
                    await self._on_auth_invalid(self.profile_id, 401)
                raise error
            self._reset_status_counts()
            self.last_success_at = datetime.now(UTC).isoformat()
            self.last_error = None
            return ApiResponse(
                payload=payload,
                cookies=_cookie_jar_values(self._client, response),
                headers=response.headers,
            )

    def _remember_error(self, error: XiaoheiheApiError) -> None:
        self.last_error = {
            "category": error.category,
            "status_code": error.status_code,
            "message": str(error),
            "details": error.details,
        }

    def _count_status(self, status: int) -> None:
        if status in self.consecutive_status:
            self.consecutive_status[status] += 1
        for other in self.consecutive_status:
            if other != status:
                self.consecutive_status[other] = 0

    def _reset_status_counts(self) -> None:
        for status in self.consecutive_status:
            self.consecutive_status[status] = 0

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        client = self._client
        self._client = None
        if client is not None and self._owns_client:
            await client.aclose()


def _backoff(attempt: int) -> float:
    return min(30.0, 0.5 * (2**attempt)) + random.uniform(0.0, 0.35)


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            return max(0.0, parsed.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _safe_response_payload(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return {"body": response.text[:500]}
    return redact_data(payload)


def _cookie_jar_values(
    client: httpx.AsyncClient,
    response: httpx.Response,
) -> dict[str, str]:
    """Return the full session cookie jar without logging or exposing it."""

    cookies: dict[str, str] = {}
    for cookie in client.cookies.jar:
        cookies[str(cookie.name)] = str(cookie.value)
    for cookie in response.cookies.jar:
        cookies[str(cookie.name)] = str(cookie.value)
    return cookies


def _result_field_names(payload: Mapping[str, Any]) -> list[str]:
    candidate = payload.get("result", payload.get("data", payload))
    if not isinstance(candidate, Mapping):
        return []
    return sorted(str(key) for key in candidate)[:50]


def _upstream_error(payload: Mapping[str, Any]) -> str:
    marker = payload.get("status", payload.get("stat"))
    if marker is None or isinstance(marker, (Mapping, list)):
        return ""
    normalized = str(marker).strip().casefold()
    if not normalized or normalized in {"ok", "success", "200"}:
        return ""
    raw_message = payload.get("msg", payload.get("message", payload.get("error", "")))
    message = redact_data(str(raw_message)) if raw_message else ""
    suffix = f": {str(message)[:300]}" if message else ""
    return f"小黑盒 API 返回非成功状态 {str(marker)[:80]}{suffix}"


def _is_relogin_response(payload: Mapping[str, Any]) -> bool:
    marker = str(payload.get("status", payload.get("stat", ""))).strip().casefold()
    message = str(payload.get("msg", payload.get("message", payload.get("error", ""))))
    hint = f"{marker} {message}".casefold()
    return any(
        token in hint
        for token in (
            "relogin",
            "login required",
            "not login",
            "unauthorized",
            "重新登录",
            "请登录",
            "未登录",
            "登录失效",
        )
    )


def _response_shape_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidate = payload.get("result", payload.get("data", payload))
    summary: dict[str, Any] = {
        "top_fields": sorted(str(key) for key in payload)[:50],
        "result_type": type(candidate).__name__,
    }
    marker = payload.get("status", payload.get("stat"))
    if marker is not None and not isinstance(marker, (Mapping, list)):
        summary["status"] = str(marker)[:80]
    if isinstance(candidate, Mapping):
        summary["result_fields"] = sorted(str(key) for key in candidate)[:50]
    elif isinstance(candidate, list):
        summary["result_count"] = len(candidate)
    return summary


def _notification_response_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = _response_shape_summary(payload)
    candidate = payload.get("result", payload.get("data", payload))
    raw_items: Any = []
    if isinstance(candidate, Mapping):
        for key in ("items", "messages", "list"):
            if key in candidate:
                raw_items = candidate[key]
                summary["list_field"] = key
                break
    elif isinstance(candidate, list):
        raw_items = candidate
        summary["list_field"] = "result"
    if isinstance(raw_items, list):
        summary["raw_count"] = len(raw_items)
        field_names: set[str] = set()
        message_types: set[str] = set()
        for item in raw_items[:5]:
            if not isinstance(item, Mapping):
                continue
            field_names.update(str(key) for key in item)
            marker = item.get("message_type", item.get("type"))
            if marker is not None:
                message_types.add(str(marker)[:40])
        summary["item_fields"] = sorted(field_names)[:80]
        summary["message_types"] = sorted(message_types)[:20]
    else:
        summary["list_type"] = type(raw_items).__name__
    summary["checked_at"] = datetime.now(UTC).isoformat()
    return summary
