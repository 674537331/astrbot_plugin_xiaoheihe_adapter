from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, got {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "main.py",
    '''        source = fallback_source if fallback_source in VALID_IMAGE_SOURCES else "event_image"\n        fallback = cls._fallback_image_attribution(event, source)\n        if not isinstance(value, dict):\n            return fallback\n        supplied_source = str(value.get("source", "") or "").strip()\n''',
    '''        source = fallback_source if fallback_source in VALID_IMAGE_SOURCES else "event_image"\n        fallback = cls._fallback_image_attribution(event, source)\n        if not isinstance(value, dict):\n            return fallback\n        supplied_source = str(value.get("source", "") or "").strip()\n        if source in {"direct_reply_target", "thread_anchor"}:\n            expected_role = (\n                ContentOwnerRole.DIRECT_REPLY_TARGET.value\n                if source == "direct_reply_target"\n                else ContentOwnerRole.THREAD_ANCHOR.value\n            )\n            role = str(value.get("owner_role", "") or "").strip()\n            identity_key = cls._identity_value(\n                value.get("owner_identity_key"), fallback=""\n            )\n            if (\n                supplied_source != source\n                or role != expected_role\n                or not identity_key.startswith("comment:")\n            ):\n                return cls._fallback_image_attribution(event, "event_image")\n            return ImageAttribution(\n                source=source,\n                owner_uid=cls._identity_value(value.get("owner_uid"), fallback="未知"),\n                owner_nickname=cls._identity_value(\n                    value.get("owner_nickname"), fallback="未知昵称"\n                ),\n                owner_role=expected_role,\n                owner_identity_key=identity_key,\n            )\n''',
)

replace_once(
    "main.py",
    '''            if source == "current_comment":\n                # The user's own image is part of the highest-priority current\n                # message.  Preserve it only as the last-resort AstrBot native\n                # vision fallback; low-priority post images never get this path.\n                remaining_urls.extend(urls)\n''',
    '''            if source in {"current_comment", "direct_reply_target"}:\n                # Current and directly quoted images are the two nearest visual\n                # sources. Preserve them as the last-resort AstrBot native vision\n                # fallback; lower-priority anchor/post images remain fail-closed.\n                remaining_urls.extend(urls)\n''',
)
replace_once(
    "main.py",
    '                    "当前评论图片预处理失败，保留原图作为最终视觉兜底",\n',
    '                    "当前/直接回复图片预处理失败，保留原图作为最终视觉兜底",\n',
)

replace_once(
    "xiaoheihe/context_builder.py",
    '''def _prioritized_attributed_images(\n    *sources: tuple[ImageAttribution, list[str]],\n) -> list[tuple[str, ImageAttribution]]:\n    values: list[tuple[str, ImageAttribution]] = []\n    for attribution, items in sorted(\n        sources, key=lambda item: image_source_priority(item[0].source)\n    ):\n        values.extend((value, attribution) for value in items if value)\n    return values\n''',
    '''def _prioritized_attributed_images(\n    *sources: tuple[ImageAttribution, list[str]],\n) -> list[tuple[str, ImageAttribution]]:\n    ordered = sorted(sources, key=lambda item: image_source_priority(item[0].source))\n    local_sources = [item for item in ordered if item[0].source != "original_post"]\n    post_sources = [item for item in ordered if item[0].source == "original_post"]\n    values: list[tuple[str, ImageAttribution]] = []\n\n    # Keep one representative from every nearby conversational source before\n    # extra images from the current comment. This prevents a six-image current\n    # comment from starving the directly quoted image, while original-post\n    # images never displace any local source.\n    remaining: list[tuple[ImageAttribution, list[str]]] = []\n    for attribution, items in local_sources:\n        cleaned = [value for value in items if value]\n        if not cleaned:\n            continue\n        values.append((cleaned[0], attribution))\n        if len(cleaned) > 1:\n            remaining.append((attribution, cleaned[1:]))\n    for attribution, items in remaining:\n        values.extend((value, attribution) for value in items)\n    for attribution, items in post_sources:\n        values.extend((value, attribution) for value in items if value)\n    return values\n''',
)

path = Path("tests/test_main_contract.py")
text = path.read_text(encoding="utf-8")
marker = "def test_v133_direct_reply_image_attribution_survives_validation("
if marker not in text:
    text += r'''


def test_v133_direct_reply_image_attribution_survives_validation(
    isolated_smoke_import,
) -> None:
    root = Path.cwd()
    spec = importlib.util.spec_from_file_location(
        "xhh_plugin_smoke",
        root / "main.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Event:
        message_obj = type("Message", (), {"raw_message": {}})()

        @staticmethod
        def get_extra(_key, default=None):
            return default

    value = {
        "source": "direct_reply_target",
        "owner_uid": "target-user",
        "owner_nickname": "被回复用户",
        "owner_role": "direct_reply_target",
        "owner_identity_key": "comment:target-1",
    }
    attribution = module.XiaoheiheAdapterPlugin._coerce_image_attribution(
        Event(), value, fallback_source="direct_reply_target"
    )
    assert attribution.source == "direct_reply_target"
    assert attribution.owner_uid == "target-user"
    assert attribution.owner_role == "direct_reply_target"
    assert attribution.owner_identity_key == "comment:target-1"

    rejected = module.XiaoheiheAdapterPlugin._coerce_image_attribution(
        Event(),
        {**value, "owner_role": "post_author"},
        fallback_source="direct_reply_target",
    )
    assert rejected.source == "event_image"
    assert rejected.owner_role == "unknown"
'''
    path.write_text(text, encoding="utf-8")
