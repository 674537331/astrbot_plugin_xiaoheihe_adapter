from __future__ import annotations

import asyncio
import time

import pytest

from xiaoheihe.api_client import SendUncertainError
from xiaoheihe.models import (
    EventState,
    Notification,
    NotificationType,
    RoutingTarget,
    SendResult,
)
from xiaoheihe.runtime import RuntimeServices, bind_runtime, get_runtime, unbind_runtime


def notification() -> Notification:
    return Notification(
        profile_id="default",
        external_event_id="runtime-event",
        external_comment_id="runtime-comment",
        notification_id="runtime-notice",
        event_type=NotificationType.MENTION,
        sender_uid="user",
        sender_nickname="User",
        post_id="post",
        root_comment_id="root",
        parent_comment_id="root",
        content="hello",
        created_at=time.time(),
    )


async def test_runtime_dry_run_delivery_and_idempotent_close(tmp_path, fake_config) -> None:
    runtime = RuntimeServices(fake_config, tmp_path)
    await runtime.ensure_started()
    event_id = await runtime.repository.claim_event(notification())
    result = await runtime.deliver(
        event_id=event_id,
        route=RoutingTarget("default", "post", "root", "root"),
        content="dry reply",
        dry_run=True,
    )
    assert result["status"] == "dry_run"
    row = await runtime.repository.db.fetchone(
        "SELECT status, reply_text FROM incoming_events WHERE id = ?", (event_id,)
    )
    assert row["status"] == EventState.DRY_RUN.value
    assert row["reply_text"] == "dry reply"
    await runtime.close()
    await runtime.close()


async def test_runtime_dry_run_can_remain_replayable(tmp_path, fake_config) -> None:
    fake_config["reply"]["dry_run_mark_processed"] = False
    runtime = RuntimeServices(fake_config, tmp_path)
    await runtime.ensure_started()
    event_id = await runtime.repository.claim_event(notification())
    await runtime.deliver(
        event_id=event_id,
        route=RoutingTarget("default", "post", "root", "root"),
        content="dry reply",
        dry_run=True,
    )
    row = await runtime.repository.db.fetchone(
        "SELECT status, next_retry_at FROM incoming_events WHERE id = ?",
        (event_id,),
    )
    assert row["status"] == EventState.RETRY_WAIT.value
    assert row["next_retry_at"] > time.time()
    await runtime.close()


async def test_runtime_status_has_no_credentials(tmp_path, fake_config) -> None:
    runtime = RuntimeServices(fake_config, tmp_path)
    status = await runtime.status()
    assert status["version"] == "v1.0.6"
    assert status["profiles"][0]["has_credentials"] is False
    assert status["database_size"] >= 0
    await runtime.close()


async def test_cleanup_loop_recovers_after_transient_failure(
    tmp_path,
    fake_config,
    monkeypatch,
) -> None:
    runtime = RuntimeServices(fake_config, tmp_path)
    attempts = 0
    delays = []

    async def cleanup_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database is locked")
        raise asyncio.CancelledError

    async def no_wait(delay):
        delays.append(delay)

    monkeypatch.setattr(runtime, "_run_cleanup_once", cleanup_once)
    monkeypatch.setattr(runtime, "_cleanup_sleep", no_wait)

    with pytest.raises(asyncio.CancelledError):
        await runtime._cleanup_loop()

    assert attempts == 2
    assert delays[:2] == [60, 3600]
    assert runtime.tasks.failures() == []
    assert "cleanup" in runtime._alerts
    await runtime.close()


async def test_runtime_auth_circuit_alert(tmp_path, fake_config) -> None:
    runtime = RuntimeServices(fake_config, tmp_path)
    await runtime.ensure_started()
    await runtime._on_auth_invalid("default", 401)
    state = await runtime.repository.account_state("default")
    assert state["status"] == "credential_invalid"
    assert state["circuit_open_until"] > time.time()
    assert (await runtime.status())["alerts"]
    await runtime.close()


async def test_runtime_client_pool_and_binding(tmp_path, fake_config) -> None:
    runtime = RuntimeServices(fake_config, tmp_path)
    bind_runtime(runtime)
    assert get_runtime() is runtime
    first = await runtime.get_client("default", anonymous=True)
    second = await runtime.get_client("default", anonymous=True)
    assert first is second
    await runtime.invalidate_client("default")
    assert first.closed
    unbind_runtime(runtime)
    with pytest.raises(RuntimeError):
        get_runtime()
    await runtime.close()


class FakeSendingClient:
    def __init__(self, result=None, error=None) -> None:
        self.credentials = type("Cred", (), {"uid": "bot"})()
        self.result = result or SendResult("sent-1", True)
        self.error = error
        self.comments = []

    async def send_comment(self, route, content):
        if self.error:
            raise self.error
        return self.result

    async def recent_comments(self, route, limit=20):
        return self.comments


async def test_runtime_real_delivery_confirms_self_comment(tmp_path, fake_config) -> None:
    runtime = RuntimeServices(fake_config, tmp_path)
    await runtime.ensure_started()
    event_id = await runtime.repository.claim_event(notification())
    client = FakeSendingClient()

    async def get_client(profile_id, anonymous=False):
        return client

    runtime.get_client = get_client
    result = await runtime.deliver(
        event_id=event_id,
        route=RoutingTarget("default", "post", "root", "root"),
        content="real reply",
        dry_run=False,
    )
    assert result["external_comment_id"] == "sent-1"
    assert await runtime.repository.is_self_comment("default", "sent-1")
    await runtime.close()


async def test_runtime_send_unknown_never_blindly_retries(tmp_path, fake_config) -> None:
    runtime = RuntimeServices(fake_config, tmp_path)
    await runtime.ensure_started()
    event_id = await runtime.repository.claim_event(notification())
    client = FakeSendingClient(error=SendUncertainError("unknown", category="send_unknown"))

    async def get_client(profile_id, anonymous=False):
        return client

    runtime.get_client = get_client
    with pytest.raises(SendUncertainError):
        await runtime.deliver(
            event_id=event_id,
            route=RoutingTarget("default", "post", "root", "root"),
            content="maybe sent",
            dry_run=False,
        )
    row = await runtime.repository.db.fetchone(
        "SELECT status FROM incoming_events WHERE id = ?", (event_id,)
    )
    assert row["status"] == "send_unknown"
    outgoing = await runtime.repository.db.fetchone(
        "SELECT status FROM outgoing_replies WHERE incoming_event_id = ?", (event_id,)
    )
    assert outgoing["status"] == "send_unknown"
    await runtime.close()


async def test_runtime_timeout_is_confirmed_only_by_recent_matching_comment(
    tmp_path, fake_config
) -> None:
    runtime = RuntimeServices(fake_config, tmp_path)
    await runtime.ensure_started()
    event_id = await runtime.repository.claim_event(notification())
    client = FakeSendingClient(error=SendUncertainError("unknown", category="send_unknown"))
    client.comments = [
        {
            "id": "confirmed-after-timeout",
            "content": "maybe sent",
            "created_at": time.time(),
            "user": {"uid": "bot"},
        }
    ]

    async def get_client(profile_id, anonymous=False):
        return client

    runtime.get_client = get_client
    result = await runtime.deliver(
        event_id=event_id,
        route=RoutingTarget("default", "post", "root", "root"),
        content="maybe sent",
        dry_run=False,
    )
    assert result["confirmed_after_timeout"] is True
    assert result["external_comment_id"] == "confirmed-after-timeout"
    row = await runtime.repository.db.fetchone(
        "SELECT status FROM incoming_events WHERE id = ?", (event_id,)
    )
    assert row["status"] == EventState.SENT.value
    assert await runtime.repository.is_self_comment("default", "confirmed-after-timeout")
    await runtime.close()


async def test_runtime_capture_candidate_and_floor_lock_bound(tmp_path, fake_config) -> None:
    runtime = RuntimeServices(fake_config, tmp_path)
    await runtime.ensure_started()
    event_id = await runtime.repository.claim_event(notification())
    candidate_id = await runtime.capture_feed_candidate(
        event_id=event_id,
        route=RoutingTarget("default", "post"),
        content="candidate",
        metadata={
            "post_title": "Title",
            "post_author_uid": "author",
            "candidate_reason": "reason",
        },
    )
    assert candidate_id
    assert runtime._floor_lock(RoutingTarget("default", "post")) is (
        runtime._floor_lock(RoutingTarget("default", "post"))
    )
    await runtime._on_auth_invalid("default", 403)
    assert (await runtime.repository.account_state("default"))["consecutive_403"] == 1
    await runtime.close()
