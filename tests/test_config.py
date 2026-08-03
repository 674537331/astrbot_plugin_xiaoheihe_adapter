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
    fake_config.pop("providers")
    service = ConfigService(fake_config)
    assert service.profile("default")["dry_run"] is True
    assert service.snapshot()["network"]["max_pending_events"] == 50
    assert service.snapshot()["providers"] == {
        "llm_provider_id": "",
        "image_provider_id": "",
    }


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
async def test_config_allows_explicit_unreviewed_proactive_send(fake_config) -> None:
    service = ConfigService(fake_config)
    candidate = copy.deepcopy(DEFAULT_CONFIG)
    candidate["proactive_feed"].update(
        {"enabled": True, "dry_run": False, "review_required": False}
    )
    changed = await service.save(candidate)
    assert changed == {"proactive_feed"}
    assert service.snapshot()["proactive_feed"] == candidate["proactive_feed"]


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


def test_config_rejects_invalid_provider_ids() -> None:
    invalid_type = copy.deepcopy(DEFAULT_CONFIG)
    invalid_type["providers"]["llm_provider_id"] = 123
    with pytest.raises(ConfigValidationError, match="llm_provider_id"):
        ConfigService(invalid_type)

    too_long = copy.deepcopy(DEFAULT_CONFIG)
    too_long["providers"]["image_provider_id"] = "x" * 257
    with pytest.raises(ConfigValidationError, match="image_provider_id"):
        ConfigService(too_long)
