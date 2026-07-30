from __future__ import annotations

import copy
from urllib.parse import parse_qs

import httpx
import pytest

from tests.helpers import load_fixture
from xiaoheihe.api_client import (
    CredentialInvalidError,
    ResponseContractError,
    SendUncertainError,
    XiaoheiheApiClient,
    XiaoheiheApiError,
)
from xiaoheihe.models import Credentials, NotificationType, RoutingTarget
from xiaoheihe.rate_limiter import AsyncRateLimiter


def credentials() -> Credentials:
    return Credentials(
        profile_id="default",
        uid="10001",
        nickname="Bot",
        cookies={"mock_sid": "redacted-fixture-value"},
    )


def client_with_handler(handler, *, authenticated=True, callback=None):
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://api.xiaoheihe.cn",
        transport=transport,
        follow_redirects=False,
    )
    client = XiaoheiheApiClient(
        "default",
        credentials=credentials() if authenticated else None,
        client=http_client,
        min_request_interval_seconds=0,
        on_auth_invalid=callback,
    )
    client._limiter = AsyncRateLimiter(0, jitter_seconds=0)
    return client, http_client


async def test_comment_context_merges_original_post_and_root_comment_tree() -> None:
    observed: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params)
        observed.append(query)
        if "root_comment_id" not in query:
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "result": {
                        "link": {
                            "linkid": "30003",
                            "title": "原帖标题",
                            "text": (
                                '[{"type":"text","text":"原帖正文"},'
                                '{"type":"image","url":"https://cdn.example.com/post.png"}]'
                            ),
                            "user": {"userid": "author", "username": "作者"},
                        }
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {
                    "link": {"linkid": "30003"},
                    "comments": [
                        {
                            "id": "root-1",
                            "content": "楼层内容",
                            "user": {"userid": "user", "username": "用户"},
                        }
                    ],
                },
            },
        )

    client, http_client = client_with_handler(handler)
    context = await client.fetch_thread_context("30003", root_comment_id="root-1")

    assert len(observed) == 2
    assert "root_comment_id" not in observed[0]
    assert observed[1]["root_comment_id"] == "root-1"
    assert context.title == "原帖标题"
    assert context.body == "原帖正文"
    assert context.image_urls == ["https://cdn.example.com/post.png"]
    assert context.comments[0]["content"] == "楼层内容"
    await client.close()
    await http_client.aclose()


async def test_qr_wait_scan_success_and_notification_parse() -> None:
    responses = {
        "/account/get_qrcode_url/": load_fixture("qr_response.json"),
        "/account/qr_state/": load_fixture("login_success.json"),
        "/bbs/app/user/message": load_fixture("notifications_mentions.json"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses[request.url.path])

    client, http_client = client_with_handler(handler, authenticated=False)
    qr = await client.request_qr()
    assert qr.request_id == "mock-qr-1"
    state, _, result_credentials = await client.check_qr(qr)
    assert state.value == "success"
    assert result_credentials.uid == "10001"
    client.credentials = result_credentials
    page = await client.fetch_notifications(NotificationType.MENTION)
    assert page.items[0]["notification"].message_id == "xhh_mention_notice-1_comment-1"
    await client.close()
    await http_client.aclose()


async def test_notification_queries_use_reference_parameters_and_offset_paging() -> None:
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(dict(request.url.params))
        return httpx.Response(
            200,
            json=load_fixture("notifications_reference_messages.json"),
        )

    client, http_client = client_with_handler(handler)
    mention_page = await client.fetch_notifications(
        NotificationType.MENTION,
        page=2,
        page_size=20,
    )
    await client.fetch_notifications(
        NotificationType.REPLY,
        cursor="40",
        page=3,
        page_size=20,
    )

    assert mention_page.items[0]["notification"].external_comment_id == "70001"
    assert observed[0]["message_type"] == "16"
    assert observed[0]["offset"] == "20"
    assert observed[0]["limit"] == "20"
    assert observed[0]["no_more"] == "false"
    assert observed[0]["heybox_id"] == "10001"
    assert observed[0]["os_type"] == "web"
    assert observed[0]["app"] == "web"
    assert observed[0]["client_type"] == "web"
    assert observed[0]["version"] == "999.0.4"
    assert observed[0]["web_version"] == "2.5"
    assert observed[0]["x_client_type"] == "web"
    assert observed[0]["x_app"] == "heybox_website"
    assert observed[0]["x_os_type"] == "Windows"
    assert observed[0]["_notip"] == "true"
    assert len(observed[0]["device_id"]) == 32
    assert len(observed[0]["hkey"]) == 7
    assert "type" not in observed[0]
    assert "page" not in observed[0]
    assert observed[1]["list_type"] == "0"
    assert observed[1]["offset"] == "40"
    assert "message_type" not in observed[1]
    assert client.last_notification_polls["mention"]["raw_count"] == 1
    assert client.last_notification_polls["mention"]["accepted_count"] == 1
    assert client.last_notification_polls["mention"]["message_types"] == ["17"]
    await client.close()
    await http_client.aclose()


async def test_real_world_comment_mentions_type_17_are_all_accepted() -> None:
    base = load_fixture("notifications_reference_messages.json")["result"]["messages"][0]
    messages = []
    for index in range(5):
        item = copy.deepcopy(base)
        item["message_id"] = f"mention-{index}"
        item["comment_a_id"] = f"comment-{index}"
        messages.append(item)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "ok", "result": {"messages": messages, "no_more": True}},
        )

    client, http_client = client_with_handler(handler)
    page = await client.fetch_notifications(NotificationType.MENTION)
    assert len(page.items) == 5
    assert client.last_notification_polls["mention"]["raw_count"] == 5
    assert client.last_notification_polls["mention"]["accepted_count"] == 5
    assert client.last_notification_polls["mention"]["message_types"] == ["17"]
    await client.close()
    await http_client.aclose()


async def test_authenticated_upstream_failed_status_is_not_silent_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "failed", "msg": "request rejected", "result": None},
        )

    client, http_client = client_with_handler(handler)
    with pytest.raises(XiaoheiheApiError, match="非成功状态 failed"):
        await client.fetch_notifications(NotificationType.MENTION)
    assert client.last_error["category"] == "upstream_rejected"
    assert client.last_success_at is None
    await client.close()
    await http_client.aclose()


async def test_relogin_business_status_invalidates_credentials() -> None:
    called = []

    async def callback(profile_id, status):
        called.append((profile_id, status))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "relogin", "msg": "请重新登录"},
        )

    client, http_client = client_with_handler(handler, callback=callback)
    with pytest.raises(CredentialInvalidError, match="relogin"):
        await client.fetch_notifications(NotificationType.MENTION)
    assert called == [("default", 401)]
    assert client.last_error["category"] == "credential_invalid"
    await client.close()
    await http_client.aclose()


async def test_credential_check_uses_observed_permission_endpoint() -> None:
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        return httpx.Response(200, json={"permission": {"comment": True}})

    client, http_client = client_with_handler(handler)
    result = await client.check_credentials()
    assert observed == ["/bbs/app/api/user/permission"]
    assert result == {"permission": {"comment": True}}
    await client.close()
    await http_client.aclose()


async def test_notification_shape_error_keeps_only_safe_structure_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {
                    "messages": [
                        {
                            "message_type": 16,
                            "comment_a_text": "private notification text",
                        }
                    ]
                },
            },
        )

    client, http_client = client_with_handler(handler)
    with pytest.raises(ResponseContractError):
        await client.fetch_notifications(NotificationType.MENTION)
    details = client.last_error["details"]
    assert details["raw_count"] == 1
    assert "comment_a_text" in details["item_fields"]
    assert "private notification text" not in str(details)
    await client.close()
    await http_client.aclose()


async def test_reference_qr_contract_preserves_query_and_reads_cookie_uid() -> None:
    observed_query = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/account/get_qrcode_url/":
            return httpx.Response(
                200,
                json=load_fixture("qr_reference_response.json"),
                headers={"set-cookie": "qr_session=mock-session; Path=/; Secure"},
            )
        observed_query.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=load_fixture("qr_direct_credentials_success.json"),
        )

    client, http_client = client_with_handler(handler, authenticated=False)
    qr = await client.request_qr()
    assert qr.poll_params == {"qr_ticket": "mock-ticket", "device": "web"}
    assert qr.request_id

    state, _, result_credentials = await client.check_qr(qr)

    assert state.value == "success"
    assert "request_id" not in observed_query
    assert observed_query["qr_ticket"] == "mock-ticket"
    assert observed_query["device"] == "web"
    assert result_credentials.uid == "10001"
    assert result_credentials.nickname == "MockUser"
    assert result_credentials.cookies["qr_session"] == "mock-session"
    assert result_credentials.cookies["pkey"] == "redacted-fixture-pkey"
    assert result_credentials.cookies["x_xhh_tokenid"]
    assert len(result_credentials.device_id) == 32
    assert observed_query["app"] == "web"
    assert observed_query["web_version"] == "2.5"
    assert observed_query["device_id"] == result_credentials.device_id
    await client.close()
    await http_client.aclose()


async def test_qr_request_and_state_share_complete_web_client_identity() -> None:
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(dict(request.url.params))
        if request.url.path == "/account/get_qrcode_url/":
            return httpx.Response(200, json=load_fixture("qr_reference_response.json"))
        return httpx.Response(
            200,
            json=load_fixture("qr_direct_credentials_success.json"),
        )

    client, http_client = client_with_handler(handler, authenticated=False)
    qr = await client.request_qr()
    await client.check_qr(qr)

    assert len(observed) == 2
    assert observed[0]["os_type"] == observed[1]["os_type"] == "web"
    assert observed[0]["app"] == observed[1]["app"] == "web"
    assert observed[0]["web_version"] == observed[1]["web_version"] == "2.5"
    assert observed[0]["device_id"] == observed[1]["device_id"]
    assert len(observed[0]["device_id"]) == 32
    assert observed[0]["hkey"] != observed[1]["hkey"]
    await client.close()
    await http_client.aclose()


async def test_qr_success_uses_state_response_without_unverified_restore_request() -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/account/get_qrcode_url/":
            payload = load_fixture("qr_reference_response.json")
            return httpx.Response(200, json=payload)
        payload = {
            "status": "ok",
            "result": {
                "error": "ok",
                "heyboxid": "10001",
                "nickname": "MockUser",
            },
        }
        return httpx.Response(
            200,
            json=payload,
            headers={"set-cookie": "pkey=redacted-state-pkey; Path=/; Secure"},
        )

    client, http_client = client_with_handler(handler, authenticated=False)
    qr = await client.request_qr()
    state, _, result_credentials = await client.check_qr(qr)

    assert state.value == "success"
    assert paths == ["/account/get_qrcode_url/", "/account/qr_state/"]
    assert result_credentials.uid == "10001"
    assert result_credentials.cookies["pkey"] == "redacted-state-pkey"
    assert result_credentials.cookies["x_xhh_tokenid"]
    assert len(result_credentials.device_id) == 32
    await client.close()
    await http_client.aclose()


async def test_comment_send_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bbs/app/comment/create"
        assert request.url.host == "workshopapi.xiaoheihe.cn"
        assert request.headers["origin"] == "https://www.xiaoheihe.cn"
        assert request.headers["referer"] == "https://www.xiaoheihe.cn/"
        body = parse_qs(request.content.decode("utf-8"))
        assert body == {
            "is_cy": ["0"],
            "link_id": ["30003"],
            "reply_id": ["parent-1"],
            "root_id": ["root-1"],
            "text": ["hello"],
        }
        return httpx.Response(200, json={"status": "ok", "commentid": "sent-comment-1"})

    client, http_client = client_with_handler(handler)
    result = await client.send_comment(
        RoutingTarget(
            profile_id="default",
            post_id="30003",
            root_comment_id="root-1",
            parent_comment_id="parent-1",
        ),
        "hello",
    )
    assert result.external_comment_id == "sent-comment-1"
    await client.close()
    await http_client.aclose()


async def test_comment_timeout_becomes_send_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client, http_client = client_with_handler(handler)
    with pytest.raises(SendUncertainError):
        await client.send_comment(RoutingTarget(profile_id="default", post_id="1"), "hello")
    await client.close()
    await http_client.aclose()


async def test_401_calls_invalidation_callback() -> None:
    called = []

    async def callback(profile_id, status):
        called.append((profile_id, status))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid"})

    client, http_client = client_with_handler(handler, callback=callback)
    with pytest.raises(CredentialInvalidError):
        await client.check_credentials()
    assert called == [("default", 401)]
    assert client.consecutive_status[401] == 1
    await client.close()
    await http_client.aclose()


async def test_owned_http_client_is_closed() -> None:
    client, http_client = client_with_handler(lambda request: httpx.Response(200, json={}))
    client._owns_client = True
    await client.close()
    assert http_client.is_closed
    await client.close()
