from __future__ import annotations

import copy

import pytest

from xiaoheihe.config_service import DEFAULT_CONFIG, ConfigService, ConfigValidationError


def test_legacy_single_source_migrates_to_fallback_sources() -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"].pop("fallback_sources")
    config["proactive_feed"]["source"] = "hardware"

    snapshot = ConfigService(config).snapshot()

    assert snapshot["proactive_feed"]["fallback_sources"] == ["digital_tech"]
    assert "source" not in snapshot["proactive_feed"]


def test_fallback_sources_accept_multiple_categories() -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["fallback_sources"] = ["pc_game", "digital_tech", "anime"]

    snapshot = ConfigService(config).snapshot()

    assert snapshot["proactive_feed"]["fallback_sources"] == [
        "pc_game",
        "digital_tech",
        "anime",
    ]


@pytest.mark.parametrize(
    "sources, message",
    [
        ([], "至少需要一个"),
        (["all", "pc_game"], "不能同时选择"),
        (["pc_game", "pc_game"], "重复分类"),
        (["not-a-source"], "不支持"),
    ],
)
def test_fallback_sources_reject_invalid_selection(sources: list[str], message: str) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["fallback_sources"] = sources

    with pytest.raises(ConfigValidationError, match=message):
        ConfigService(config)


def test_generic_schema_hides_legacy_source_selector() -> None:
    schema = ConfigService(copy.deepcopy(DEFAULT_CONFIG)).ui_schema()
    assert "source" not in schema["proactive_feed"]["items"]
