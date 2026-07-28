from __future__ import annotations

import asyncio

from xiaoheihe.models import Notification, NotificationType


def make_notification() -> Notification:
    return Notification(
        profile_id="default",
        external_event_id="event-1",
        external_comment_id="comment-1",
        notification_id="notice-1",
        event_type=NotificationType.MENTION,
        sender_uid="200",
        sender_nickname="Alice",
        post_id="300",
        root_comment_id="400",
        parent_comment_id="400",
        content="hello",
        created_at=1800000000,
    )


async def test_unique_event_and_comment_constraints(repository) -> None:
    notification = make_notification()
    first = await repository.claim_event(notification)
    second = await repository.claim_event(notification)
    assert isinstance(first, int)
    assert second is None

    notification.external_event_id = "event-2"
    assert await repository.claim_event(notification) is None


async def test_concurrent_claim_returns_single_winner(repository) -> None:
    results = await asyncio.gather(
        *(repository.claim_event(make_notification()) for _ in range(12))
    )
    assert sum(result is not None for result in results) == 1
