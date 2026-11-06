from __future__ import annotations

from pathlib import Path

import pytest

from tools import validate_repository


@pytest.mark.parametrize(
    "extra_source",
    [
        "import logging",
        "import logging.handlers as handlers",
        "from logging import getLogger",
        "from loguru import logger",
        "import structlog",
        "from astrbot.core import logger",
    ],
)
def test_repository_check_rejects_nonpublic_loggers(
    tmp_path: Path, monkeypatch, extra_source: str
) -> None:
    package = tmp_path / "xiaoheihe"
    package.mkdir()
    (package / "logging_service.py").write_text(
        "from astrbot.api import logger\n", encoding="utf-8"
    )
    (package / "worker.py").write_text(extra_source + "\n", encoding="utf-8")
    monkeypatch.setattr(validate_repository, "ROOT", tmp_path)

    failures = validate_repository._logging_failures()

    assert failures
    assert all("xiaoheihe/worker.py:1:" in failure for failure in failures)


def test_repository_check_requires_the_public_logger_import(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "xiaoheihe"
    package.mkdir()
    service = package / "logging_service.py"
    service.write_text("", encoding="utf-8")
    monkeypatch.setattr(validate_repository, "ROOT", tmp_path)

    assert validate_repository._logging_failures() == [
        "xiaoheihe/logging_service.py: missing public astrbot.api logger import"
    ]

    service.write_text("from astrbot.api import logger\n", encoding="utf-8")
    assert validate_repository._logging_failures() == []
