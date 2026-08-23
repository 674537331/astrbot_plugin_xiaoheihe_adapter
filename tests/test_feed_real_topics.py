from __future__ import annotations

import copy

from xiaoheihe import feed_service as feed_module
from xiaoheihe.config_service import DEFAULT_CONFIG
from xiaoheihe.feed_service import FeedService
from xiaoheihe.models import ApiPage


class RealTopicClient:
    def __init__(self) -> None:
        self.credentials = type("Credentials", (), {"uid": "bot"})()

    async def fetch_feed(self, **kwargs):
        raise AssertionError("真实分区已选择时不应回退首页推荐流")


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


async def test_real_topic_multiselect_merges_sorts_and_deduplicates(repository, monkeypatch) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"].update(
        {
            "enabled": True,
            "topic_ids": ["100", "200"],
            "source": "mobile_game",
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


async def test_one_failed_real_topic_does_not_abort_other_topics(repository, monkeypatch) -> None:
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


async def test_all_failed_real_topics_surface_error(repository, monkeypatch) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"].update({"enabled": True, "topic_ids": ["100", "200"]})

    async def fake_topic_feed(client, topic_id, **kwargs):
        raise RuntimeError(f"failed {topic_id}")

    monkeypatch.setattr(feed_module, "fetch_topic_feed", fake_topic_feed)
    service = FeedService(
        "default",
        config,
        RealTopicClient(),
        repository,
        unused_delivery,
        unused_delivery,
    )

    try:
        await service.run_once()
    except RuntimeError as exc:
        assert "failed" in str(exc)
    else:
        raise AssertionError("all failed topic feeds must not silently report success")
