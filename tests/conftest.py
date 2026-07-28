from __future__ import annotations

import copy

import pytest

from tests.astrbot_stubs import install

install()

from xiaoheihe.config_service import DEFAULT_CONFIG  # noqa: E402
from xiaoheihe.database import Database  # noqa: E402
from xiaoheihe.repository import Repository  # noqa: E402


class FakeConfig(dict):
    def __init__(self, value=None) -> None:
        super().__init__(copy.deepcopy(value or DEFAULT_CONFIG))
        self.save_count = 0

    def save_config(self) -> None:
        self.save_count += 1


@pytest.fixture
def fake_config() -> FakeConfig:
    return FakeConfig()


@pytest.fixture
async def repository(tmp_path):
    database = Database(tmp_path / "xiaoheihe.db")
    await database.open()
    repo = Repository(database)
    yield repo
    await database.close()
