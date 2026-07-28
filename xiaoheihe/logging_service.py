from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .security import redact_data, redact_text
from .task_manager import TaskManager

try:
    from astrbot.api import logger as astrbot_logger
except ImportError:
    astrbot_logger = None


class LoggingService:
    def __init__(
        self,
        data_dir: Path,
        task_manager: TaskManager,
        *,
        level: str = "INFO",
        max_memory_entries: int = 2000,
        total_limit_mb: int = 100,
    ) -> None:
        self._entries: deque[dict[str, Any]] = deque(
            maxlen=max(100, min(max_memory_entries, 10000))
        )
        self._tasks = task_manager
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / "xiaoheihe.log"
        self._logger = logging.getLogger(f"astrbot.xiaoheihe.file.{id(self)}")
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        # AstrBot's root formatter expects fields injected only by its public
        # logger. Keep the plugin file logger isolated, then emit separately
        # through astrbot.api.logger when that public API is available.
        self._logger.propagate = False
        per_file = 5 * 1024 * 1024
        backups = max(1, min(39, int(total_limit_mb) // 5 - 1))
        handler = RotatingFileHandler(
            self._log_path,
            maxBytes=per_file,
            backupCount=backups,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self._handler = handler
        self._logger.addHandler(handler)

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
        safe_message = redact_text(message).replace("\r", "\\r").replace("\n", "\\n")
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "level": normalized_level,
            "profile_id": profile_id,
            "message": safe_message,
            "details": redact_data(details or {}),
        }
        self._entries.append(entry)
        log_method = getattr(self._logger, normalized_level.lower(), self._logger.info)
        log_method(
            "[profile=%s] %s %s",
            profile_id or "-",
            safe_message,
            entry["details"] or "",
        )
        if astrbot_logger is not None:
            astrbot_log_method = getattr(
                astrbot_logger,
                normalized_level.lower(),
                astrbot_logger.info,
            )
            astrbot_log_method(
                "[小黑盒][profile=%s] %s %s",
                profile_id or "-",
                safe_message,
                entry["details"] or "",
            )
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
        total_limit_mb: int,
    ) -> None:
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        max_entries = max(100, min(max_memory_entries, 10000))
        if self._entries.maxlen != max_entries:
            self._entries = deque(list(self._entries)[-max_entries:], maxlen=max_entries)
        self._handler.backupCount = max(1, min(39, int(total_limit_mb) // 5 - 1))

    def close(self) -> None:
        self._logger.removeHandler(self._handler)
        self._handler.close()
