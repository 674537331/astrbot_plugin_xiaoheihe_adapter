from __future__ import annotations

from xiaoheihe.provider_routing import build_provider_route_plan


def test_provider_route_plan_keeps_image_chain_order_and_deduplicates() -> None:
    plan = build_provider_route_plan(
        {
            "llm_provider_id": "plugin-main",
            "image_provider_id": "plugin-image",
        },
        {
            "default_image_caption_provider_id": "astrbot-image",
            "fallback_chat_models": ["astrbot-main", "fallback-1", "fallback-1"],
        },
        astrbot_main_provider_id="astrbot-main",
    )

    assert plan.image_provider_ids == (
        "plugin-image",
        "astrbot-image",
        "astrbot-main",
    )
    assert plan.desired_main_provider_ids == (
        "plugin-main",
        "astrbot-main",
        "fallback-1",
    )
    assert plan.native_main_provider_ids == (
        "plugin-main",
        "astrbot-main",
        "fallback-1",
    )
    assert plan.needs_main_fallback_configuration is False


def test_provider_route_plan_reports_astrbot_4_event_fallback_gap() -> None:
    plan = build_provider_route_plan(
        {"llm_provider_id": "plugin-main"},
        {"fallback_chat_models": ["fallback-1", "astrbot-main"]},
        astrbot_main_provider_id="astrbot-main",
    )

    assert plan.desired_main_provider_ids == (
        "plugin-main",
        "astrbot-main",
        "fallback-1",
    )
    assert plan.native_main_provider_ids == (
        "plugin-main",
        "fallback-1",
        "astrbot-main",
    )
    assert plan.needs_main_fallback_configuration is True


def test_provider_route_plan_uses_astrbot_native_chain_without_plugin_main() -> None:
    plan = build_provider_route_plan(
        {},
        {"fallback_chat_models": ["fallback-1"]},
        astrbot_main_provider_id="astrbot-main",
    )

    assert plan.desired_main_provider_ids == ("astrbot-main", "fallback-1")
    assert plan.native_main_provider_ids == ("astrbot-main", "fallback-1")
    assert plan.needs_main_fallback_configuration is False
