from __future__ import annotations

import aiosqlite

from xiaoheihe.database import MIGRATIONS, Database


async def test_database_migrations_and_pragmas(tmp_path) -> None:
    database = Database(tmp_path / "adapter.db")
    await database.open()
    assert await database.schema_version() == MIGRATIONS[-1][0]
    mode = await database.fetchone("PRAGMA journal_mode")
    foreign_keys = await database.fetchone("PRAGMA foreign_keys")
    assert str(mode[0]).lower() == "wal"
    assert foreign_keys[0] == 1
    tables = await database.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    names = {row["name"] for row in tables}
    assert {
        "account_state",
        "incoming_events",
        "processed_event_keys",
        "outgoing_replies",
        "self_comment_ids",
        "session_mappings",
        "feed_candidates",
        "runtime_errors",
        "daily_counters",
    } <= names
    await database.close()


async def test_database_close_is_idempotent(tmp_path) -> None:
    database = Database(tmp_path / "adapter.db")
    await database.open()
    await database.close()
    await database.close()


async def test_database_upgrades_v2_to_v3(tmp_path) -> None:
    path = tmp_path / "upgrade.db"
    connection = await aiosqlite.connect(path)
    await connection.executescript(MIGRATIONS[0][1])
    await connection.executescript(MIGRATIONS[1][1])
    await connection.execute("INSERT INTO schema_migrations(version) VALUES (1), (2)")
    await connection.commit()
    await connection.close()

    database = Database(path)
    await database.open()
    assert await database.schema_version() == 3
    columns = await database.fetchall("PRAGMA table_info(incoming_events)")
    names = {row["name"] for row in columns}
    assert {"retry_count", "next_retry_at"} <= names
    await database.close()
