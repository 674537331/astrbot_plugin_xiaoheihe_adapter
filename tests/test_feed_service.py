from __future__ import annotations

import copy
import time

import pytest

from xiaoheihe.api_client import SendUncertainError
from xiaoheihe.config_service import DEFAULT_CONFIG
from xiaoheihe.feed_service import FeedService
from xiaoheihe.models import ApiPage, RoutingTarget, SendResult


class FakeFeedClient:
    def __init__(self) -> None:
        self.sent = []
        self.credentials = type("Credentials", (), {"uid": "bot"})()

    async def fetch_feed(self, **kwargs):
        return ApiPage(
            items=[
                {
                    "id": "post-1",
                    "title": "正常讨论",
                    "content": "这个游戏机制应该如何理解？",
                    "created_at": time.time(),
                    "author": {"uid": "author-1", "nickname": "A"},
                },
                {
                    "id": "post-2",
                    "title": "抽奖广告",
                    "content": "来参加抽奖",
                    "created_at": time.time(),
                    "author": {"uid": "author-2"},
                },
            ]
        )

    async def send_comment(self, route, text):
        self.sent.append((route, text))
        return SendResult("comment-sent", True)


async def unused_delivery(route, text):
    raise AssertionError(f"reviewed delivery should not run: {route.session_id} {text}")


async def test_feed_run_filters_and_dispatches(repository) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["enabled"] = True
    dispatched = []

    async def dispatch(notification, metadata):
        dispatched.append((notification, metadata))

    service = FeedService(
        "default", config, FakeFeedClient(), repository, dispatch, unused_delivery
    )
    assert await service.run_once() == 1
    assert dispatched[0][0].raw["event_type"] == "proactive_feed"
    assert dispatched[0][0].post_id == "post-1"


async def test_candidate_review_dry_run_and_reject(repository) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)

    async def dispatch(notification, metadata):
        raise AssertionError("approve should not dispatch a new model event")

    candidate_id = await repository.create_feed_candidate(
        "default", "post-1", "Title", "author", "generated", "reason"
    )
    service = FeedService(
        "default", config, FakeFeedClient(), repository, dispatch, unused_delivery
    )
    assert await service.approve(candidate_id, "edited") == "dry_run"
    candidate = await repository.feed_candidate(candidate_id)
    assert candidate["edited_text"] == "edited"

    second_id = await repository.create_feed_candidate(
        "default", "post-2", "Title", "author", "generated", "reason"
    )
    assert await service.reject(second_id) is True
    assert (await repository.feed_candidate(second_id))["status"] == "rejected"


async def test_approved_real_send_uses_edited_text(repository) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["dry_run"] = False
    client = FakeFeedClient()

    async def dispatch(notification, metadata):
        raise AssertionError("not used")

    candidate_id = await repository.create_feed_candidate(
        "default", "post-3", "Title", "author", "generated", "reason"
    )

    async def deliver(route, text):
        result = await client.send_comment(route, text)
        return {"external_comment_id": result.external_comment_id}

    service = FeedService("default", config, client, repository, dispatch, deliver)
    assert await service.approve(candidate_id, "reviewed") == "comment-sent"
    assert client.sent[0][1] == "reviewed"
    assert (await repository.feed_candidate(candidate_id))["status"] == "sent"


async def test_approved_uncertain_send_is_not_approvable_twice(repository) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["dry_run"] = False
    client = FakeFeedClient()

    async def uncertain_send(route, text):
        raise SendUncertainError("unknown", category="send_unknown")

    candidate_id = await repository.create_feed_candidate(
        "default", "post-uncertain", "Title", "author", "generated", "reason"
    )

    async def dispatch(*args):
        return None

    service = FeedService(
        "default",
        config,
        client,
        repository,
        dispatch,
        uncertain_send,
    )
    with pytest.raises(SendUncertainError):
        await service.approve(candidate_id)
    assert (await repository.feed_candidate(candidate_id))["status"] == "send_unknown"
    with pytest.raises(ValueError, match="已处理"):
        await service.approve(candidate_id)


async def test_feed_skips_self_blacklisted_and_already_replied(repository) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["enabled"] = True
    config["proactive_feed"]["max_per_run"] = 5
    config["permissions"]["author_blacklist"] = ["blocked"]
    client = FakeFeedClient()

    async def fetch_feed(**kwargs):
        return ApiPage(
            items=[
                {
                    "id": "self-post",
                    "title": "普通讨论",
                    "content": "正文内容",
                    "author": {"uid": "bot"},
                },
                {
                    "id": "blocked-post",
                    "title": "普通讨论",
                    "content": "正文内容",
                    "author": {"uid": "blocked"},
                },
                {
                    "id": "replied-post",
                    "title": "普通讨论",
                    "content": "正文内容",
                    "author": {"uid": "author"},
                },
            ]
        )

    client.fetch_feed = fetch_feed
    outgoing_id = await repository.record_outgoing_attempt(
        "default",
        None,
        RoutingTarget("default", "replied-post"),
        "sent before",
        "sent",
    )
    assert outgoing_id
    dispatched = []

    async def dispatch(*args):
        dispatched.append(args)

    service = FeedService("default", config, client, repository, dispatch, unused_delivery)
    assert await service.run_once() == 0
    assert not dispatched
