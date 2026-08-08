from __future__ import annotations

import time

from xiaoheihe.config_service import DEFAULT_CONFIG
from xiaoheihe.models import EventState, Notification, NotificationType


def old_notification(identifier: str, created_at: float) -> Notification:
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
        content="body",
        created_at=created_at,
        raw={"body": "raw"},
    )


async def test_cleanup_preserves_dedup_until_policy_and_prunes_body(repository) -> None:
    now = time.time()
    event_id = await repository.claim_event(old_notification("old", now - 40 * 86400))
    await repository.mark_event(event_id, EventState.SENT, reply_text="reply")
    await repository.db.execute(
        "UPDATE incoming_events SET completed_at = ?, updated_at = ? WHERE id = ?",
        (now - 40 * 86400, now - 40 * 86400, event_id),
    )
    retention = DEFAULT_CONFIG["retention"]
    assert await repository.prune_event_bodies(retention, now=now) == 1
    row = await repository.db.fetchone(
        "SELECT content, raw_json FROM incoming_events WHERE id = ?", (event_id,)
    )
    assert row["content"] is None
    dedup = await repository.db.fetchone(
        "SELECT 1 FROM processed_event_keys WHERE event_key = ?", ("event-old",)
    )
    assert dedup is not None


async def test_cleanup_removes_old_sessions_and_runtime_errors(repository) -> None:
    now = time.time()
    await repository.db.execute(
        """
        INSERT INTO session_mappings(
            profile_id, session_id, post_id, root_comment_id, last_used_at
        ) VALUES ('default', 'old-session', 'p', 'r', ?)
        """,
        (now - 181 * 86400,),
    )
    await repository.add_runtime_error("test", "old")
    await repository.db.execute("UPDATE runtime_errors SET created_at = ?", (now - 31 * 86400,))
    preview = await repository.cleanup_preview(DEFAULT_CONFIG["retention"], now=now)
    assert preview["session_mappings"] == 1
    assert preview["runtime_errors"] == 1
    removed = await repository.cleanup(DEFAULT_CONFIG["retention"], now=now)
    assert removed["session_mappings"] == 1
    assert removed["runtime_errors"] == 1


async def test_cleanup_deletes_failed_reply_before_linked_dead_letter(repository) -> None:
    now = time.time()
    notification = old_notification("linked", now - 40 * 86400)
    event_id = await repository.claim_event(notification)
    await repository.mark_event(event_id, EventState.DEAD_LETTER, error="failed")
    await repository.db.execute(
        "UPDATE incoming_events SET completed_at = ?, updated_at = ? WHERE id = ?",
        (now - 40 * 86400, now - 40 * 86400, event_id),
    )
    await repository.record_outgoing_attempt(
        "default",
        event_id,
        notification.route,
        "failed reply",
        status="failed",
    )
    await repository.db.execute(
        "UPDATE outgoing_replies SET attempted_at = ? WHERE incoming_event_id = ?",
        (now - 40 * 86400, event_id),
    )

    removed = await repository.cleanup(DEFAULT_CONFIG["retention"], now=now)

    assert removed["outgoing_replies"] == 1
    assert removed["incoming_events"] == 1


async def test_soft_limit_prunes_only_low_priority_content(repository) -> None:
    now = time.time()
    dry_id = await repository.claim_event(old_notification("dry", now))
    pending_id = await repository.claim_event(old_notification("pending", now))
    await repository.mark_event(dry_id, EventState.DRY_RUN, reply_text="generated")
    retention = {**DEFAULT_CONFIG["retention"], "database_soft_limit_mb": 0}
    changed = await repository.enforce_soft_limit(retention)
    assert changed["dry_run_bodies"] == 1
    dry = await repository.db.fetchone(
        "SELECT content, raw_json FROM incoming_events WHERE id = ?", (dry_id,)
    )
    pending = await repository.db.fetchone(
        "SELECT content, raw_json, status FROM incoming_events WHERE id = ?", (pending_id,)
    )
    assert dry["content"] is None and dry["raw_json"] is None
    assert pending["content"] == "body"
    assert pending["raw_json"]
    assert pending["status"] == EventState.CLAIMED.value


async def test_cleanup_bounds_completed_metadata_by_dedup_retention(repository) -> None:
    now = time.time()
    old = now - 366 * 86400
    notification = old_notification("metadata", old)
    event_id = await repository.claim_event(notification)
    await repository.mark_event(event_id, EventState.SENT, reply_text="reply")
    outgoing_id = await repository.record_outgoing_attempt(
        "default",
        event_id,
        notification.route,
        "reply",
        status="sending",
    )
    await repository.confirm_outgoing(outgoing_id, "self-old")
    await repository.db.execute(
        "UPDATE incoming_events SET completed_at = ?, updated_at = ? WHERE id = ?",
        (old, old, event_id),
    )
    await repository.db.execute(
        "UPDATE outgoing_replies SET attempted_at = ?, confirmed_at = ? WHERE id = ?",
        (old, old, outgoing_id),
    )
    await repository.db.execute(
        "UPDATE self_comment_ids SET created_at = ? WHERE external_comment_id = 'self-old'",
        (old,),
    )
    await repository.db.execute(
        """
        INSERT INTO daily_counters(profile_id, day, reply_count, proactive_count, error_count)
        VALUES ('default', '2025-01-01', 1, 0, 0)
        """
    )
    await repository.db.execute(
        """
        INSERT INTO feed_candidates(
            profile_id, post_id, generated_text, status, created_at, reviewed_at
        ) VALUES ('default', 'old-feed', 'old', 'rejected', ?, ?)
        """,
        (old, old),
    )

    removed = await repository.cleanup(DEFAULT_CONFIG["retention"], now=now)

    assert removed["outgoing_replies"] >= 1
    assert removed["incoming_events"] >= 1
    assert removed["self_comment_ids"] >= 1
    assert removed["daily_counters"] >= 1
    assert removed["feed_candidates"] >= 1
    assert (
        await repository.db.fetchone(
            "SELECT 1 FROM self_comment_ids WHERE external_comment_id = 'self-old'"
        )
        is None
    )


async def test_cleanup_drains_more_than_one_500_row_batch(repository) -> None:
    now = time.time()
    old = now - 181 * 86400
    async with repository.db.transaction() as connection:
        await connection.executemany(
            """
            INSERT INTO session_mappings(
                profile_id, session_id, post_id, root_comment_id, last_used_at
            ) VALUES ('default', ?, 'p', 'r', ?)
            """,
            [(f"old-session-{index}", old) for index in range(510)],
        )

    removed = await repository.cleanup(DEFAULT_CONFIG["retention"], now=now)

    assert removed["session_mappings"] == 510
