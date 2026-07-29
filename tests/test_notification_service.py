from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import replace

import pytest

from tests.helpers import load_fixture
from xiaoheihe.config_service import DEFAULT_CONFIG
from xiaoheihe.context_builder import BuiltContext
from xiaoheihe.models import ApiPage, EventState, Notification, NotificationType, ThreadContext
from xiaoheihe.notification_service import NotificationService
from xiaoheihe.parsers import parse_notifications
from xiaoheihe.permission_service import PermissionService
from xiaoheihe.task_manager import TaskManager


def notice(identifier: str, created_at: float) -> Notification:
    return Notification(
        profile_id="default",
        external_event_id=f"event-{identifier}",
        external_comment_id=f"comment-{identifier}",
        notification_id=f"notice-{identifier}",
        event_type=NotificationType.MENTION,
        sender_uid="user",
        sender_nickname="User",
        post_id="post",
        root_comment_id="root",
        parent_comment_id="root",
        content="hello",
        created_at=created_at,
    )


class FakeClient:
    def __init__(self, notifications):
        self.notifications = notifications

    async def fetch_notifications(self, *args, **kwargs):
        return ApiPage(items=[{"notification": item} for item in self.notifications])


class FakeContextBuilder:
    async def build(self, notification, client):
        return BuiltContext(
            user_text=notification.content,
            dynamic_context="<x/>",
            image_urls=[],
            warnings=[],
            thread=ThreadContext("post", "title", "body", "author", "A", []),
        )


class FakeLogging:
    def __init__(self):
        self.entries = []

    def emit(self, *args, **kwargs):
        self.entries.append((args, kwargs))


def make_service(repository, notifications, dispatch, *, backfill=0):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["polling"]["initial_backfill_count"] = backfill
    profile = config["profiles"][0]
    permission = PermissionService(
        config["permissions"],
        owner_uid="",
        self_uid="bot",
        only_explicit_mentions=True,
        reply_to_direct_replies=True,
    )
    return NotificationService(
        profile,
        config,
        FakeClient(notifications),
        repository,
        TaskManager(),
        FakeLogging(),
        permission,
        FakeContextBuilder(),
        dispatch,
    )


async def test_first_poll_does_not_enqueue_history(repository) -> None:
    dispatched = []

    async def dispatch(*args):
        dispatched.append(args)

    service = make_service(repository, [notice("old", time.time() - 60)], dispatch)
    await service.poll_once()
    assert service._queue.empty()


async def test_optional_first_backfill_and_worker_dispatch(repository) -> None:
    dispatched = []

    async def dispatch(*args):
        dispatched.append(args)

    service = make_service(repository, [notice("old", time.time() - 60)], dispatch, backfill=1)
    await service.poll_once()
    _, _, notification = await service._queue.get()
    await service._handle(notification)
    assert dispatched


async def test_real_type_17_mention_reaches_event_record(repository) -> None:
    parsed = parse_notifications(
        "default",
        load_fixture("notifications_reference_messages.json"),
        NotificationType.MENTION,
    )

    class FixtureClient:
        async def fetch_notifications(self, event_type, **kwargs):
            if event_type is NotificationType.MENTION:
                return parsed
            return ApiPage(items=[])

    async def dispatch(event_id, *args):
        await repository.mark_event(event_id, EventState.DRY_RUN, reply_text="fixture reply")

    await repository.update_account_state("default", last_poll_at="2026-07-29T00:00:00Z")
    service = make_service(repository, [], dispatch)
    service.client = FixtureClient()
    await service.poll_once()
    _, _, notification = service._queue.get_nowait()
    await service._handle(notification)
    row = await repository.db.fetchone(
        """
        SELECT status, event_type, external_comment_id, root_comment_id
        FROM incoming_events WHERE external_event_id = ?
        """,
        ("90001",),
    )
    assert dict(row) == {
        "status": EventState.DRY_RUN.value,
        "event_type": NotificationType.MENTION.value,
        "external_comment_id": "70001",
        "root_comment_id": "70000",
    }


async def test_self_comment_id_is_filtered(repository) -> None:
    notification = notice("self", time.time())
    await repository.db.execute(
        """
        INSERT INTO self_comment_ids(
            profile_id, external_comment_id, post_id, root_comment_id,
            content_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("default", notification.external_comment_id, "post", "root", "hash", time.time()),
    )
    dispatched = []

    async def dispatch(*args):
        dispatched.append(args)

    service = make_service(repository, [], dispatch)
    await service._handle(notification)
    assert not dispatched


async def test_enqueue_enforces_per_user_and_queue_limits(repository) -> None:
    async def dispatch(*args):
        return None

    service = make_service(repository, [], dispatch)
    service._max_per_user = 1
    assert await service.enqueue(notice("one", time.time()))
    assert not await service.enqueue(notice("two", time.time()))

    service._max_per_user = 10
    service._queue = service._queue.__class__(maxsize=1)
    service._pending_per_user.clear()
    service._pending_event_keys.clear()
    assert await service.enqueue(notice("three", time.time()))
    assert not await service.enqueue(replace(notice("four", time.time()), sender_uid="other"))


async def test_enqueue_skips_in_memory_and_completed_duplicates(repository) -> None:
    async def dispatch(*args):
        return None

    service = make_service(repository, [], dispatch)
    current = notice("duplicate", time.time())
    assert await service.enqueue(current)
    assert not await service.enqueue(current)
    assert service._queue.qsize() == 1
    assert service._pending_per_user["user"] == 1

    completed = notice("completed", time.time())
    event_id = await repository.claim_event(completed)
    assert event_id is not None
    await repository.mark_event(event_id, EventState.DRY_RUN, reply_text="done")
    assert not await service.enqueue(completed)
    assert service._queue.qsize() == 1

    retry = notice("retry-due", time.time())
    retry_id = await repository.claim_event(retry)
    assert retry_id is not None
    await repository.defer_event(retry_id, "later", delay_seconds=60)
    assert not await service.enqueue(retry)
    await repository.db.execute(
        "UPDATE incoming_events SET next_retry_at = 0 WHERE id = ?",
        (retry_id,),
    )
    assert await service.enqueue(retry)
    assert service._queue.qsize() == 2


async def test_handle_records_ignored_and_retry_wait(repository) -> None:
    async def dispatch(*args):
        return None

    service = make_service(repository, [], dispatch)
    service.permissions = PermissionService(
        {
            **copy.deepcopy(DEFAULT_CONFIG["permissions"]),
            "user_blacklist": ["user"],
        },
        owner_uid="",
        self_uid="bot",
        only_explicit_mentions=True,
        reply_to_direct_replies=True,
    )
    ignored = notice("ignored", time.time())
    await service._handle(ignored)
    row = await repository.db.fetchone(
        "SELECT status, should_filter FROM incoming_events WHERE external_event_id = ?",
        (ignored.external_event_id,),
    )
    assert dict(row) == {"status": EventState.IGNORED.value, "should_filter": 1}

    class BrokenBuilder:
        async def build(self, notification, client):
            raise RuntimeError("context failed")

    service.permissions = PermissionService(
        DEFAULT_CONFIG["permissions"],
        owner_uid="",
        self_uid="bot",
        only_explicit_mentions=True,
        reply_to_direct_replies=True,
    )
    service.context_builder = BrokenBuilder()
    failed = notice("failed", time.time())
    with pytest.raises(RuntimeError, match="context failed"):
        await service._handle(failed)
    row = await repository.db.fetchone(
        "SELECT status, error FROM incoming_events WHERE external_event_id = ?",
        (failed.external_event_id,),
    )
    assert row["status"] == EventState.RETRY_WAIT.value
    assert "context failed" in row["error"]


async def test_poll_uses_pages_and_skips_duplicate_claim(repository) -> None:
    calls = []
    dispatched = []

    class PagedClient:
        async def fetch_notifications(self, event_type, *, cursor, page, page_size):
            calls.append((event_type, cursor, page, page_size))
            if page == 1:
                return ApiPage(
                    items=[{"notification": notice("paged", time.time() + 10)}],
                    next_cursor="next",
                    has_more=True,
                )
            return ApiPage(
                items=[{"notification": notice("paged", time.time() + 10)}],
                has_more=False,
            )

    async def dispatch(*args):
        dispatched.append(args)

    service = make_service(repository, [], dispatch)
    service.client = PagedClient()
    await service.poll_once()
    assert len(calls) == 4  # two pages for mentions and two pages for replies
    assert service._queue.qsize() == 1
    assert service._pending_per_user["user"] == 1
    while not service._queue.empty():
        _, _, item = service._queue.get_nowait()
        await service._handle(item)
        service._queue.task_done()
    assert len(dispatched) == 1


async def test_worker_drains_one_item_and_stop_is_idempotent(repository) -> None:
    dispatched = []

    async def dispatch(*args):
        dispatched.append(args)

    service = make_service(repository, [], dispatch)
    assert await service.enqueue(notice("worker", time.time()))
    worker = __import__("asyncio").create_task(service._worker())
    await service._queue.join()
    assert not service._pending_event_keys
    assert not service._pending_per_user
    await service.stop()
    worker.cancel()
    with pytest.raises(__import__("asyncio").CancelledError):
        await worker
    assert dispatched


async def test_dispatch_cannot_overwrite_a_completed_event(repository) -> None:
    async def dispatch(event_id, *args):
        await repository.mark_event(event_id, EventState.DRY_RUN, reply_text="done")

    service = make_service(repository, [], dispatch)
    current = notice("completion-race", time.time())
    await service._handle(current)
    row = await repository.db.fetchone(
        "SELECT status, reply_text FROM incoming_events WHERE external_event_id = ?",
        (current.external_event_id,),
    )
    assert dict(row) == {"status": EventState.DRY_RUN.value, "reply_text": "done"}


async def test_startup_rebuilds_claimed_event_without_reinserting(repository) -> None:
    current = notice("restart", time.time())
    event_id = await repository.claim_event(current)
    dispatched = []

    async def dispatch(*args):
        dispatched.append(args)

    service = make_service(repository, [], dispatch)
    assert await service.recover_pending() == 1
    _, _, rebuilt = service._queue.get_nowait()
    existing_id = service._recovery_ids.pop(rebuilt.external_event_id)
    await service._handle(rebuilt, event_id=existing_id)
    assert existing_id == event_id
    assert len(dispatched) == 1
    count = await repository.db.fetchone(
        "SELECT COUNT(*) AS count FROM incoming_events WHERE external_event_id = ?",
        (current.external_event_id,),
    )
    assert count["count"] == 1


async def test_same_floor_dispatch_is_serialized(repository) -> None:
    active = 0
    peak = 0

    async def dispatch(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1

    service = make_service(repository, [], dispatch)
    first = notice("floor-one", time.time())
    second = notice("floor-two", time.time())
    await asyncio.gather(service._handle(first), service._handle(second))
    assert peak == 1
