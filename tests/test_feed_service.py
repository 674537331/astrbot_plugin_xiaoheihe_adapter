from __future__ import annotations

import asyncio
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
    assert dispatched[0][0].created_at >= time.time() - 5
    assert (await repository.today_counters("default"))["proactive_count"] == 2


async def test_feed_daily_limit_blocks_fetch_before_reading(repository) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["enabled"] = True
    config["proactive_feed"]["max_per_day"] = 1
    await repository.increment_counter("default", proactive=1)

    class NoFetchClient(FakeFeedClient):
        async def fetch_feed(self, **kwargs):
            raise AssertionError("daily browsing quota must block feed retrieval")

    service = FeedService(
        "default",
        config,
        NoFetchClient(),
        repository,
        unused_delivery,
        unused_delivery,
    )
    assert await service.run_once() == 0


async def test_feed_counts_read_items_and_limits_fetch_size(repository) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["enabled"] = True
    config["proactive_feed"]["max_per_day"] = 3
    config["proactive_feed"]["max_per_run"] = 5
    client = FakeFeedClient()
    captured = {}

    async def fetch_feed(**kwargs):
        captured.update(kwargs)
        return ApiPage(items=FakeFeedClient().sent)

    async def dispatch(*args):
        return None

    client.fetch_feed = fetch_feed
    service = FeedService("default", config, client, repository, dispatch, unused_delivery)
    assert await service.run_once() == 0
    assert captured == {"offset": 0}
    assert (await repository.today_counters("default"))["proactive_count"] == 0


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
    stored = await repository.feed_candidate(candidate_id)
    assert stored["status"] == "sent"
    assert stored["sent_comment_id"] == "comment-sent"
    assert (await repository.today_counters("default"))["proactive_count"] == 0


async def test_concurrent_candidate_approval_sends_once(repository) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["dry_run"] = False
    client = FakeFeedClient()
    release = asyncio.Event()
    started = asyncio.Event()

    async def dispatch(*args):
        return None

    async def deliver(route, text):
        started.set()
        await release.wait()
        result = await client.send_comment(route, text)
        return {"external_comment_id": result.external_comment_id}

    candidate_id = await repository.create_feed_candidate(
        "default",
        "post-concurrent",
        "Title",
        "author",
        "generated",
        "reason",
    )
    shared_lock = asyncio.Lock()
    first = FeedService(
        "default",
        config,
        client,
        repository,
        dispatch,
        deliver,
        approval_lock=shared_lock,
    )
    second = FeedService(
        "default",
        config,
        client,
        repository,
        dispatch,
        deliver,
        approval_lock=shared_lock,
    )
    first_task = asyncio.create_task(first.approve(candidate_id, "reviewed"))
    await started.wait()
    second_task = asyncio.create_task(second.approve(candidate_id, "reviewed"))
    release.set()

    assert await first_task == "comment-sent"
    with pytest.raises(ValueError, match="已处理"):
        await second_task
    assert len(client.sent) == 1


async def test_interrupted_candidate_send_recovers_as_unknown(repository) -> None:
    candidate_id = await repository.create_feed_candidate(
        "default",
        "post-interrupted",
        "Title",
        "author",
        "generated",
        "reason",
    )
    claimed = await repository.claim_feed_candidate_for_send(candidate_id, "reviewed")
    assert claimed["status"] == "sending"

    assert await repository.recover_interrupted_feed_sends() == 1
    assert (await repository.feed_candidate(candidate_id))["status"] == "send_unknown"
    config = copy.deepcopy(DEFAULT_CONFIG)

    async def dispatch(*args):
        return None

    service = FeedService(
        "default",
        config,
        FakeFeedClient(),
        repository,
        dispatch,
        unused_delivery,
    )
    assert await service.reject(candidate_id) is True
    assert (await repository.feed_candidate(candidate_id))["status"] == "rejected"


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


async def test_feed_source_filters_recommendation_topics_locally(repository) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"].update({"enabled": True, "source": "pc_game"})
    client = FakeFeedClient()

    async def fetch_feed(**kwargs):
        assert kwargs == {"offset": 0}
        return ApiPage(
            items=[
                {
                    "post_id": "mobile-post",
                    "title": "手游讨论",
                    "content": "聊聊新版本的玩法。",
                    "author": {"uid": "mobile-author"},
                    "section_names": ["手机游戏"],
                },
                {
                    "post_id": "pc-post",
                    "title": "PC 游戏讨论",
                    "content": "聊聊这套机制设计。",
                    "author": {"uid": "pc-author", "nickname": "PCAuthor"},
                    "section_names": ["Steam", "PC游戏"],
                    "image_urls": ["https://cdn.example.com/post.jpg"],
                },
            ]
        )

    client.fetch_feed = fetch_feed
    dispatched = []

    async def dispatch(notification, metadata):
        dispatched.append((notification, metadata))

    service = FeedService("default", config, client, repository, dispatch, unused_delivery)
    assert await service.run_once() == 1
    notification, metadata = dispatched[0]
    assert notification.post_id == "pc-post"
    assert notification.image_urls == ["https://cdn.example.com/post.jpg"]
    assert metadata["post_author_uid"] == "pc-author"
