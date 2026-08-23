from __future__ import annotations

import copy

from xiaoheihe import feed_service as feed_module
from xiaoheihe.config_service import DEFAULT_CONFIG
from xiaoheihe.feed_service import FeedService
from xiaoheihe.models import ApiPage


class RealTopicClient:
    def __init__(self, fallback_items: list[dict] | None = None) -> None:
        self.credentials = type("Credentials", (), {"uid": "bot"})()
        self.fallback_items = fallback_items

    async def fetch_feed(self, **kwargs):
        if self.fallback_items is None:
            raise AssertionError("存在可用真实分区时不应回退首页推荐流")
        assert kwargs == {"offset": 0}
        return ApiPage(items=self.fallback_items)


async def unused_delivery(route, text):
    raise AssertionError(f"unexpected reviewed delivery: {route} {text}")


def topic_post(post_id: str, created_at: float, topic_id: str) -> dict:
    return {
        "post_id": post_id,
        "title": f"帖子 {post_id}",
        "content": "这是可参与的正常讨论内容。",
        "created_at": created_at,
        "author": {"uid": f"author-{post_id}", "nickname": "作者"},
        "source_topic_id": topic_id,
        "popularity_score": 1,
    }


async def test_real_topic_multiselect_merges_sorts_and_deduplicates(
    repository,
    monkeypatch,
) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"].update(
        {
            "enabled": True,
            "topic_ids": ["100", "200"],
            "fallback_sources": ["mobile_game"],
            "max_per_run": 3,
        }
    )

    async def fake_topic_feed(client, topic_id, **kwargs):
        if topic_id == "100":
            return ApiPage(
                items=[
                    topic_post("shared", 20, "100"),
                    topic_post("older", 10, "100"),
                ]
            )
        return ApiPage(
            items=[
                topic_post("newer", 30, "200"),
                topic_post("shared", 20, "200"),
            ]
        )

    monkeypatch.setattr(feed_module, "fetch_topic_feed", fake_topic_feed)
    dispatched = []

    async def dispatch(notification, metadata):
        dispatched.append((notification, metadata))

    service = FeedService(
        "default",
        config,
        RealTopicClient(),
        repository,
        dispatch,
        unused_delivery,
    )

    assert await service.run_once() == 3
    assert [item[0].post_id for item in dispatched] == ["newer", "shared", "older"]
    assert len({item[0].post_id for item in dispatched}) == 3
    assert all("真实分区" in item[1]["candidate_reason"] for item in dispatched)


async def test_one_failed_real_topic_does_not_abort_or_fallback(repository, monkeypatch) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"].update(
        {
            "enabled": True,
            "topic_ids": ["100", "200"],
            "max_per_run": 2,
        }
    )

    async def fake_topic_feed(client, topic_id, **kwargs):
        if topic_id == "100":
            raise RuntimeError("temporary topic failure")
        return ApiPage(items=[topic_post("healthy", 30, "200")])

    monkeypatch.setattr(feed_module, "fetch_topic_feed", fake_topic_feed)
    dispatched = []

    async def dispatch(notification, metadata):
        dispatched.append(notification.post_id)

    service = FeedService(
        "default",
        config,
        RealTopicClient(),
        repository,
        dispatch,
        unused_delivery,
    )

    assert await service.run_once() == 1
    assert dispatched == ["healthy"]


async def test_all_failed_real_topics_fallback_to_recommendation_sources(
    repository,
    monkeypatch,
) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"].update(
        {
            "enabled": True,
            "topic_ids": ["100", "200"],
            "fallback_sources": ["pc_game", "digital_tech"],
            "max_per_run": 2,
        }
    )

    async def fake_topic_feed(client, topic_id, **kwargs):
        raise RuntimeError(f"failed {topic_id}")

    fallback_items = [
        {
            "post_id": "fallback-pc",
            "title": "PC 硬件讨论",
            "content": "聊聊新的显卡和平台选择。",
            "author": {"uid": "fallback-author"},
            "section_names": ["Steam", "硬件"],
        },
        {
            "post_id": "fallback-mobile",
            "title": "手游讨论",
            "content": "聊聊手游的新版本。",
            "author": {"uid": "mobile-author"},
            "section_names": ["手机游戏"],
        },
    ]
    monkeypatch.setattr(feed_module, "fetch_topic_feed", fake_topic_feed)
    dispatched = []

    async def dispatch(notification, metadata):
        dispatched.append((notification.post_id, metadata))

    service = FeedService(
        "default",
        config,
        RealTopicClient(fallback_items),
        repository,
        dispatch,
        unused_delivery,
    )

    assert await service.run_once() == 1
    assert dispatched[0][0] == "fallback-pc"
    assert "回退推荐流" in dispatched[0][1]["candidate_reason"]
    assert dispatched[0][1]["feed_origin"] == "recommendation_fallback"
