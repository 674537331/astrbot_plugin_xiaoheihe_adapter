from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .security import redact_log_data, redact_log_text
from .task_manager import TaskManager

LEVEL_PRIORITIES = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


class LoggingService:
    def __init__(
        self,
        data_dir: Path,
        task_manager: TaskManager,
        *,
        level: str = "INFO",
        max_memory_entries: int = 2000,
    ) -> None:
        self._entries: deque[dict[str, Any]] = deque(
            maxlen=max(100, min(max_memory_entries, 10000))
        )
        self._tasks = task_manager
        # The path is read only for legacy storage accounting. AstrBot owns all
        # persistent logging; this service never creates or attaches handlers.
        self._log_path = data_dir / "logs" / "xiaoheihe.log"
        self._minimum_level = LEVEL_PRIORITIES.get(level.upper(), LEVEL_PRIORITIES["INFO"])

    @property
    def log_path(self) -> Path:
        return self._log_path

    def emit(
        self,
        level: str,
        message: str,
        *,
        profile_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_level = level.upper()
        if normalized_level not in LEVEL_PRIORITIES:
            normalized_level = "INFO"
        safe_message = redact_log_text(message).replace("\r", "\\r").replace("\n", "\\n")
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "level": normalized_level,
            "profile_id": profile_id,
            "message": safe_message,
            "details": redact_log_data(details or {}),
        }
        self._entries.append(entry)
        if LEVEL_PRIORITIES[normalized_level] >= self._minimum_level:
            log_method = getattr(logger, normalized_level.lower())
            rendered = f"[小黑盒][profile={profile_id or '-'}] {safe_message}"
            if entry["details"]:
                rendered += f" {entry['details']}"
            log_method(rendered)
        self._tasks.publish_sse({"type": "log", "entry": entry})
        return entry

    def list(self, *, level: str = "", keyword: str = "", limit: int = 200) -> list[dict[str, Any]]:
        level_filter = level.upper()
        needle = keyword.casefold()
        result: list[dict[str, Any]] = []
        for entry in reversed(self._entries):
            if level_filter and entry["level"] != level_filter:
                continue
            if needle and needle not in str(entry).casefold():
                continue
            result.append(entry)
            if len(result) >= max(1, min(limit, 1000)):
                break
        return result

    def total_size(self) -> int:
        """Return existing legacy log bytes without creating or changing files."""

        return sum(
            path.stat().st_size
            for path in self._log_path.parent.glob("xiaoheihe.log*")
            if path.is_file()
        )

    def reconfigure(
        self,
        *,
        level: str,
        max_memory_entries: int,
    ) -> None:
        self._minimum_level = LEVEL_PRIORITIES.get(level.upper(), LEVEL_PRIORITIES["INFO"])
        max_entries = max(100, min(max_memory_entries, 10000))
        if self._entries.maxlen != max_entries:
            self._entries = deque(list(self._entries)[-max_entries:], maxlen=max_entries)

    def close(self) -> None:
        """Keep the lifecycle hook; AstrBot owns the logger and its resources."""
