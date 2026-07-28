from __future__ import annotations

import asyncio
import itertools
import json
import random
import time
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from .api_client import XiaoheiheApiClient, XiaoheiheApiError
from .context_builder import BuiltContext, ContextBuilder
from .logging_service import LoggingService
from .models import EventState, Notification, NotificationType, PermissionDecision
from .permission_service import PermissionService
from .repository import Repository
from .task_manager import TaskManager

DispatchCallback = Callable[[int, Notification, BuiltContext, PermissionDecision], Awaitable[None]]


class NotificationService:
    def __init__(
        self,
        profile: dict[str, Any],
        config: dict[str, Any],
        client: XiaoheiheApiClient,
        repository: Repository,
        task_manager: TaskManager,
        logger: LoggingService,
        permission_service: PermissionService,
        context_builder: ContextBuilder,
        dispatch: DispatchCallback,
    ) -> None:
        self.profile = profile
        self.profile_id = str(profile["profile_id"])
        self.config = config
        self.client = client
        self.repository = repository
        self.tasks = task_manager
        self.logger = logger
        self.permissions = permission_service
        self.context_builder = context_builder
        self.dispatch = dispatch
        network = config["network"]
        self._queue: asyncio.PriorityQueue[tuple[int, int, Notification]] = asyncio.PriorityQueue(
            maxsize=int(network["max_pending_events"])
        )
        self._max_per_user = int(network["max_pending_per_user"])
        self._pending_per_user: Counter[str] = Counter()
        self._recovery_ids: dict[str, int] = {}
        self._floor_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._poll_summary_signatures: dict[str, tuple[Any, ...]] = {}
        self._sequence = itertools.count()
        self._stop = asyncio.Event()
        self._startup_time = time.time()

    async def run(self) -> None:
        # Restore persisted work before polling starts.  This keeps a due retry from
        # being claimed both by startup recovery and by a freshly fetched copy.
        await self.recover_pending()
        workers = int(self.config["network"]["max_reply_concurrency"])
        for index in range(workers):
            await self.tasks.start(
                f"xhh-worker-{self.profile_id}-{index}",
                self._worker(),
            )
        try:
            await self._poll_loop()
        finally:
            await self.tasks.cancel_prefix(f"xhh-worker-{self.profile_id}-")

    async def _poll_loop(self) -> None:
        polling = self.config["polling"]
        interval = float(polling["poll_interval_seconds"])
        jitter = float(polling["poll_jitter_seconds"])
        while not self._stop.is_set():
            try:
                await self.poll_once()
                await self.repository.update_account_state(
                    self.profile_id,
                    last_poll_at=_iso_now(),
                    consecutive_poll_failures=0,
                    last_error="",
                )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                state = await self.repository.account_state(self.profile_id)
                failures = int(state.get("consecutive_poll_failures", 0)) + 1
                await self.repository.update_account_state(
                    self.profile_id,
                    consecutive_poll_failures=failures,
                    last_error=str(exc),
                )
                await self.repository.add_runtime_error(
                    "polling",
                    str(exc),
                    profile_id=self.profile_id,
                )
                self.logger.emit(
                    "ERROR",
                    f"通知轮询失败（连续 {failures} 次）: {exc}",
                    profile_id=self.profile_id,
                )
            delay = max(30.0, interval + random.uniform(0.0, max(0.0, jitter)))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                continue

    async def poll_once(self) -> None:
        account = await self.repository.account_state(self.profile_id)
        first_run = not bool(account.get("last_poll_at"))
        baseline = self._startup_time
        backfill = int(self.config["polling"]["initial_backfill_count"])
        event_types: list[NotificationType] = []
        if self.profile.get("poll_mentions", True):
            event_types.append(NotificationType.MENTION)
        if self.profile.get("poll_replies", True):
            event_types.append(NotificationType.REPLY)
        for event_type in event_types:
            remaining_backfill = backfill
            cursor = ""
            max_pages = int(self.config["polling"]["max_pages_per_poll"])
            for page_index in range(1, max_pages + 1):
                page = await self.client.fetch_notifications(
                    event_type,
                    cursor=cursor,
                    page=page_index,
                    page_size=int(self.config["polling"]["page_size"]),
                )
                self._report_poll_summary(event_type, page_index)
                for wrapper in page.items:
                    notification = wrapper["notification"]
                    if first_run and notification.created_at < baseline:
                        if remaining_backfill <= 0:
                            continue
                        remaining_backfill -= 1
                    await self.enqueue(notification)
                if not page.has_more:
                    break
                cursor = page.next_cursor
                if not cursor and page_index >= max_pages:
                    break

    def _report_poll_summary(self, event_type: NotificationType, page_index: int) -> None:
        summaries = getattr(self.client, "last_notification_polls", {})
        summary = summaries.get(event_type.value, {})
        raw_count = int(summary.get("raw_count") or 0)
        accepted_count = int(summary.get("accepted_count") or 0)
        message_types = tuple(str(value) for value in summary.get("message_types", []))
        signature = (raw_count, accepted_count, message_types)
        if self._poll_summary_signatures.get(event_type.value) == signature:
            return
        self._poll_summary_signatures[event_type.value] = signature
        level = (
            "WARNING"
            if event_type is NotificationType.MENTION and raw_count > 0 and not accepted_count
            else "INFO"
        )
        self.logger.emit(
            level,
            (f"{event_type.value} 通知页结构：原始 {raw_count} 条，接收 {accepted_count} 条"),
            profile_id=self.profile_id,
            details={
                "event_type": event_type.value,
                "page": page_index,
                "raw_count": raw_count,
                "accepted_count": accepted_count,
                "message_types": list(message_types),
                "result_type": summary.get("result_type", ""),
                "list_field": summary.get("list_field", ""),
            },
        )

    async def enqueue(self, notification: Notification) -> bool:
        uid = str(notification.sender_uid)
        if self._pending_per_user[uid] >= self._max_per_user:
            self.logger.emit(
                "WARNING",
                "单用户待处理事件达到上限，保留通知供下轮去重处理",
                profile_id=self.profile_id,
                details={"sender_uid": uid},
            )
            return False
        decision = self.permissions.decide(notification)
        priority = 0 if decision.is_owner else 10
        try:
            self._queue.put_nowait((priority, next(self._sequence), notification))
        except asyncio.QueueFull:
            self.logger.emit(
                "WARNING",
                "待处理事件队列已满，停止本轮入队",
                profile_id=self.profile_id,
            )
            return False
        self._pending_per_user[uid] += 1
        return True

    async def recover_pending(self) -> int:
        recovered = 0
        rows = await self.repository.recoverable_events(
            limit=int(self.config["network"]["max_pending_events"]),
            profile_id=self.profile_id,
        )
        for row in rows:
            if str(row["profile_id"]) != self.profile_id:
                continue
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                raw = {}
            notification = Notification(
                profile_id=self.profile_id,
                external_event_id=str(row["external_event_id"]),
                external_comment_id=str(row["external_comment_id"]),
                notification_id=str(row["notification_id"]),
                event_type=NotificationType(str(row["event_type"])),
                sender_uid=str(row["sender_uid"]),
                sender_nickname=str(row["sender_nickname"]),
                post_id=str(row["post_id"]),
                root_comment_id=str(row["root_comment_id"]),
                parent_comment_id=str(row["parent_comment_id"]),
                content=str(row.get("content") or ""),
                created_at=float(row["discovered_at"]),
                post_author_uid=str(raw.get("post_author_uid", "")),
                explicit_wake=True,
                image_urls=[
                    str(value) for value in raw.get("image_urls", []) if isinstance(value, str)
                ],
                raw=raw if isinstance(raw, dict) else {},
            )
            event_id = int(row["id"])
            if str(row["status"]) == EventState.RETRY_WAIT.value:
                claimed_id = await self.repository.claim_retry_event(notification)
                if claimed_id is None:
                    continue
                event_id = claimed_id
            self._recovery_ids[notification.external_event_id] = event_id
            if await self.enqueue(notification):
                recovered += 1
            else:
                self._recovery_ids.pop(notification.external_event_id, None)
        return recovered

    async def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                _, _, notification = await self._queue.get()
            except asyncio.CancelledError:
                raise
            uid = str(notification.sender_uid)
            try:
                existing_id = self._recovery_ids.pop(notification.external_event_id, None)
                await self._handle(notification, event_id=existing_id)
            except asyncio.CancelledError:
                raise
            except XiaoheiheApiError as exc:
                self.logger.emit(
                    "ERROR",
                    f"事件处理 API 错误: {exc}",
                    profile_id=self.profile_id,
                    details={"category": exc.category},
                )
            except BaseException as exc:
                self.logger.emit(
                    "ERROR",
                    f"事件处理失败: {exc}",
                    profile_id=self.profile_id,
                )
            finally:
                self._pending_per_user[uid] -= 1
                if self._pending_per_user[uid] <= 0:
                    self._pending_per_user.pop(uid, None)
                self._queue.task_done()

    async def _handle(self, notification: Notification, *, event_id: int | None = None) -> None:
        if await self.repository.is_self_comment(
            notification.profile_id, notification.external_comment_id
        ):
            return
        event_id = event_id or await self.repository.claim_event(notification)
        if event_id is None:
            event_id = await self.repository.claim_retry_event(notification)
        if event_id is None:
            return
        decision = self.permissions.decide(notification)
        if not decision.allowed:
            await self.repository.mark_event(
                event_id,
                EventState.IGNORED,
                error=decision.reason,
                should_filter=True,
            )
            return
        try:
            context = await self.context_builder.build(
                notification,
                self.client,
            )
            await self.repository.mark_event(event_id, EventState.CONTEXT_READY)
            async with self._floor_lock(notification.route.session_id):
                await self.repository.record_session(notification.route)
                await self.repository.mark_event(event_id, EventState.DISPATCHED)
                await self.dispatch(event_id, notification, context, decision)
        except BaseException as exc:
            await self.repository.schedule_retry(
                event_id,
                str(exc),
                max_retries=int(self.config["reply"]["max_retries"]),
            )
            raise

    def _floor_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._floor_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._floor_locks[session_id] = lock
        self._floor_locks.move_to_end(session_id)
        while len(self._floor_locks) > 1024:
            old_key, old_lock = next(iter(self._floor_locks.items()))
            if old_lock.locked():
                self._floor_locks.move_to_end(old_key)
                break
            self._floor_locks.pop(old_key, None)
        return lock

    async def stop(self) -> None:
        self._stop.set()
        with suppress(TimeoutError):
            await asyncio.wait_for(self._queue.join(), timeout=5)
        self._floor_locks.clear()


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
