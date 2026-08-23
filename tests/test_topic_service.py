from __future__ import annotations

from types import SimpleNamespace

import pytest

from xiaoheihe.endpoints import EndpointName
from xiaoheihe.topic_service import (
    fetch_topic_catalog,
    fetch_topic_feed,
    normalize_topic_id,
    normalize_topic_ids,
)


class FakeTopicClient:
    def __init__(self, payloads: dict[EndpointName, dict]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[EndpointName, dict]] = []

    async def _request(self, endpoint, *, params=None, **kwargs):
        self.calls.append((endpoint, dict(params or {})))
        return SimpleNamespace(payload=self.payloads[endpoint])


async def test_real_topic_catalog_flattens_groups_without_writes() -> None:
    client = FakeTopicClient(
        {
            EndpointName.TOPIC_INDEX: {
                "result": {
                    "topics_list": [
                        {
                            "name": "游戏",
                            "topics": [
                                {"topic_id": 100, "name": "PC 游戏"},
                                {"topic_id": "101", "name": "主机游戏"},
                            ],
                        },
                        {"topic_id": "200", "name": "盒友杂谈"},
                    ]
                }
            }
        }
    )

    topics = await fetch_topic_catalog(client)

    assert topics == [
        {"id": "100", "name": "PC 游戏", "group": "游戏"},
        {"id": "101", "name": "主机游戏", "group": "游戏"},
        {"id": "200", "name": "盒友杂谈", "group": ""},
    ]
    assert client.calls == [(EndpointName.TOPIC_INDEX, {"type": "list"})]


async def test_real_topic_feed_uses_topic_id_and_preserves_source() -> None:
    client = FakeTopicClient(
        {
            EndpointName.TOPIC_FEED: {
                "result": {
                    "links": [
                        {
                            "linkid": "987",
                            "title": "真实分区帖子",
                            "description": "正文",
                            "create_at": 1_700_000_000,
                            "user": {"userid": "author", "username": "A"},
                        }
                    ]
                }
            }
        }
    )

    page = await fetch_topic_feed(client, "123", offset=0, limit=3)

    assert page.items[0]["post_id"] == "987"
    assert page.items[0]["source_topic_id"] == "123"
    endpoint, params = client.calls[0]
    assert endpoint is EndpointName.TOPIC_FEED
    assert params == {
        "topic_id": "123",
        "offset": 0,
        "limit": 3,
        "lastval": "",
        "dw": 720,
    }


def test_topic_id_normalization_deduplicates_and_caps() -> None:
    values = ["1", "1", "bad", *[str(index) for index in range(2, 30)]]
    normalized = normalize_topic_ids(values)
    assert len(normalized) == 20
    assert normalized[:3] == ["1", "2", "3"]
    assert len(set(normalized)) == len(normalized)


@pytest.mark.parametrize("value", ["", "abc", "12-3", "1" * 33])
def test_topic_id_validation_rejects_non_numeric_ids(value: str) -> None:
    with pytest.raises(ValueError, match="topic_id"):
        normalize_topic_id(value)
