from __future__ import annotations

import pytest

from xiaoheihe.models import RoutingTarget


def test_deterministic_post_and_thread_sessions() -> None:
    post = RoutingTarget(profile_id="p1", post_id="123")
    thread = RoutingTarget(
        profile_id="p1",
        post_id="123",
        root_comment_id="456",
        parent_comment_id="789",
    )
    assert post.session_id == "xhh_post_123"
    assert post.group_id == "xhh_post_123"
    assert thread.session_id == "xhh_thread_123_456"
    assert RoutingTarget.from_session_id("p1", thread.session_id).root_comment_id == "456"


def test_invalid_session_never_guesses_target() -> None:
    with pytest.raises(ValueError, match="无法"):
        RoutingTarget.from_session_id("default", "random-session")


async def test_session_mapping_survives_recalculation(repository) -> None:
    route = RoutingTarget(profile_id="default", post_id="123", root_comment_id="456")
    await repository.record_session(route)
    row = await repository.db.fetchone(
        "SELECT * FROM session_mappings WHERE profile_id = ? AND session_id = ?",
        ("default", route.session_id),
    )
    assert row["post_id"] == "123"
    assert RoutingTarget.from_session_id("default", row["session_id"]) == RoutingTarget(
        profile_id="default",
        post_id="123",
        root_comment_id="456",
        parent_comment_id="456",
    )
