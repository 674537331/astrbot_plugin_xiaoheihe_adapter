from __future__ import annotations

import asyncio
import itertools
import json
import random
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
        self._pending_event_keys: set[str] = set()
        self._limit_warning_users: set[str] = set()
        self._recovery_ids: dict[str, int] = {}
        self._floor_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._poll_summary_signatures: dict[str, tuple[Any, ...]] = {}
        self._sequence = itertools.count()
        self._stop = asyncio.Event()

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
            except Exception as exc:
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
        await self.recover_retries()
        event_types: list[NotificationType] = []
        if self.profile.get("poll_mentions", True):
            event_types.append(NotificationType.MENTION)
        if self.profile.get("poll_replies", True):
            event_types.append(NotificationType.REPLY)
        for event_type in event_types:
            await self._poll_event_type(event_type)

    async def _poll_event_type(self, event_type: NotificationType) -> None:
        stored_cursor = await self.repository.notification_cursor(
            self.profile_id,
            event_type.value,
        )
        page_cursor = ""
        newest_event_id = ""
        boundary_reached = False
        exhausted = False
        all_new_events_durable = True
        first_observation = stored_cursor is None
        remaining_backfill = int(self.config["polling"]["initial_backfill_count"])
        max_pages = int(self.config["polling"]["max_pages_per_poll"])
        page_size = int(self.config["polling"]["page_size"])
        pages_used = 0

        for page_index in range(1, max_pages + 1):
            pages_used = page_index
            page = await self.client.fetch_notifications(
                event_type,
                cursor=page_cursor,
                page=page_index,
                page_size=page_size,
            )
            self._report_poll_summary(event_type, page_index)
            notifications = [wrapper["notification"] for wrapper in page.items]
            if notifications and not newest_event_id:
                newest_event_id = notifications[0].external_event_id

            if first_observation:
                if page_index == 1:
                    await self.repository.initialize_notification_cursor(
                        self.profile_id,
                        event_type.value,
                        newest_event_id,
                    )
                    self.logger.emit(
                        "INFO",
                        f"{event_type.value} 通知历史基线已建立",
                        profile_id=self.profile_id,
                        details={
                            "latest_event_id": newest_event_id,
                            "initial_backfill_count": remaining_backfill,
                        },
                    )
                for notification in notifications:
                    if remaining_backfill <= 0:
                        break
                    remaining_backfill -= 1
                    queued = await self.enqueue(notification)
                    if not queued and not await self.repository.event_exists(
                        notification.profile_id,
                        notification.external_event_id,
                    ):
                        all_new_events_durable = False
                if remaining_backfill <= 0:
                    break
            else:
                for notification in notifications:
                    if _event_at_or_before(
                        notification.external_event_id,
                        stored_cursor,
                    ):
                        boundary_reached = True
                        break
                    queued = await self.enqueue(notification)
                    if not queued and not await self.repository.event_exists(
                        notification.profile_id,
                        notification.external_event_id,
                    ):
                        all_new_events_durable = False
                if boundary_reached:
                    break

            if not page.has_more:
                exhausted = True
                break
            page_cursor = page.next_cursor

        if first_observation:
            return
        if not all_new_events_durable:
            return
        if newest_event_id:
            if not (boundary_reached or exhausted):
                if _event_is_newer(newest_event_id, stored_cursor):
                    next_offset = _notification_offset(
                        page_cursor,
                        fallback=pages_used * page_size,
                    )
                    persisted = await self.repository.advance_notification_cursor_with_backfill(
                        self.profile_id,
                        event_type.value,
                        stored_cursor,
                        newest_event_id,
                        next_offset=next_offset,
                    )
                    if persisted:
                        self.logger.emit(
                            "WARNING",
                            (
                                f"{event_type.value} 新通知超过单轮最大页数，"
                                "剩余区间已持久化并将在后续轮询继续回填"
                            ),
                            profile_id=self.profile_id,
                            details={
                                "max_pages": max_pages,
                                "next_offset": next_offset,
                            },
                        )
                return
            if _event_is_newer(newest_event_id, stored_cursor):
                await self.repository.advance_notification_cursor(
                    self.profile_id,
                    event_type.value,
                    stored_cursor,
                    newest_event_id,
                )

        remaining_pages = max(0, max_pages - pages_used)
        if remaining_pages:
            await self._poll_backfill(event_type, page_budget=remaining_pages)

    async def _poll_backfill(
        self,
        event_type: NotificationType,
        *,
        page_budget: int,
    ) -> None:
        state = await self.repository.notification_backfill(
            self.profile_id,
            event_type.value,
        )
        if state is None or page_budget <= 0:
            return
        boundary_event_id = str(state["boundary_event_id"])
        page_size = int(self.config["polling"]["page_size"])
        current_offset = max(0, int(state["next_offset"]))
        boundary_reached = False
        exhausted = False
        all_events_durable = True

        for _ in range(page_budget):
            page_number = max(1, current_offset // max(1, page_size) + 1)
            page = await self.client.fetch_notifications(
                event_type,
                cursor=str(current_offset),
                page=page_number,
                page_size=page_size,
            )
            self._report_poll_summary(event_type, page_number)
            notifications = [wrapper["notification"] for wrapper in page.items]
            for notification in notifications:
                if _event_at_or_before(
                    notification.external_event_id,
                    boundary_event_id,
                ):
                    boundary_reached = True
                    break
                queued = await self.enqueue(notification)
                if not queued and not await self.repository.event_exists(
                    notification.profile_id,
                    notification.external_event_id,
                ):
                    all_events_durable = False
            if boundary_reached:
                break
            if not page.has_more:
                exhausted = True
                break
            next_offset = _notification_offset(
                page.next_cursor,
                fallback=current_offset + page_size,
            )
            if next_offset <= current_offset:
                self.logger.emit(
                    "WARNING",
                    f"{event_type.value} 历史通知回填分页未推进，保留当前位置等待下轮重试",
                    profile_id=self.profile_id,
                    details={"current_offset": current_offset},
                )
                return
            current_offset = next_offset

        if not all_events_durable:
            return
        if boundary_reached or exhausted:
            cleared = await self.repository.clear_notification_backfill(
                self.profile_id,
                event_type.value,
                boundary_event_id,
            )
            if cleared:
                self.logger.emit(
                    "INFO",
                    f"{event_type.value} 历史通知回填完成",
                    profile_id=self.profile_id,
                    details={"boundary_event_id": boundary_event_id},
                )
            return
        await self.repository.advance_notification_backfill(
            self.profile_id,
            event_type.value,
            boundary_event_id,
            next_offset=current_offset,
        )

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

    async def enqueue(self, notification: Notification, *, recovered: bool = False) -> bool:
        uid = str(notification.sender_uid)
        event_key = notification.external_event_id
        if event_key in self._pending_event_keys:
            return False
        event_id = self._recovery_ids.get(event_key) if recovered else None
        if not recovered:
            event_id = await self.repository.claim_event(notification)
            if event_id is None:
                event_id = await self.repository.claim_retry_event(notification)
            if event_id is None:
                return False
        if self._pending_per_user[uid] >= self._max_per_user:
            if event_id is not None:
                await self.repository.defer_event(
                    event_id,
                    "单用户待处理事件达到上限，等待持久化重试",
                    delay_seconds=float(self.config["polling"]["poll_interval_seconds"]),
                )
                self._recovery_ids.pop(event_key, None)
            if uid not in self._limit_warning_users:
                self._limit_warning_users.add(uid)
                self.logger.emit(
                    "WARNING",
                    "单用户待处理事件达到上限，保留通知供后续轮询处理",
                    profile_id=self.profile_id,
                    details={"sender_uid": uid},
                )
            return False
        if self._queue.full():
            if event_id is not None:
                await self.repository.defer_event(
                    event_id,
                    "待处理队列已满，等待持久化重试",
                    delay_seconds=float(self.config["polling"]["poll_interval_seconds"]),
                )
                self._recovery_ids.pop(event_key, None)
            self.logger.emit(
                "WARNING",
                "待处理事件队列已满，停止本轮入队",
                profile_id=self.profile_id,
            )
            return False
        if event_id is None:
            return False
        self._recovery_ids[event_key] = event_id
        decision = self.permissions.decide(notification)
        priority = 0 if decision.is_owner else 10
        try:
            self._queue.put_nowait((priority, next(self._sequence), notification))
        except asyncio.QueueFull:
            if event_id is not None:
                await self.repository.defer_event(
                    event_id,
                    "待处理队列已满，等待后续轮询恢复",
                    delay_seconds=float(self.config["polling"]["poll_interval_seconds"]),
                )
                self._recovery_ids.pop(event_key, None)
            self.logger.emit(
                "WARNING",
                "待处理事件队列已满，停止本轮入队",
                profile_id=self.profile_id,
            )
            return False
        self._pending_per_user[uid] += 1
        self._pending_event_keys.add(event_key)
        return True

    async def recover_retries(self) -> int:
        recovered = 0
        rows = await self.repository.due_retry_events(
            limit=int(self.config["network"]["max_pending_events"]),
            profile_id=self.profile_id,
        )
        for row in rows:
            notification = _notification_from_row(row, self.profile_id)
            if await self.enqueue(notification):
                recovered += 1
        return recovered

    async def recover_pending(self) -> int:
        recovered = 0
        rows = await self.repository.recoverable_events(
            limit=int(self.config["network"]["max_pending_events"]),
            profile_id=self.profile_id,
        )
        for row in rows:
            if str(row["profile_id"]) != self.profile_id:
                continue
            event_id = int(row["id"])
            status = str(row["status"])
            outgoing = await self.repository.latest_outgoing_for_event(event_id)
            if outgoing is not None:
                outgoing_status = str(outgoing.get("status") or "")
                if outgoing_status == EventState.SENT.value:
                    await self.repository.mark_event(
                        event_id,
                        EventState.SENT,
                        reply_text=str(outgoing.get("content") or ""),
                    )
                elif outgoing_status == EventState.DRY_RUN.value:
                    await self.repository.mark_event(
                        event_id,
                        EventState.DRY_RUN,
                        reply_text=str(outgoing.get("content") or ""),
                    )
                elif outgoing_status in {"sending", EventState.SEND_UNKNOWN.value}:
                    await self.repository.mark_event(
                        event_id,
                        EventState.SEND_UNKNOWN,
                        error="插件重启时发现已有未确认的评论发送记录，已停止自动重发",
                    )
                else:
                    await self.repository.mark_event(
                        event_id,
                        EventState.DEAD_LETTER,
                        error="插件重启时发现已有失败的评论发送记录，已停止自动重发",
                    )
                continue
            if status == EventState.DISPATCHED.value:
                await self.repository.mark_event(
                    event_id,
                    EventState.DEAD_LETTER,
                    error="插件重启时事件已进入 AstrBot 管线，无法确认旧管线结果，已停止重复分发",
                )
                continue
            notification = _notification_from_row(row, self.profile_id)
            if str(row["status"]) == EventState.RETRY_WAIT.value:
                claimed_id = await self.repository.claim_retry_event(notification)
                if claimed_id is None:
                    continue
                event_id = claimed_id
            self._recovery_ids[notification.external_event_id] = event_id
            if await self.enqueue(notification, recovered=True):
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
            except Exception as exc:
                self.logger.emit(
                    "ERROR",
                    f"事件处理失败: {exc}",
                    profile_id=self.profile_id,
                )
            finally:
                self._pending_per_user[uid] -= 1
                if self._pending_per_user[uid] <= 0:
                    self._pending_per_user.pop(uid, None)
                if self._pending_per_user.get(uid, 0) < self._max_per_user:
                    self._limit_warning_users.discard(uid)
                self._pending_event_keys.discard(notification.external_event_id)
                self._queue.task_done()

    async def _handle(self, notification: Notification, *, event_id: int | None = None) -> None:
        event_id = event_id or self._recovery_ids.pop(
            notification.external_event_id,
            None,
        )
        if await self.repository.is_self_comment(
            notification.profile_id, notification.external_comment_id
        ):
            if event_id is not None:
                await self.repository.mark_event(
                    event_id,
                    EventState.IGNORED,
                    error="机器人自身评论通知",
                    should_filter=True,
                )
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
        except asyncio.CancelledError as exc:
            await self.repository.schedule_retry(
                event_id,
                str(exc) or "事件处理任务被取消",
                max_retries=int(self.config["reply"]["max_retries"]),
            )
            raise
        except Exception as exc:
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
        self._pending_event_keys.clear()
        self._limit_warning_users.clear()


def _numeric_event_id(value: str) -> int | None:
    normalized = str(value).strip()
    if not normalized.isdecimal():
        return None
    return int(normalized)


def _notification_offset(value: str, *, fallback: int) -> int:
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return max(0, int(fallback))


def _event_at_or_before(event_id: str, stored_cursor: str) -> bool:
    if not stored_cursor:
        return False
    current_number = _numeric_event_id(event_id)
    cursor_number = _numeric_event_id(stored_cursor)
    if current_number is not None and cursor_number is not None:
        return current_number <= cursor_number
    return str(event_id) == str(stored_cursor)


def _event_is_newer(event_id: str, stored_cursor: str) -> bool:
    if not stored_cursor:
        return bool(event_id)
    current_number = _numeric_event_id(event_id)
    cursor_number = _numeric_event_id(stored_cursor)
    if current_number is not None and cursor_number is not None:
        return current_number > cursor_number
    return str(event_id) != str(stored_cursor)


def _notification_from_row(row: dict[str, Any], profile_id: str) -> Notification:
    try:
        raw = json.loads(row.get("raw_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    timing = raw.get("_adapter_timing", {})
    timing = timing if isinstance(timing, dict) else {}
    discovered_at = float(row["discovered_at"])
    return Notification(
        profile_id=profile_id,
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
        created_at=_safe_time(timing.get("source_created_at"), discovered_at),
        observed_at=_safe_time(timing.get("observed_at"), discovered_at),
        post_author_uid=str(raw.get("post_author_uid", "")),
        explicit_wake=True,
        image_urls=[str(value) for value in raw.get("image_urls", []) if isinstance(value, str)],
        raw=raw,
    )


def _safe_time(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
