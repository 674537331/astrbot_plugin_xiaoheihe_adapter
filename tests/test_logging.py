from __future__ import annotations

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
    assert service.list(limit=1)[0] == entry
    assert service.total_size() >= 0
    service.reconfigure(level="ERROR", max_memory_entries=250, total_limit_mb=50)
    assert service._entries.maxlen == 250
    assert service._handler.backupCount == 9
    service.close()
    await tasks.close()
