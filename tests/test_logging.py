from __future__ import annotations

import logging

from xiaoheihe import logging_service as logging_module
from xiaoheihe.logging_service import LoggingService
from xiaoheihe.task_manager import TaskManager


async def test_logging_redacts_and_rotates_service_closes(tmp_path) -> None:
    tasks = TaskManager()
    service = LoggingService(tmp_path, tasks, max_memory_entries=100)
    entry = service.emit(
        "INFO",
        "Authorization: Bearer secret-value",
        details={"cookie": "private"},
    )
    assert "secret-value" not in entry["message"]
    assert entry["details"]["cookie"] == "[REDACTED]"
    assert logging_module.redact_data({"session_mappings": 2}) == {"session_mappings": 2}
    assert service.list(limit=1)[0] == entry
    assert service.total_size() >= 0
    service.reconfigure(level="ERROR", max_memory_entries=250, total_limit_mb=50)
    assert service._entries.maxlen == 250
    assert service._handler.backupCount == 9
    service.close()
    await tasks.close()


async def test_plugin_file_logger_does_not_reach_astrbot_root_formatter(
    tmp_path,
) -> None:
    class RejectRawPluginRecord(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise AssertionError("raw plugin record propagated to AstrBot root logger")

    tasks = TaskManager()
    service = LoggingService(tmp_path, tasks)
    root_logger = logging.getLogger()
    handler = RejectRawPluginRecord()
    root_logger.addHandler(handler)
    try:
        service.emit("INFO", "status probe")
    finally:
        root_logger.removeHandler(handler)
        service.close()
        await tasks.close()

    assert service._logger.propagate is False


async def test_logging_uses_astrbot_public_logger(tmp_path, monkeypatch) -> None:
    class FakeAstrBotLogger:
        def __init__(self) -> None:
            self.calls = []

        def info(self, message, *args) -> None:
            self.calls.append((message, args))

    public_logger = FakeAstrBotLogger()
    monkeypatch.setattr(logging_module, "astrbot_logger", public_logger)
    tasks = TaskManager()
    service = LoggingService(tmp_path, tasks)
    try:
        service.emit("INFO", "status refreshed", profile_id="default")
    finally:
        service.close()
        await tasks.close()

    assert public_logger.calls == [
        (
            "[小黑盒][profile=%s] %s %s",
            ("default", "status refreshed", ""),
        )
    ]
