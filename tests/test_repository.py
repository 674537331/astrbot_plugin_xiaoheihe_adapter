from __future__ import annotations

import time

from xiaoheihe.models import EventState, Notification, NotificationType, RoutingTarget


def make_notification(identifier="one", uid="user", post="post") -> Notification:
    return Notification(
        profile_id="default",
        external_event_id=f"event-{identifier}",
        external_comment_id=f"comment-{identifier}",
        notification_id=f"notice-{identifier}",
        event_type=NotificationType.MENTION,
        sender_uid=uid,
        sender_nickname="User",
        post_id=post,
        root_comment_id="root",
        parent_comment_id="root",
        content=f"content {identifier}",
        created_at=time.time(),
    )


async def test_outgoing_confirmation_self_loop_and_recent_match(repository) -> None:
    event_id = await repository.claim_event(make_notification())
    route = RoutingTarget("default", "post", "root", "root")
    outgoing_id = await repository.record_outgoing_attempt(
        "default", event_id, route, "reply", "sending"
    )
    match = await repository.recent_outgoing_match(
        "default", route, "reply", since=time.time() - 10
    )
    assert match["id"] == outgoing_id
    await repository.confirm_outgoing(outgoing_id, "self-comment")
    assert await repository.is_self_comment("default", "self-comment")
    row = await repository.db.fetchone(
        "SELECT status, external_comment_id FROM outgoing_replies WHERE id = ?",
        (outgoing_id,),
    )
    assert row["status"] == "sent"
    assert row["external_comment_id"] == "self-comment"


async def test_event_filters_counters_and_recovery(repository) -> None:
    first_id = await repository.claim_event(make_notification("first", "100", "p1"))
    second_id = await repository.claim_event(make_notification("second", "200", "p2"))
    await repository.mark_event(first_id, EventState.SENT, reply_text="answer")
    await repository.mark_event(second_id, EventState.RETRY_WAIT, error="retry")
    filtered = await repository.list_events(
        status="sent", uid="100", post_id="p1", keyword="answer"
    )
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == first_id
    recoverable = await repository.recoverable_events()
    assert [item["id"] for item in recoverable] == [second_id]
    await repository.increment_counter("default", reply=2, proactive=1, error=3)
    assert await repository.today_counters("default") == {
        "reply_count": 2,
        "proactive_count": 1,
        "error_count": 3,
    }


async def test_retry_schedule_reclaims_atomically_and_dead_letters(repository) -> None:
    notification = make_notification("scheduled")
    event_id = await repository.claim_event(notification)
    assert (
        await repository.schedule_retry(event_id, "first", max_retries=1) is EventState.RETRY_WAIT
    )
    assert await repository.claim_retry_event(notification) is None
    await repository.db.execute(
        "UPDATE incoming_events SET next_retry_at = 0 WHERE id = ?", (event_id,)
    )
    assert await repository.claim_retry_event(notification) == event_id
    assert (
        await repository.schedule_retry(event_id, "second", max_retries=1) is EventState.DEAD_LETTER
    )
    row = await repository.db.fetchone(
        "SELECT status, retry_count, next_retry_at FROM incoming_events WHERE id = ?",
        (event_id,),
    )
    assert row["status"] == EventState.DEAD_LETTER.value
    assert row["retry_count"] == 1
    assert row["next_retry_at"] is None


async def test_account_error_feed_and_diagnostics(repository) -> None:
    await repository.update_account_state("default", status="success", uid="1", nickname="Bot")
    assert (await repository.account_state("default"))["uid"] == "1"
    await repository.add_runtime_error(
        "response_shape", "changed", profile_id="default", details={"token": "secret"}
    )
    candidate_id = await repository.create_feed_candidate(
        "default", "post", "Title", "author", "generated", "reason"
    )
    assert (
        await repository.create_feed_candidate(
            "default", "post", "Title", "author", "generated", "reason"
        )
        is None
    )
    assert (await repository.list_feed_candidates())[0]["id"] == candidate_id
    assert await repository.review_feed_candidate(candidate_id, "approved", "edited")
    assert (await repository.feed_candidate(candidate_id))["edited_text"] == "edited"
    snapshot = await repository.diagnostic_snapshot()
    assert snapshot["schema_version"] == 3
    assert snapshot["counts"]["feed_candidates"] == 1
    assert snapshot["account_states"][0]["nickname"] == "Bot"
    assert snapshot["recent_errors"][0]["category"] == "response_shape"


async def test_missing_rows_and_invalid_filters(repository) -> None:
    assert await repository.feed_candidate(999) is None
    assert (
        await repository.recent_outgoing_match(
            "default",
            RoutingTarget("default", "none"),
            "none",
            since=time.time() - 1,
        )
        is None
    )
    assert await repository.today_counters("missing") == {
        "reply_count": 0,
        "proactive_count": 0,
        "error_count": 0,
    }
    import pytest

    with pytest.raises(ValueError, match="状态"):
        await repository.list_events(status="invalid")
    with pytest.raises(ValueError, match="审核"):
        await repository.review_feed_candidate(1, "invalid")
