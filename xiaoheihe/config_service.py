from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

from .security import SecurityError, validate_profile_id

DEFAULT_CONFIG: dict[str, Any] = {
    "profiles": [
        {
            "__template_key": "account",
            "profile_id": "default",
            "display_name": "默认账号",
            "enabled": True,
            "poll_mentions": True,
            "poll_replies": True,
            "dry_run": True,
            "owner_uid": "",
        }
    ],
    "polling": {
        "poll_interval_seconds": 60,
        "poll_jitter_seconds": 5.0,
        "max_pages_per_poll": 3,
        "page_size": 20,
        "initial_backfill_count": 0,
    },
    "providers": {
        "llm_provider_id": "",
        "image_provider_id": "",
        "context_provider_id": "",
    },
    "context": {
        "max_post_chars": 6000,
        "max_thread_comments": 40,
        "thread_reply_post_chars": 1600,
        "thread_reply_recent_comments": 12,
        "enable_thread_reply_compression": True,
        "thread_reply_compression_trigger_chars": 2400,
        "thread_reply_compressed_post_chars": 700,
        "thread_reply_compressed_comments_chars": 1400,
        "thread_reply_compressed_image_chars": 800,
        "thread_reply_compression_timeout_seconds": 30,
        "context_cache_ttl_seconds": 300,
        "context_cache_max_entries": 256,
        "enable_image_understanding": True,
        "max_images_per_event": 6,
        "max_image_size_mb": 8,
        "max_total_image_size_mb": 24,
        "image_timeout_seconds": 15,
    },
    "reply": {
        "dry_run_mark_processed": True,
        "max_reply_chars": 500,
        "reply_timeout_seconds": 120,
        "max_retries": 3,
        "only_explicit_mentions": True,
        "reply_to_direct_replies": True,
    },
    "permissions": {
        "whitelist_mode": False,
        "user_whitelist": [],
        "user_blacklist": [],
        "author_whitelist": [],
        "author_blacklist": [],
        "keyword_blacklist": [],
        "map_owner_to_astrbot_admin": False,
    },
    "network": {
        "request_timeout_seconds": 20,
        "connect_timeout_seconds": 8,
        "read_timeout_seconds": 15,
        "min_request_interval_seconds": 1.0,
        "max_reply_concurrency": 2,
        "max_pending_events": 50,
        "max_pending_per_user": 5,
    },
    "proactive_feed": {
        "enabled": False,
        "dry_run": True,
        "review_required": True,
        "interval_seconds": 900,
        "jitter_seconds": 60,
        "max_per_run": 1,
        "max_per_day": 10,
        "source": "all",
        "keywords": [],
        "allowed_post_types": [],
    },
    "retention": {
        "incoming_body_days": 30,
        "dry_run_days": 30,
        "failed_days": 30,
        "success_reply_days": 90,
        "session_mapping_days": 180,
        "dedup_days": 365,
        "database_warn_mb": 150,
        "database_soft_limit_mb": 200,
        "log_total_limit_mb": 100,
        "image_cache_soft_limit_mb": 200,
    },
    "logging": {"level": "INFO", "max_memory_entries": 2000},
}

PROACTIVE_FEED_SOURCES = {
    "all",
    "pc_game",
    "mobile_game",
    "console_game",
    "community",
    "daily",
    "digital_tech",
    "anime",
    "film_tv",
    "esports",
    "guide",
    "deals",
    "indie_game",
}

LEGACY_FEED_SOURCES = {
    "follow": "all",
    "game": "pc_game",
    "hardware": "digital_tech",
    "software": "digital_tech",
    "movie": "film_tv",
    "music": "daily",
    "life": "daily",
    "tech": "digital_tech",
}

RestartCallback = Callable[[set[str]], Awaitable[None]]


class ConfigValidationError(ValueError):
    """Configuration violates a documented safety invariant."""


class ConfigService:
    def __init__(self, config: MutableMapping[str, Any]) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._callbacks: list[RestartCallback] = []
        merged = self._merge_defaults(dict(config), DEFAULT_CONFIG)
        self._normalize_legacy(merged)
        self.validate(merged)
        self._replace_in_place(merged)

    @property
    def raw(self) -> MutableMapping[str, Any]:
        return self._config

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._config))

    def defaults(self) -> dict[str, Any]:
        return copy.deepcopy(DEFAULT_CONFIG)

    def ui_schema(self) -> dict[str, Any]:
        schema_path = Path(__file__).resolve().parent.parent / "_conf_schema.json"
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigValidationError(f"读取配置界面定义失败: {exc}") from exc
        if not isinstance(schema, dict):
            raise ConfigValidationError("配置界面定义根节点必须是对象")
        return schema

    def add_restart_callback(self, callback: RestartCallback) -> None:
        self._callbacks.append(callback)

    def profile(self, profile_id: str) -> dict[str, Any]:
        profile_key = validate_profile_id(profile_id)
        for profile in self._config.get("profiles", []):
            if str(profile.get("profile_id", "")) == profile_key:
                return copy.deepcopy(profile)
        raise ConfigValidationError(f"未找到账号档案: {profile_key}")

    def enabled_profiles(self) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(profile)
            for profile in self._config.get("profiles", [])
            if profile.get("enabled", True)
        ]

    async def save(self, replacement: dict[str, Any]) -> set[str]:
        candidate = self._merge_defaults(copy.deepcopy(replacement), DEFAULT_CONFIG)
        self._normalize_legacy(candidate)
        self.validate(candidate)
        async with self._lock:
            previous = self.snapshot()
            changed = {key for key in DEFAULT_CONFIG if previous.get(key) != candidate.get(key)}
            self._replace_in_place(candidate)
            try:
                save = getattr(self._config, "save_config", None)
                if not callable(save):
                    raise ConfigValidationError("AstrBotConfig 缺少 save_config()")
                save()
            except BaseException:
                self._replace_in_place(previous)
                raise
        for callback in tuple(self._callbacks):
            await callback(changed)
        return changed

    def validate(self, config: dict[str, Any]) -> None:
        profiles = config.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            raise ConfigValidationError("至少需要一个账号档案")
        seen: set[str] = set()
        for index, profile in enumerate(profiles):
            if not isinstance(profile, dict):
                raise ConfigValidationError(f"profiles[{index}] 必须是对象")
            try:
                profile_id = validate_profile_id(str(profile.get("profile_id", "")))
            except SecurityError as exc:
                raise ConfigValidationError(str(exc)) from exc
            if profile_id in seen:
                raise ConfigValidationError(f"profile_id 重复: {profile_id}")
            seen.add(profile_id)
            for key in ("owner_uid",):
                value = profile.get(key, "")
                if value is not None and not isinstance(value, (str, int)):
                    raise ConfigValidationError(f"{profile_id}.{key} 必须是 UID 字符串")

        polling = _object(config, "polling")
        _bounded_int(polling, "poll_interval_seconds", 30, 86400)
        _bounded_int(polling, "max_pages_per_poll", 1, 20)
        _bounded_int(polling, "page_size", 1, 100)
        _bounded_int(polling, "initial_backfill_count", 0, 500)
        _bounded_number(polling, "poll_jitter_seconds", 0, 300)

        context = _object(config, "context")
        _bounded_int(context, "max_post_chars", 500, 50000)
        _bounded_int(context, "max_thread_comments", 1, 200)
        _bounded_int(context, "thread_reply_post_chars", 200, 10000)
        _bounded_int(context, "thread_reply_recent_comments", 1, 50)
        _bounded_int(context, "thread_reply_compression_trigger_chars", 500, 50000)
        _bounded_int(context, "thread_reply_compressed_post_chars", 200, 3000)
        _bounded_int(context, "thread_reply_compressed_comments_chars", 300, 6000)
        _bounded_int(context, "thread_reply_compressed_image_chars", 200, 4000)
        _bounded_int(context, "thread_reply_compression_timeout_seconds", 3, 120)
        _bounded_int(context, "max_images_per_event", 0, 20)
        _bounded_int(context, "max_image_size_mb", 1, 32)
        _bounded_int(context, "max_total_image_size_mb", 1, 128)
        _bounded_int(context, "context_cache_ttl_seconds", 10, 86400)
        _bounded_int(context, "context_cache_max_entries", 1, 4096)
        _bounded_int(context, "image_timeout_seconds", 1, 120)
        if context["max_total_image_size_mb"] < context["max_image_size_mb"]:
            raise ConfigValidationError("图片总上限不能小于单图上限")

        reply = _object(config, "reply")
        _bounded_int(reply, "max_reply_chars", 1, 5000)
        _bounded_int(reply, "reply_timeout_seconds", 5, 600)
        _bounded_int(reply, "max_retries", 0, 8)

        network = _object(config, "network")
        _bounded_number(network, "request_timeout_seconds", 1, 300)
        _bounded_number(network, "connect_timeout_seconds", 1, 120)
        _bounded_number(network, "read_timeout_seconds", 1, 300)
        _bounded_int(network, "max_reply_concurrency", 1, 16)
        _bounded_int(network, "max_pending_events", 1, 1000)
        _bounded_int(network, "max_pending_per_user", 1, 100)
        if network["max_pending_per_user"] > network["max_pending_events"]:
            raise ConfigValidationError("单用户队列上限不能大于全局队列上限")
        if not 0.2 <= float(network["min_request_interval_seconds"]) <= 60:
            raise ConfigValidationError("最小请求间隔必须在 0.2–60 秒")

        providers = _object(config, "providers")
        for key in ("llm_provider_id", "image_provider_id", "context_provider_id"):
            value = providers.get(key)
            if not isinstance(value, str):
                raise ConfigValidationError(f"providers.{key} 必须是字符串")
            if len(value) > 256:
                raise ConfigValidationError(f"providers.{key} 不能超过 256 字符")

        proactive = _object(config, "proactive_feed")
        _bounded_int(proactive, "interval_seconds", 300, 86400)
        _bounded_int(proactive, "jitter_seconds", 0, 3600)
        _bounded_int(proactive, "max_per_run", 1, 20)
        _bounded_int(proactive, "max_per_day", 1, 100)
        for key in ("keywords", "allowed_post_types"):
            values = proactive.get(key)
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise ConfigValidationError(f"proactive_feed.{key} 必须是字符串列表")
            if any(len(item) > 100 for item in values):
                raise ConfigValidationError(f"proactive_feed.{key} 单项不能超过 100 字符")
        source = proactive.get("source")
        if source not in PROACTIVE_FEED_SOURCES:
            raise ConfigValidationError("proactive_feed.source 必须是受支持的推荐流分区")

        permissions = _object(config, "permissions")
        for key in (
            "user_whitelist",
            "user_blacklist",
            "author_whitelist",
            "author_blacklist",
            "keyword_blacklist",
        ):
            values = permissions.get(key)
            if not isinstance(values, list) or not all(
                isinstance(item, (str, int)) for item in values
            ):
                raise ConfigValidationError(f"permissions.{key} 必须是字符串列表")
            if any(len(str(item)) > 200 for item in values):
                raise ConfigValidationError(f"permissions.{key} 单项不能超过 200 字符")

        retention = _object(config, "retention")
        for key in (
            "incoming_body_days",
            "dry_run_days",
            "failed_days",
            "success_reply_days",
            "session_mapping_days",
            "dedup_days",
        ):
            _bounded_int(retention, key, 1, 3650)
        _bounded_int(retention, "database_warn_mb", 1, 10240)
        _bounded_int(retention, "database_soft_limit_mb", 1, 10240)
        _bounded_int(retention, "log_total_limit_mb", 5, 2048)
        _bounded_int(retention, "image_cache_soft_limit_mb", 1, 10240)
        if retention["database_soft_limit_mb"] < retention["database_warn_mb"]:
            raise ConfigValidationError("数据库软上限不能小于警告阈值")

        logging = _object(config, "logging")
        if logging.get("level") not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ConfigValidationError("logging.level 无效")
        _bounded_int(logging, "max_memory_entries", 100, 10000)

    def _replace_in_place(self, value: dict[str, Any]) -> None:
        self._config.clear()
        self._config.update(copy.deepcopy(value))

    @staticmethod
    def _normalize_legacy(config: dict[str, Any]) -> None:
        proactive = config.get("proactive_feed")
        if isinstance(proactive, dict):
            source = proactive.get("source")
            if source in LEGACY_FEED_SOURCES:
                proactive["source"] = LEGACY_FEED_SOURCES[source]

    @classmethod
    def _merge_defaults(cls, value: Any, defaults: Any) -> Any:
        if isinstance(defaults, dict):
            source = value if isinstance(value, dict) else {}
            return {
                key: cls._merge_defaults(source.get(key), default_value)
                for key, default_value in defaults.items()
            }
        if value is None:
            return copy.deepcopy(defaults)
        return copy.deepcopy(value)


def _object(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if not isinstance(value, dict):
        raise ConfigValidationError(f"{key} 必须是对象")
    return value


def _bounded_int(value: dict[str, Any], key: str, minimum: int, maximum: int) -> None:
    candidate = value.get(key)
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise ConfigValidationError(f"{key} 必须是整数")
    if not minimum <= candidate <= maximum:
        raise ConfigValidationError(f"{key} 必须在 {minimum}–{maximum} 之间")


def _bounded_number(value: dict[str, Any], key: str, minimum: float, maximum: float) -> None:
    candidate = value.get(key)
    if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
        raise ConfigValidationError(f"{key} 必须是数字")
    if not minimum <= float(candidate) <= maximum:
        raise ConfigValidationError(f"{key} 必须在 {minimum}–{maximum} 之间")
