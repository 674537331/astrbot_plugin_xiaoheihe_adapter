from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _provider_ids(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[str] = []
    for value in values:
        provider_id = str(value or "").strip()
        if provider_id and provider_id not in result:
            result.append(provider_id)
    return tuple(result)


def _unique_provider_ids(*values: str | tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        items = value if isinstance(value, tuple) else (value,)
        for item in items:
            provider_id = str(item or "").strip()
            if provider_id and provider_id not in result:
                result.append(provider_id)
    return tuple(result)


def visual_chain_budget_seconds(
    *,
    image_count: int,
    max_images: int,
    image_timeout_seconds: float,
    total_timeout_seconds: float,
    provider_candidate_count: int,
    image_group_counts: tuple[int, ...] | list[int] | None = None,
    min_attempt_seconds: float = 15.0,
    max_attempt_seconds: float = 120.0,
    max_total_seconds: float = 600.0,
    max_provider_candidates: int = 3,
) -> float:
    """Return a bounded sequential vision budget for source groups and providers."""

    count = min(max(0, int(image_count)), max(0, int(max_images)))
    candidates = max(0, min(int(provider_candidate_count), int(max_provider_candidates)))
    if count == 0 or candidates == 0:
        return 0.0

    remaining = count
    groups: list[int] = []
    for raw_count in image_group_counts or ():
        group_count = min(remaining, max(0, int(raw_count)))
        if group_count:
            groups.append(group_count)
            remaining -= group_count
        if remaining <= 0:
            break
    if remaining:
        groups.append(remaining)

    per_chain_attempt_budget = sum(
        min(
            float(max_attempt_seconds),
            max(float(min_attempt_seconds), float(image_timeout_seconds) * group_count),
        )
        for group_count in groups
    )
    configured_total = max(
        float(min_attempt_seconds),
        min(float(max_total_seconds), float(total_timeout_seconds)),
    )
    return min(
        float(max_total_seconds),
        configured_total,
        per_chain_attempt_budget * candidates,
    )


@dataclass(frozen=True, slots=True)
class ProviderRoutePlan:
    """Resolved provider order without retaining Provider instances."""

    plugin_main_provider_id: str
    astrbot_main_provider_id: str
    astrbot_image_provider_id: str
    astrbot_fallback_provider_ids: tuple[str, ...]
    image_provider_ids: tuple[str, ...]
    desired_main_provider_ids: tuple[str, ...]
    native_main_provider_ids: tuple[str, ...]
    astrbot_main_is_first_fallback: bool

    @property
    def needs_main_fallback_configuration(self) -> bool:
        return bool(
            self.plugin_main_provider_id
            and self.astrbot_main_provider_id
            and self.plugin_main_provider_id != self.astrbot_main_provider_id
            and not self.astrbot_main_is_first_fallback
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "plugin_main_provider_id": self.plugin_main_provider_id,
            "astrbot_main_provider_id": self.astrbot_main_provider_id,
            "astrbot_image_provider_id": self.astrbot_image_provider_id,
            "astrbot_fallback_provider_ids": list(self.astrbot_fallback_provider_ids),
            "image_provider_ids": list(self.image_provider_ids),
            "desired_main_provider_ids": list(self.desired_main_provider_ids),
            "native_main_provider_ids": list(self.native_main_provider_ids),
            "astrbot_main_is_first_fallback": self.astrbot_main_is_first_fallback,
            "needs_main_fallback_configuration": self.needs_main_fallback_configuration,
        }


def build_provider_route_plan(
    plugin_provider_settings: dict[str, Any],
    astrbot_provider_settings: dict[str, Any],
    *,
    astrbot_main_provider_id: str,
    plugin_main_provider_id: str | None = None,
) -> ProviderRoutePlan:
    """Build the intended and AstrBot-native provider chains.

    AstrBot 4.x accepts one event-level ``selected_provider`` and obtains all
    fallbacks from ``provider_settings.fallback_chat_models``.  Keeping both
    chains explicit lets the plugin warn instead of silently claiming that the
    profile's main provider was inserted into an event-specific fallback list.
    """

    configured_plugin_main = str(
        plugin_main_provider_id
        if plugin_main_provider_id is not None
        else plugin_provider_settings.get("llm_provider_id", "")
    ).strip()
    plugin_image = str(plugin_provider_settings.get("image_provider_id", "") or "").strip()
    astrbot_main = str(astrbot_main_provider_id or "").strip()
    astrbot_image = str(
        astrbot_provider_settings.get("default_image_caption_provider_id", "") or ""
    ).strip()
    fallbacks = _provider_ids(astrbot_provider_settings.get("fallback_chat_models", ()))

    image_chain = _unique_provider_ids(plugin_image, astrbot_image, astrbot_main)
    desired_main = _unique_provider_ids(configured_plugin_main, astrbot_main, fallbacks)
    if configured_plugin_main:
        native_main = _unique_provider_ids(configured_plugin_main, fallbacks)
        effective_fallbacks = tuple(
            provider_id for provider_id in fallbacks if provider_id != configured_plugin_main
        )
        astrbot_main_is_first_fallback = bool(
            not astrbot_main
            or configured_plugin_main == astrbot_main
            or (effective_fallbacks and effective_fallbacks[0] == astrbot_main)
        )
    else:
        native_main = _unique_provider_ids(astrbot_main, fallbacks)
        astrbot_main_is_first_fallback = True

    return ProviderRoutePlan(
        plugin_main_provider_id=configured_plugin_main,
        astrbot_main_provider_id=astrbot_main,
        astrbot_image_provider_id=astrbot_image,
        astrbot_fallback_provider_ids=fallbacks,
        image_provider_ids=image_chain,
        desired_main_provider_ids=desired_main,
        native_main_provider_ids=native_main,
        astrbot_main_is_first_fallback=astrbot_main_is_first_fallback,
    )
