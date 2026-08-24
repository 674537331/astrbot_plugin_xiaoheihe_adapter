"""Deterministic routing primitives for keeping nearby conversation context closest."""

from __future__ import annotations

from dataclasses import dataclass

VALID_RELATIONS = frozenset({"related", "partial", "drifted", "unclear"})

_ORIGINAL_POST_REFERENCE_MARKERS = (
    "原帖",
    "主帖",
    "主贴",
    "主楼",
    "楼主",
    "题主",
    "帖子里",
    "帖子中",
    "帖子那",
    "帖子这",
    "原帖图",
    "主帖图",
    "主贴图",
    "楼主图",
    "原图",
    "上面的帖子",
    "上面帖子",
)

_IMAGE_SOURCE_PRIORITY = {
    "current_comment": 0,
    "direct_reply_target": 1,
    "thread_anchor": 2,
    "original_post": 3,
    "event_image": 4,
}


@dataclass(frozen=True, slots=True)
class ContextRelevancePlan:
    relation_to_post: str = "unclear"
    explicit_post_reference: bool = False

    @property
    def preserve_original_post(self) -> bool:
        return self.explicit_post_reference or self.relation_to_post != "drifted"

    @property
    def suppress_original_post(self) -> bool:
        return not self.preserve_original_post


def normalize_relation(value: object) -> str:
    relation = str(value or "unclear").strip().casefold()
    return relation if relation in VALID_RELATIONS else "unclear"


def detect_explicit_original_post_reference(text: object) -> bool:
    normalized = " ".join(str(text or "").split()).casefold()
    if not normalized:
        return False
    return any(marker.casefold() in normalized for marker in _ORIGINAL_POST_REFERENCE_MARKERS)


def should_preserve_original_post(
    relation_to_post: object,
    *,
    explicit_post_reference: bool = False,
) -> bool:
    return ContextRelevancePlan(
        relation_to_post=normalize_relation(relation_to_post),
        explicit_post_reference=bool(explicit_post_reference),
    ).preserve_original_post


def image_source_priority(source: object) -> int:
    return _IMAGE_SOURCE_PRIORITY.get(str(source or "event_image"), 99)
