from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import Database
from .models import EventState, Notification, RoutingTarget
from .security import redact_data, redact_text

FINAL_STATES = {
    EventState.SENT.value,
    EventState.DRY_RUN.value,
    EventState.IGNORED.value,
    EventState.DEAD_LETTER.value,
}


class Repository:
    def __init__(self, database: Database) -> None:
        self.db = database

    async def claim_event(self, notification: Notification) -> int | None:
        now = time.time()
        observed_at = notification.observed_at or now
        raw = dict(notification.raw)
        raw["_adapter_timing"] = {
            "source_created_at": notification.created_at,
            "observed_at": observed_at,
        }
        raw_json = json.dumps(
            redact_data(raw),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        async with self.db.transaction() as connection:
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO incoming_events(
                    profile_id, external_event_id, external_comment_id,
                    notification_id, event_type, status, sender_uid,
                    sender_nickname, post_id, root_comment_id, parent_comment_id,
                    content, raw_json, discovered_at, claimed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification.profile_id,
                    notification.external_event_id,
                    notification.external_comment_id,
                    notification.notification_id,
                    notification.event_type.value,
                    EventState.CLAIMED.value,
                    str(notification.sender_uid),
                    notification.sender_nickname,
                    notification.post_id,
                    notification.root_comment_id,
                    notification.parent_comment_id,
                    notification.content,
                    raw_json,
                    observed_at,
                    now,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                return None
            await connection.execute(
                """
                INSERT OR IGNORE INTO processed_event_keys(
                    profile_id, event_key, key_type, created_at
                ) VALUES (?, ?, 'notification', ?)
                """,
                (notification.profile_id, notification.external_event_id, now),
            )
            return int(cursor.lastrowid)

    async def claim_retry_event(self, notification: Notification) -> int | None:
        now = time.time()
        async with self.db.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE incoming_events
                SET status = 'claimed', claimed_at = ?, updated_at = ?, error = ''
                WHERE profile_id = ? AND external_event_id = ?
                  AND status = 'retry_wait'
                  AND COALESCE(next_retry_at, 0) <= ?
                """,
                (
                    now,
                    now,
                    notification.profile_id,
                    notification.external_event_id,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = await connection.execute(
                """
                SELECT id FROM incoming_events
                WHERE profile_id = ? AND external_event_id = ?
                """,
                (notification.profile_id, notification.external_event_id),
            )
            result = await row.fetchone()
            return int(result["id"]) if result else None

    async def is_event_queueable(self, profile_id: str, external_event_id: str) -> bool:
        row = await self.db.fetchone(
            """
            SELECT status, next_retry_at
            FROM incoming_events
            WHERE profile_id = ? AND external_event_id = ?
            """,
            (profile_id, external_event_id),
        )
        if row is None:
            return True
        return (
            str(row["status"]) == EventState.RETRY_WAIT.value
            and float(row["next_retry_at"] or 0) <= time.time()
        )

    async def event_exists(self, profile_id: str, external_event_id: str) -> bool:
        row = await self.db.fetchone(
            """
            SELECT 1 FROM incoming_events
            WHERE profile_id = ? AND external_event_id = ?
            """,
            (profile_id, external_event_id),
        )
        return row is not None

    async def notification_cursor(
        self,
        profile_id: str,
        event_type: str,
    ) -> str | None:
        row = await self.db.fetchone(
            """
            SELECT last_event_id FROM notification_cursors
            WHERE profile_id = ? AND event_type = ?
            """,
            (profile_id, event_type),
        )
        return str(row["last_event_id"]) if row is not None else None

    async def initialize_notification_cursor(
        self,
        profile_id: str,
        event_type: str,
        last_event_id: str,
    ) -> bool:
        now = time.time()
        cursor = await self.db.execute(
            """
            INSERT OR IGNORE INTO notification_cursors(
                profile_id, event_type, last_event_id, initialized_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (profile_id, event_type, str(last_event_id), now, now),
        )
        return cursor.rowcount == 1

    async def advance_notification_cursor(
        self,
        profile_id: str,
        event_type: str,
        previous_event_id: str,
        last_event_id: str,
    ) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE notification_cursors
            SET last_event_id = ?, updated_at = ?
            WHERE profile_id = ? AND event_type = ? AND last_event_id = ?
            """,
            (
                str(last_event_id),
                time.time(),
                profile_id,
                event_type,
                str(previous_event_id),
            ),
        )
        return cursor.rowcount == 1

    async def notification_backfill(
        self,
        profile_id: str,
        event_type: str,
    ) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            """
            SELECT boundary_event_id, next_offset, started_at, updated_at
            FROM notification_backfills
            WHERE profile_id = ? AND event_type = ?
            """,
            (profile_id, event_type),
        )
        return dict(row) if row is not None else None

    async def advance_notification_cursor_with_backfill(
        self,
        profile_id: str,
        event_type: str,
        previous_event_id: str,
        last_event_id: str,
        *,
        next_offset: int,
    ) -> bool:
        """Advance the live cursor while durably retaining an older scan gap."""

        now = time.time()
        safe_offset = max(0, int(next_offset))
        async with self.db.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE notification_cursors
                SET last_event_id = ?, updated_at = ?
                WHERE profile_id = ? AND event_type = ? AND last_event_id = ?
                """,
                (
                    str(last_event_id),
                    now,
                    profile_id,
                    event_type,
                    str(previous_event_id),
                ),
            )
            if cursor.rowcount != 1:
                return False
            existing_cursor = await connection.execute(
                """
                SELECT boundary_event_id, next_offset
                FROM notification_backfills
                WHERE profile_id = ? AND event_type = ?
                """,
                (profile_id, event_type),
            )
            existing = await existing_cursor.fetchone()
            if existing is None:
                await connection.execute(
                    """
                    INSERT INTO notification_backfills(
                        profile_id, event_type, boundary_event_id,
                        next_offset, started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        event_type,
                        str(previous_event_id),
                        safe_offset,
                        now,
                        now,
                    ),
                )
            else:
                # A newer burst can open another gap in front of an older one.
                # Keep the oldest boundary and rewind the offset so the two gaps
                # are drained as one continuous range instead of dropping the
                # newly created middle section.
                await connection.execute(
                    """
                    UPDATE notification_backfills
                    SET next_offset = MIN(next_offset, ?), updated_at = ?
                    WHERE profile_id = ? AND event_type = ?
                    """,
                    (safe_offset, now, profile_id, event_type),
                )
        return True

    async def advance_notification_backfill(
        self,
        profile_id: str,
        event_type: str,
        boundary_event_id: str,
        *,
        next_offset: int,
    ) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE notification_backfills
            SET next_offset = ?, updated_at = ?
            WHERE profile_id = ? AND event_type = ? AND boundary_event_id = ?
            """,
            (
                max(0, int(next_offset)),
                time.time(),
                profile_id,
                event_type,
                str(boundary_event_id),
            ),
        )
        return cursor.rowcount == 1

    async def clear_notification_backfill(
        self,
        profile_id: str,
        event_type: str,
        boundary_event_id: str,
    ) -> bool:
        cursor = await self.db.execute(
            """
            DELETE FROM notification_backfills
            WHERE profile_id = ? AND event_type = ? AND boundary_event_id = ?
            """,
            (profile_id, event_type, str(boundary_event_id)),
        )
        return cursor.rowcount == 1

    async def mark_event(
        self,
        event_id: int,
        state: EventState,
        *,
        reply_text: str | None = None,
        error: str = "",
        generated_ms: int | None = None,
        should_filter: bool | None = None,
    ) -> None:
        now = time.time()
        completed_at = now if state.value in FINAL_STATES else None
        reply_hash = _content_hash(reply_text) if reply_text else ""
        await self.db.execute(
            """
            UPDATE incoming_events
            SET status = ?,
                reply_text = COALESCE(?, reply_text),
                reply_hash = CASE WHEN ? != '' THEN ? ELSE reply_hash END,
                error = ?,
                generated_ms = COALESCE(?, generated_ms),
                should_filter = COALESCE(?, should_filter),
                completed_at = COALESCE(?, completed_at),
                updated_at = ?
            WHERE id = ?
            """,
            (
                state.value,
                reply_text,
                reply_hash,
                reply_hash,
                redact_text(error)[:2000],
                generated_ms,
                int(should_filter) if should_filter is not None else None,
                completed_at,
                now,
                event_id,
            ),
        )

    async def schedule_retry(
        self,
        event_id: int,
        error: str,
        *,
        max_retries: int,
    ) -> EventState:
        now = time.time()
        async with self.db.transaction() as connection:
            cursor = await connection.execute(
                "SELECT retry_count, status FROM incoming_events WHERE id = ?",
                (event_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("待重试事件不存在")
            current_state = str(row["status"])
            if current_state in {
                EventState.RETRY_WAIT.value,
                EventState.SEND_UNKNOWN.value,
                EventState.SENT.value,
                EventState.DRY_RUN.value,
                EventState.IGNORED.value,
                EventState.DEAD_LETTER.value,
            }:
                return EventState(current_state)
            retry_count = int(row["retry_count"])
            if retry_count >= max(0, max_retries):
                await connection.execute(
                    """
                    UPDATE incoming_events
                    SET status = 'dead_letter', error = ?, completed_at = ?,
                        next_retry_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (redact_text(error)[:2000], now, now, event_id),
                )
                return EventState.DEAD_LETTER
            delay = min(1800, 30 * (2**retry_count))
            await connection.execute(
                """
                UPDATE incoming_events
                SET status = 'retry_wait', error = ?, retry_count = retry_count + 1,
                    next_retry_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (redact_text(error)[:2000], now + delay, now, event_id),
            )
            return EventState.RETRY_WAIT

    async def defer_event(self, event_id: int, error: str, *, delay_seconds: float) -> None:
        now = time.time()
        await self.db.execute(
            """
            UPDATE incoming_events
            SET status = 'retry_wait', error = ?, next_retry_at = ?,
                completed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (
                redact_text(error)[:2000],
                now + max(1.0, delay_seconds),
                now,
                event_id,
            ),
        )

    async def recoverable_events(
        self, limit: int = 50, *, profile_id: str = ""
    ) -> list[dict[str, Any]]:
        now = time.time()
        rows = await self.db.fetchall(
            """
            SELECT * FROM incoming_events
            WHERE (? = '' OR profile_id = ?)
              AND (
                status IN ('claimed', 'context_ready', 'dispatched')
                OR (status = 'retry_wait' AND COALESCE(next_retry_at, 0) <= ?)
              )
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (profile_id, profile_id, now, max(1, min(limit, 500))),
        )
        return [dict(row) for row in rows]

    async def due_retry_events(
        self, limit: int = 50, *, profile_id: str = ""
    ) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """
            SELECT * FROM incoming_events
            WHERE (? = '' OR profile_id = ?)
              AND status = 'retry_wait'
              AND COALESCE(next_retry_at, 0) <= ?
            ORDER BY next_retry_at ASC, updated_at ASC
            LIMIT ?
            """,
            (
                profile_id,
                profile_id,
                time.time(),
                max(1, min(limit, 500)),
            ),
        )
        return [dict(row) for row in rows]

    async def is_self_comment(self, profile_id: str, external_comment_id: str) -> bool:
        row = await self.db.fetchone(
            """
            SELECT 1 FROM self_comment_ids
            WHERE profile_id = ? AND external_comment_id = ?
            """,
            (profile_id, external_comment_id),
        )
        return row is not None

    async def record_outgoing_attempt(
        self,
        profile_id: str,
        event_id: int | None,
        route: RoutingTarget,
        content: str,
        status: str,
        *,
        error: str = "",
        external_comment_id: str = "",
    ) -> int:
        now = time.time()
        cursor = await self.db.execute(
            """
            INSERT INTO outgoing_replies(
                profile_id, incoming_event_id, post_id, root_comment_id,
                parent_comment_id, external_comment_id, content, content_hash,
                status, attempted_at, confirmed_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                event_id,
                route.post_id,
                route.root_comment_id,
                route.parent_comment_id,
                external_comment_id,
                content,
                _content_hash(content),
                status,
                now,
                now if status == EventState.SENT.value else None,
                redact_text(error)[:2000],
            ),
        )
        return int(cursor.lastrowid)

    async def latest_outgoing_for_event(self, event_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            """
            SELECT * FROM outgoing_replies
            WHERE incoming_event_id = ?
            ORDER BY attempted_at DESC, id DESC
            LIMIT 1
            """,
            (event_id,),
        )
        return dict(row) if row is not None else None

    async def confirm_outgoing(self, outgoing_id: int, external_comment_id: str) -> None:
        row = await self.db.fetchone(
            """
            SELECT profile_id, post_id, root_comment_id, content_hash
            FROM outgoing_replies WHERE id = ?
            """,
            (outgoing_id,),
        )
        if row is None:
            raise ValueError("待确认发送记录不存在")
        now = time.time()
        async with self.db.transaction() as connection:
            await connection.execute(
                """
                UPDATE outgoing_replies
                SET external_comment_id = ?, status = 'sent', confirmed_at = ?, error = ''
                WHERE id = ?
                """,
                (external_comment_id, now, outgoing_id),
            )
            if external_comment_id:
                await connection.execute(
                    """
                    INSERT OR IGNORE INTO self_comment_ids(
                        profile_id, external_comment_id, post_id, root_comment_id,
                        content_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["profile_id"],
                        external_comment_id,
                        row["post_id"],
                        row["root_comment_id"],
                        row["content_hash"],
                        now,
                    ),
                )

    async def recent_outgoing_match(
        self,
        profile_id: str,
        route: RoutingTarget,
        content: str,
        *,
        since: float,
    ) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            """
            SELECT * FROM outgoing_replies
            WHERE profile_id = ? AND post_id = ? AND root_comment_id = ?
              AND content_hash = ? AND attempted_at >= ?
            ORDER BY attempted_at DESC LIMIT 1
            """,
            (
                profile_id,
                route.post_id,
                route.root_comment_id,
                _content_hash(content),
                since,
            ),
        )
        return dict(row) if row else None

    async def has_replied_to_post(self, profile_id: str, post_id: str) -> bool:
        row = await self.db.fetchone(
            """
            SELECT 1 FROM outgoing_replies
            WHERE profile_id = ? AND post_id = ? AND status = 'sent'
            LIMIT 1
            """,
            (profile_id, post_id),
        )
        if row is not None:
            return True
        row = await self.db.fetchone(
            """
            SELECT 1 FROM self_comment_ids
            WHERE profile_id = ? AND post_id = ?
            LIMIT 1
            """,
            (profile_id, post_id),
        )
        return row is not None

    async def record_session(self, route: RoutingTarget) -> None:
        await self.db.execute(
            """
            INSERT INTO session_mappings(
                profile_id, session_id, post_id, root_comment_id, last_used_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, session_id) DO UPDATE SET
                post_id = excluded.post_id,
                root_comment_id = excluded.root_comment_id,
                last_used_at = excluded.last_used_at
            """,
            (
                route.profile_id,
                route.session_id,
                route.post_id,
                route.root_comment_id,
                time.time(),
            ),
        )

    async def update_account_state(
        self, profile_id: str, **fields: str | int | float | None
    ) -> None:
        allowed = {
            "status",
            "uid",
            "nickname",
            "login_at",
            "last_login_check_at",
            "last_poll_at",
            "last_success_request_at",
            "last_error",
            "consecutive_401",
            "consecutive_403",
            "consecutive_429",
            "consecutive_poll_failures",
            "circuit_open_until",
        }
        safe_fields = {key: value for key, value in fields.items() if key in allowed}
        if "last_error" in safe_fields and safe_fields["last_error"] is not None:
            safe_fields["last_error"] = redact_text(str(safe_fields["last_error"]))[:2000]
        if not safe_fields:
            return
        keys = list(safe_fields)
        columns = ", ".join(f"{key} = ?" for key in keys)
        values = [safe_fields[key] for key in keys]
        async with self.db.transaction() as connection:
            await connection.execute(
                """
                INSERT OR IGNORE INTO account_state(profile_id, status)
                VALUES (?, 'idle')
                """,
                (profile_id,),
            )
            await connection.execute(
                f"UPDATE account_state SET {columns}, updated_at = CURRENT_TIMESTAMP "
                "WHERE profile_id = ?",
                (*values, profile_id),
            )

    async def account_state(self, profile_id: str) -> dict[str, Any]:
        row = await self.db.fetchone(
            "SELECT * FROM account_state WHERE profile_id = ?", (profile_id,)
        )
        return dict(row) if row else {"profile_id": profile_id, "status": "idle"}

    async def add_runtime_error(
        self,
        category: str,
        message: str,
        *,
        profile_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO runtime_errors(
                profile_id, category, message, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                category,
                redact_text(message)[:2000],
                json.dumps(redact_data(details or {}), ensure_ascii=False),
                time.time(),
            ),
        )

    async def increment_counter(
        self, profile_id: str, *, reply: int = 0, proactive: int = 0, error: int = 0
    ) -> None:
        day = datetime.now(UTC).date().isoformat()
        await self.db.execute(
            """
            INSERT INTO daily_counters(
                profile_id, day, reply_count, proactive_count, error_count
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, day) DO UPDATE SET
                reply_count = reply_count + excluded.reply_count,
                proactive_count = proactive_count + excluded.proactive_count,
                error_count = error_count + excluded.error_count
            """,
            (profile_id, day, reply, proactive, error),
        )

    async def reserve_proactive_request(self, profile_id: str, daily_limit: int) -> bool:
        """Atomically reserve one proactive AI-generation request for today."""
        day = datetime.now(UTC).date().isoformat()
        async with self.db.transaction() as connection:
            async with connection.execute(
                """
                SELECT proactive_count FROM daily_counters
                WHERE profile_id = ? AND day = ?
                """,
                (profile_id, day),
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None and int(row["proactive_count"]) >= daily_limit:
                return False
            await connection.execute(
                """
                INSERT INTO daily_counters(
                    profile_id, day, reply_count, proactive_count, error_count
                ) VALUES (?, ?, 0, 1, 0)
                ON CONFLICT(profile_id, day) DO UPDATE SET
                    proactive_count = proactive_count + 1
                """,
                (profile_id, day),
            )
            return True

    async def today_counters(self, profile_id: str) -> dict[str, int]:
        day = datetime.now(UTC).date().isoformat()
        row = await self.db.fetchone(
            """
            SELECT reply_count, proactive_count, error_count
            FROM daily_counters WHERE profile_id = ? AND day = ?
            """,
            (profile_id, day),
        )
        return (
            {
                "reply_count": int(row["reply_count"]),
                "proactive_count": int(row["proactive_count"]),
                "error_count": int(row["error_count"]),
            }
            if row
            else {"reply_count": 0, "proactive_count": 0, "error_count": 0}
        )

    async def list_events(
        self,
        *,
        status: str = "",
        uid: str = "",
        post_id: str = "",
        keyword: str = "",
        start_time: float | None = None,
        end_time: float | None = None,
        page: int = 1,
        page_size: int = 30,
    ) -> dict[str, Any]:
        where = ["1 = 1"]
        parameters: list[Any] = []
        valid_status = {state.value for state in EventState}
        if status:
            if status not in valid_status:
                raise ValueError("无效事件状态")
            where.append("status = ?")
            parameters.append(status)
        if uid:
            where.append("sender_uid = ?")
            parameters.append(str(uid))
        if post_id:
            where.append("post_id = ?")
            parameters.append(str(post_id))
        if keyword:
            where.append("(content LIKE ? OR reply_text LIKE ?)")
            pattern = f"%{keyword[:100]}%"
            parameters.extend((pattern, pattern))
        if start_time is not None:
            where.append("COALESCE(completed_at, updated_at, discovered_at) >= ?")
            parameters.append(float(start_time))
        if end_time is not None:
            where.append("COALESCE(completed_at, updated_at, discovered_at) <= ?")
            parameters.append(float(end_time))
        safe_size = max(1, min(page_size, 100))
        safe_page = max(1, page)
        predicate = " AND ".join(where)
        count_row = await self.db.fetchone(
            f"SELECT COUNT(*) AS count FROM incoming_events WHERE {predicate}",
            parameters,
        )
        rows = await self.db.fetchall(
            f"""
            SELECT id, profile_id, external_event_id, external_comment_id,
                   notification_id, event_type, status, sender_uid,
                   sender_nickname, post_id, root_comment_id, parent_comment_id,
                   content, reply_text, error, should_filter, discovered_at,
                   updated_at, completed_at,
                   COALESCE(completed_at, updated_at, discovered_at) AS event_time,
                   generated_ms, retry_count, next_retry_at
            FROM incoming_events
            WHERE {predicate}
            ORDER BY COALESCE(completed_at, updated_at, discovered_at) DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, safe_size, (safe_page - 1) * safe_size),
        )
        return {
            "items": [dict(row) for row in rows],
            "total": int(count_row["count"]) if count_row else 0,
            "page": safe_page,
            "page_size": safe_size,
        }

    async def create_feed_candidate(
        self,
        profile_id: str,
        post_id: str,
        title: str,
        author_uid: str,
        text: str,
        reason: str,
        incoming_event_id: int | None = None,
    ) -> int | None:
        cursor = await self.db.execute(
            """
            INSERT OR IGNORE INTO feed_candidates(
                profile_id, post_id, post_title, post_author_uid,
                generated_text, reason, incoming_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                post_id,
                title,
                str(author_uid),
                text,
                reason,
                incoming_event_id,
                time.time(),
            ),
        )
        return int(cursor.lastrowid) if cursor.rowcount == 1 else None

    async def proactive_event_id(self, profile_id: str, post_id: str) -> int | None:
        row = await self.db.fetchone(
            """
            SELECT id FROM incoming_events
            WHERE profile_id = ? AND post_id = ? AND event_type = 'proactive_feed'
            ORDER BY id DESC
            LIMIT 1
            """,
            (profile_id, post_id),
        )
        return int(row["id"]) if row else None

    async def list_feed_candidates(
        self, *, status: str = "pending", limit: int = 100
    ) -> list[dict[str, Any]]:
        allowed = {
            "pending",
            "approved",
            "sending",
            "rejected",
            "sent",
            "expired",
            "failed",
            "send_unknown",
        }
        if status not in allowed:
            raise ValueError("无效候选状态")
        rows = await self.db.fetchall(
            """
            SELECT * FROM feed_candidates
            WHERE status = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (status, max(1, min(limit, 500))),
        )
        return [dict(row) for row in rows]

    async def feed_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM feed_candidates WHERE id = ?", (candidate_id,))
        return dict(row) if row else None

    async def review_feed_candidate(
        self, candidate_id: int, status: str, edited_text: str | None = None
    ) -> bool:
        if status not in {
            "approved",
            "rejected",
            "sent",
            "failed",
            "expired",
            "send_unknown",
        }:
            raise ValueError("无效审核状态")
        cursor = await self.db.execute(
            """
            UPDATE feed_candidates
            SET status = ?, edited_text = COALESCE(?, edited_text), reviewed_at = ?
            WHERE id = ? AND status IN ('pending', 'approved')
            """,
            (status, edited_text, time.time(), candidate_id),
        )
        return cursor.rowcount == 1

    async def reject_feed_candidate(self, candidate_id: int) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE feed_candidates
            SET status = 'rejected', reviewed_at = ?
            WHERE id = ? AND status IN ('pending', 'approved', 'send_unknown')
            """,
            (time.time(), candidate_id),
        )
        return cursor.rowcount == 1

    async def claim_feed_candidate_for_send(
        self,
        candidate_id: int,
        edited_text: str,
    ) -> dict[str, Any] | None:
        async with self.db.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE feed_candidates
                SET status = 'sending', edited_text = ?, reviewed_at = ?
                WHERE id = ? AND status IN ('pending', 'approved')
                """,
                (edited_text, time.time(), candidate_id),
            )
            if cursor.rowcount != 1:
                return None
            result = await connection.execute(
                "SELECT * FROM feed_candidates WHERE id = ?",
                (candidate_id,),
            )
            row = await result.fetchone()
            return dict(row) if row is not None else None

    async def finish_feed_candidate_send(
        self,
        candidate_id: int,
        status: str,
        edited_text: str,
        *,
        sent_comment_id: str = "",
    ) -> bool:
        if status not in {"sent", "failed", "send_unknown"}:
            raise ValueError("无效候选发送终态")
        cursor = await self.db.execute(
            """
            UPDATE feed_candidates
            SET status = ?, edited_text = ?, reviewed_at = ?,
                sent_comment_id = CASE WHEN ? != '' THEN ? ELSE sent_comment_id END
            WHERE id = ? AND status = 'sending'
            """,
            (
                status,
                edited_text,
                time.time(),
                sent_comment_id,
                sent_comment_id,
                candidate_id,
            ),
        )
        return cursor.rowcount == 1

    async def recover_interrupted_feed_sends(self) -> int:
        cursor = await self.db.execute(
            """
            UPDATE feed_candidates
            SET status = 'send_unknown', reviewed_at = ?
            WHERE status = 'sending'
            """,
            (time.time(),),
        )
        return max(0, cursor.rowcount)

    async def cleanup_preview(
        self, retention: dict[str, Any], *, now: float | None = None
    ) -> dict[str, int]:
        current = now if now is not None else time.time()
        rules = self._cleanup_rules(retention, current)
        counts: dict[str, int] = {}
        for name, sql, parameters in rules:
            row = await self.db.fetchone(f"SELECT COUNT(*) AS count FROM ({sql})", parameters)
            counts[name] = int(row["count"]) if row else 0
        return counts

    async def cleanup(
        self,
        retention: dict[str, Any],
        *,
        batch_size: int = 500,
        now: float | None = None,
    ) -> dict[str, int]:
        current = now if now is not None else time.time()
        safe_batch = max(1, min(batch_size, 500))
        removed: dict[str, int] = {}
        for name, select_sql, parameters in self._cleanup_rules(retention, current):
            cursor = await self.db.execute(
                f"DELETE FROM {name} WHERE rowid IN (SELECT rowid FROM ({select_sql}) LIMIT ?)",
                (*parameters, safe_batch),
            )
            removed[name] = max(0, cursor.rowcount)
        await self.db.checkpoint()
        await self.db.incremental_vacuum()
        return removed

    async def enforce_soft_limit(
        self,
        retention: dict[str, Any],
        *,
        batch_size: int = 500,
    ) -> dict[str, int]:
        soft_limit = int(retention["database_soft_limit_mb"]) * 1024 * 1024
        if self.database_size() <= soft_limit:
            return {}
        safe_batch = max(1, min(batch_size, 500))
        operations = (
            (
                "rejected_feed_candidates",
                """
                DELETE FROM feed_candidates
                WHERE id IN (
                    SELECT id FROM feed_candidates
                    WHERE status IN ('rejected', 'expired')
                    ORDER BY created_at ASC LIMIT ?
                )
                """,
            ),
            (
                "dry_run_bodies",
                """
                UPDATE incoming_events SET content = NULL, raw_json = NULL
                WHERE id IN (
                    SELECT id FROM incoming_events
                    WHERE status = 'dry_run' AND (content IS NOT NULL OR raw_json IS NOT NULL)
                    ORDER BY completed_at ASC LIMIT ?
                )
                """,
            ),
            (
                "historical_event_bodies",
                """
                UPDATE incoming_events SET content = NULL, raw_json = NULL
                WHERE id IN (
                    SELECT id FROM incoming_events
                    WHERE status IN ('sent', 'ignored', 'dead_letter')
                      AND (content IS NOT NULL OR raw_json IS NOT NULL)
                    ORDER BY completed_at ASC LIMIT ?
                )
                """,
            ),
            (
                "successful_reply_bodies",
                """
                UPDATE outgoing_replies SET content = NULL
                WHERE id IN (
                    SELECT id FROM outgoing_replies
                    WHERE status = 'sent' AND content IS NOT NULL
                    ORDER BY confirmed_at ASC LIMIT ?
                )
                """,
            ),
        )
        changed: dict[str, int] = {}
        for name, sql in operations:
            cursor = await self.db.execute(sql, (safe_batch,))
            changed[name] = max(0, cursor.rowcount)
        await self.db.checkpoint()
        await self.db.incremental_vacuum()
        return changed

    def _cleanup_rules(
        self, retention: dict[str, Any], now: float
    ) -> list[tuple[str, str, tuple[Any, ...]]]:
        day = 86400
        failure_cutoff = now - int(retention["failed_days"]) * day
        session_cutoff = now - int(retention["session_mapping_days"]) * day
        dedup_cutoff = now - int(retention["dedup_days"]) * day
        error_cutoff = now - 30 * day
        return [
            (
                "processed_event_keys",
                "SELECT rowid FROM processed_event_keys WHERE created_at < ?",
                (dedup_cutoff,),
            ),
            (
                "session_mappings",
                "SELECT rowid FROM session_mappings WHERE last_used_at < ?",
                (session_cutoff,),
            ),
            (
                "runtime_errors",
                "SELECT rowid FROM runtime_errors WHERE created_at < ?",
                (error_cutoff,),
            ),
            (
                "outgoing_replies",
                """
                SELECT rowid FROM outgoing_replies
                WHERE status = 'failed' AND attempted_at < ?
                """,
                (failure_cutoff,),
            ),
            (
                "incoming_events",
                """
                SELECT rowid FROM incoming_events
                WHERE status = 'dead_letter' AND completed_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM outgoing_replies
                      WHERE outgoing_replies.incoming_event_id = incoming_events.id
                  )
                """,
                (failure_cutoff,),
            ),
        ]

    async def prune_event_bodies(
        self, retention: dict[str, Any], *, now: float | None = None
    ) -> int:
        current = now if now is not None else time.time()
        incoming_cutoff = current - int(retention["incoming_body_days"]) * 86400
        dry_run_cutoff = current - int(retention["dry_run_days"]) * 86400
        reply_cutoff = current - int(retention["success_reply_days"]) * 86400
        incoming_cursor = await self.db.execute(
            """
            UPDATE incoming_events
            SET content = NULL, raw_json = NULL
            WHERE completed_at < ? AND status IN ('sent', 'ignored')
              AND (content IS NOT NULL OR raw_json IS NOT NULL)
            """,
            (incoming_cutoff,),
        )
        dry_run_cursor = await self.db.execute(
            """
            UPDATE incoming_events
            SET content = NULL, raw_json = NULL
            WHERE completed_at < ? AND status = 'dry_run'
              AND (content IS NOT NULL OR raw_json IS NOT NULL)
            """,
            (dry_run_cutoff,),
        )
        replies_cursor = await self.db.execute(
            """
            UPDATE outgoing_replies
            SET content = NULL
            WHERE confirmed_at < ? AND status = 'sent' AND content IS NOT NULL
            """,
            (reply_cutoff,),
        )
        return sum(
            max(0, cursor.rowcount) for cursor in (incoming_cursor, dry_run_cursor, replies_cursor)
        )

    async def table_counts(self) -> dict[str, int]:
        tables = (
            "incoming_events",
            "processed_event_keys",
            "outgoing_replies",
            "self_comment_ids",
            "session_mappings",
            "feed_candidates",
            "runtime_errors",
            "daily_counters",
            "notification_cursors",
            "notification_backfills",
        )
        counts: dict[str, int] = {}
        for table in tables:
            row = await self.db.fetchone(f"SELECT COUNT(*) AS count FROM {table}")
            counts[table] = int(row["count"]) if row else 0
        return counts

    def database_size(self) -> int:
        paths = (
            self.db.path,
            Path(f"{self.db.path}-wal"),
            Path(f"{self.db.path}-shm"),
        )
        return sum(path.stat().st_size for path in paths if path.exists())

    async def diagnostic_snapshot(self) -> dict[str, Any]:
        states = await self.db.fetchall(
            """
            SELECT profile_id, status, uid, nickname, login_at, last_login_check_at,
                   last_poll_at, last_success_request_at, last_error,
                   consecutive_401, consecutive_403, consecutive_429,
                   consecutive_poll_failures, circuit_open_until, updated_at
            FROM account_state
            """
        )
        errors = await self.db.fetchall(
            """
            SELECT profile_id, category, message, created_at
            FROM runtime_errors ORDER BY created_at DESC LIMIT 50
            """
        )
        return {
            "schema_version": await self.db.schema_version(),
            "database_size": self.database_size(),
            "counts": await self.table_counts(),
            "account_states": [dict(row) for row in states],
            "recent_errors": [dict(row) for row in errors],
        }


def _content_hash(content: str | None) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()
