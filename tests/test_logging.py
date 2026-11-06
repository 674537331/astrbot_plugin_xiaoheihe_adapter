from __future__ import annotations

import asyncio

from xiaoheihe import logging_service as logging_module
from xiaoheihe.logging_service import LoggingService
from xiaoheihe.task_manager import TaskManager


class PublicLoggerSpy:
    def __init__(self) -> None:
        self.calls = []

    def debug(self, message: str) -> None:
        self.calls.append(("DEBUG", message))

    def info(self, message: str) -> None:
        self.calls.append(("INFO", message))

    def warning(self, message: str) -> None:
        self.calls.append(("WARNING", message))

    def error(self, message: str) -> None:
        self.calls.append(("ERROR", message))


async def test_public_logger_receives_only_redacted_text_without_creating_files(
    tmp_path, monkeypatch
) -> None:
    public_logger = PublicLoggerSpy()
    monkeypatch.setattr(logging_module, "logger", public_logger)
    tasks = TaskManager()
    service = LoggingService(tmp_path, tasks, max_memory_entries=100)
    try:
        entry = service.emit(
            "INFO",
            "Authorization: Bearer secret-value; https://cdn.example.test/image.png?sig=private",
            profile_id="default",
            details={"cookie": "private", "_rnd": "fixture-signature"},
        )
        assert service.list(limit=1) == [entry]
        assert entry["details"] == {"cookie": "[REDACTED]", "_rnd": "[REDACTED]"}
        assert len(public_logger.calls) == 1
        level, rendered = public_logger.calls[0]
        assert level == "INFO"
        assert rendered.startswith("[小黑盒][profile=default]")
        for secret in ("secret-value", "sig=private", "fixture-signature"):
            assert secret not in rendered
        assert service.total_size() == 0
        assert list(tmp_path.iterdir()) == []
    finally:
        service.close()
        await tasks.close()


async def test_level_changes_filter_public_output_and_preserve_management_cache(
    tmp_path, monkeypatch
) -> None:
    public_logger = PublicLoggerSpy()
    monkeypatch.setattr(logging_module, "logger", public_logger)
    tasks = TaskManager()
    service = LoggingService(tmp_path, tasks, level="INFO")
    try:
        debug = service.emit("DEBUG", "local diagnostic")
        assert public_logger.calls == []
        service.reconfigure(level="ERROR", max_memory_entries=100)
        info = service.emit("INFO", "status refreshed")
        error = service.emit("ERROR", "upstream failed")
        assert public_logger.calls == [("ERROR", "[小黑盒][profile=-] upstream failed")]
        assert service.list() == [error, info, debug]
        assert service.list(level="error", keyword="upstream") == [error]
        assert service.list(keyword="no match") == []
        service.reconfigure(level="DEBUG", max_memory_entries=100)
        for number in range(101):
            service.emit("DEBUG", f"bounded entry {number}")
        assert len(service.list(limit=1000)) == 100
        assert service.list(limit=1)[0]["message"] == "bounded entry 100"
    finally:
        service.close()
        await tasks.close()


async def test_legacy_log_files_are_preserved_and_counted_without_new_writes(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    legacy = {"xiaoheihe.log": b"old log", "xiaoheihe.log.1": b"older log"}
    for name, content in legacy.items():
        (log_dir / name).write_bytes(content)
    (log_dir / "other.txt").write_bytes(b"unrelated")
    tasks = TaskManager()
    service = LoggingService(tmp_path, tasks)
    try:
        service.emit("WARNING", "new host log")
        service.reconfigure(level="ERROR", max_memory_entries=250)
        assert service.log_path == log_dir / "xiaoheihe.log"
        assert service.total_size() == sum(len(content) for content in legacy.values())
        assert {path.name: path.read_bytes() for path in log_dir.iterdir()} == {
            **legacy,
            "other.txt": b"unrelated",
        }
    finally:
        service.close()
        service.close()
        await tasks.close()


async def test_log_sse_keeps_redaction_with_public_logger_backend(tmp_path) -> None:
    tasks = TaskManager()
    service = LoggingService(tmp_path, tasks)
    stream = tasks.sse_events()
    pending = asyncio.create_task(anext(stream))
    try:
        await asyncio.sleep(0)
        entry = service.emit("INFO", "token=fixture-token", details={"nonce": "fixture-nonce"})
        event = await asyncio.wait_for(pending, timeout=1)
        assert event == {"type": "log", "entry": entry}
        assert "fixture-token" not in str(event)
        assert "fixture-nonce" not in str(event)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()
        service.close()
        await tasks.close()
