from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .api_client import ResponseContractError, XiaoheiheApiClient
from .endpoints import EndpointName
from .models import ApiPage
from .parsers import ResponseShapeError, parse_feed

TOPIC_FEED_FIRST_PAGE_LIMIT = 10
MAX_TOPIC_SELECTIONS = 20


async def fetch_topic_catalog(client: XiaoheiheApiClient) -> list[dict[str, str]]:
    """Read Xiaoheihe's real topic catalog without modifying account state."""

    response = await client._request(
        EndpointName.TOPIC_INDEX,
        params={"type": "list"},
    )
    payload = response.payload
    candidate = payload.get("result", payload.get("data", payload))
    if isinstance(candidate, Mapping):
        raw_topics = candidate.get("topics_list", payload.get("topics_list", []))
    else:
        raw_topics = payload.get("topics_list", [])
    topics = _flatten_topic_catalog(raw_topics)
    if not topics:
        raise ResponseContractError("真实分区列表为空或响应字段已变化", category="response_shape")
    return topics


async def fetch_topic_feed(
    client: XiaoheiheApiClient,
    topic_id: str,
    *,
    offset: int = 0,
    limit: int = TOPIC_FEED_FIRST_PAGE_LIMIT,
    lastval: str = "",
) -> ApiPage:
    """Read one real Xiaoheihe topic feed; this endpoint is GET-only."""

    normalized_topic_id = normalize_topic_id(topic_id)
    normalized_offset = max(0, int(offset))
    normalized_limit = max(1, min(int(limit), 30))
    normalized_lastval = str(lastval or "").strip()[:2048]
    response = await client._request(
        EndpointName.TOPIC_FEED,
        params={
            "topic_id": normalized_topic_id,
            "offset": normalized_offset,
            "limit": normalized_limit,
            "lastval": normalized_lastval,
            "dw": 720,
        },
    )
    try:
        page = parse_feed(response.payload, offset=normalized_offset)
    except ResponseShapeError as exc:
        raise ResponseContractError(str(exc), category="response_shape") from exc
    for item in page.items:
        item["source_topic_id"] = normalized_topic_id
    return page


def normalize_topic_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            topic_id = normalize_topic_id(str(value))
        except ValueError:
            continue
        if topic_id in seen:
            continue
        seen.add(topic_id)
        result.append(topic_id)
        if len(result) >= MAX_TOPIC_SELECTIONS:
            break
    return result


def normalize_topic_id(value: str) -> str:
    topic_id = str(value or "").strip()
    if not topic_id or len(topic_id) > 32 or not topic_id.isdigit():
        raise ValueError("topic_id 必须是 1-32 位数字")
    return topic_id


def _flatten_topic_catalog(value: Any) -> list[dict[str, str]]:
    topics: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(node: Any, group: str = "") -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, group)
            return
        if not isinstance(node, Mapping):
            return

        topic_id = _first_text(node, "topic_id", "id")
        name = _first_text(node, "name", "title", "topic_name")
        next_group = group
        if not topic_id and name:
            next_group = name
        if topic_id and topic_id.isdigit() and name and topic_id not in seen:
            seen.add(topic_id)
            topics.append({"id": topic_id, "name": name, "group": group})

        for key, child in node.items():
            if key in {"topic_id", "id", "name", "title", "topic_name"}:
                continue
            if isinstance(child, (list, Mapping)):
                walk(child, next_group)

    walk(value)
    return topics


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
