from __future__ import annotations

import copy
import json
from pathlib import Path

from xiaoheihe.config_service import DEFAULT_CONFIG, ConfigService


def test_legacy_source_survives_astrbot_schema_default_prefill() -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["source"] = "hardware"
    config["proactive_feed"]["fallback_sources"] = ["all"]

    snapshot = ConfigService(config).snapshot()

    assert snapshot["proactive_feed"]["fallback_sources"] == ["digital_tech"]
    assert "source" not in snapshot["proactive_feed"]


def test_new_fallback_selection_wins_over_stale_legacy_source() -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["proactive_feed"]["source"] = "game"
    config["proactive_feed"]["fallback_sources"] = ["anime", "digital_tech"]

    snapshot = ConfigService(config).snapshot()

    assert snapshot["proactive_feed"]["fallback_sources"] == ["anime", "digital_tech"]
    assert "source" not in snapshot["proactive_feed"]


def test_raw_schema_persists_browse_source_fields_without_generic_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    raw_schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    proactive = raw_schema["proactive_feed"]["items"]

    assert proactive["source"]["invisible"] is True
    assert proactive["topic_ids"] == {
        "description": "真实分区 ID（由浏览来源页管理）",
        "type": "list",
        "default": [],
        "invisible": True,
        "items": {"type": "string"},
    }
    assert proactive["fallback_sources"]["default"] == ["all"]
    assert proactive["fallback_sources"]["invisible"] is True

    ui_schema = ConfigService(copy.deepcopy(DEFAULT_CONFIG)).ui_schema()
    ui_items = ui_schema["proactive_feed"]["items"]
    assert "source" not in ui_items
    assert "topic_ids" not in ui_items
    assert "fallback_sources" not in ui_items
