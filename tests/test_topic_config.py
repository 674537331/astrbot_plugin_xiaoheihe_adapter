from __future__ import annotations

import copy

import pytest

from xiaoheihe.config_service import DEFAULT_CONFIG, ConfigService, ConfigValidationError


def config_with_topics(topic_ids: list[str]) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["topic_ids"] = topic_ids
    return config


def test_topic_ids_accept_multiple_real_topics() -> None:
    service = ConfigService(config_with_topics(["100", "200", "300"]))
    assert service.snapshot()["proactive_feed"]["topic_ids"] == ["100", "200", "300"]


@pytest.mark.parametrize(
    "topic_ids, message",
    [
        (["100", "100"], "不能包含重复分区"),
        (["100", "bad"], "仅允许 1-32 位数字"),
        ([str(index) for index in range(21)], "最多选择 20 个"),
    ],
)
def test_topic_ids_reject_invalid_selection(topic_ids: list[str], message: str) -> None:
    with pytest.raises(ConfigValidationError, match=message):
        ConfigService(config_with_topics(topic_ids))
