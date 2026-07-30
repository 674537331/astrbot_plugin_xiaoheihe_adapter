from __future__ import annotations

import copy

import pytest

from xiaoheihe.config_service import (
    DEFAULT_CONFIG,
    ConfigService,
    ConfigValidationError,
)


def test_config_defaults_and_profile(fake_config) -> None:
    fake_config.pop("network")
    service = ConfigService(fake_config)
    assert service.profile("default")["dry_run"] is True
    assert service.snapshot()["network"]["max_pending_events"] == 50


def test_config_migrates_legacy_follow_source_to_recommendation_defaults() -> None:
    legacy = copy.deepcopy(DEFAULT_CONFIG)
    legacy["proactive_feed"].pop("section")
    legacy["proactive_feed"].pop("selection_strategy")
    legacy["proactive_feed"]["source"] = "follow"

    snapshot = ConfigService(legacy).snapshot()

    assert snapshot["proactive_feed"]["section"] == "All（全部）"
    assert snapshot["proactive_feed"]["selection_strategy"] == "推荐顺序"
    assert "source" not in snapshot["proactive_feed"]


@pytest.mark.asyncio
async def test_config_save_uses_same_object_and_callback(fake_config) -> None:
    service = ConfigService(fake_config)
    changed_seen = []

    async def callback(changed):
        changed_seen.append(changed)

    service.add_restart_callback(callback)
    candidate = service.snapshot()
    candidate["reply"]["max_reply_chars"] = 450
    changed = await service.save(candidate)
    assert fake_config["reply"]["max_reply_chars"] == 450
    assert fake_config.save_count == 1
    assert changed == {"reply"}
    assert changed_seen == [{"reply"}]


@pytest.mark.asyncio
async def test_config_rejects_unsafe_proactive_send(fake_config) -> None:
    service = ConfigService(fake_config)
    candidate = copy.deepcopy(DEFAULT_CONFIG)
    candidate["proactive_feed"].update(
        {"enabled": True, "dry_run": False, "review_required": False}
    )
    with pytest.raises(ConfigValidationError, match="review_required"):
        await service.save(candidate)


def test_config_rejects_duplicate_profile(fake_config) -> None:
    fake_config["profiles"].append(copy.deepcopy(fake_config["profiles"][0]))
    with pytest.raises(ConfigValidationError, match="重复"):
        ConfigService(fake_config)


def test_config_rejects_unsafe_storage_and_timeout_values(fake_config) -> None:
    fake_config["retention"]["database_warn_mb"] = 300
    fake_config["retention"]["database_soft_limit_mb"] = 200
    with pytest.raises(ConfigValidationError, match="软上限"):
        ConfigService(fake_config)

    safe = copy.deepcopy(DEFAULT_CONFIG)
    safe["network"]["request_timeout_seconds"] = 0
    with pytest.raises(ConfigValidationError, match="request_timeout_seconds"):
        ConfigService(safe)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("section", "未知分区", "可选分区"),
        ("selection_strategy", "随便挑", "推荐顺序"),
    ],
)
def test_config_rejects_unknown_feed_select_values(key, value, message) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"][key] = value
    with pytest.raises(ConfigValidationError, match=message):
        ConfigService(config)
