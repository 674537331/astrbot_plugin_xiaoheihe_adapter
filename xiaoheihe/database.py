from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass(frozen=True, slots=True)
class ExecuteResult:
    rowcount: int
    lastrowid: int | None


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS account_state (
            profile_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'idle',
            uid TEXT NOT NULL DEFAULT '',
            nickname TEXT NOT NULL DEFAULT '',
            login_at TEXT,
            last_login_check_at TEXT,
            last_poll_at TEXT,
            last_success_request_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            consecutive_401 INTEGER NOT NULL DEFAULT 0,
            consecutive_403 INTEGER NOT NULL DEFAULT 0,
            consecutive_429 INTEGER NOT NULL DEFAULT 0,
            consecutive_poll_failures INTEGER NOT NULL DEFAULT 0,
            circuit_open_until REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS incoming_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            external_event_id TEXT NOT NULL,
            external_comment_id TEXT NOT NULL,
            notification_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            sender_uid TEXT NOT NULL,
            sender_nickname TEXT NOT NULL DEFAULT '',
            post_id TEXT NOT NULL,
            root_comment_id TEXT NOT NULL DEFAULT '',
            parent_comment_id TEXT NOT NULL DEFAULT '',
            content TEXT,
            raw_json TEXT,
            reply_text TEXT,
            reply_hash TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            should_filter INTEGER NOT NULL DEFAULT 0,
            discovered_at REAL NOT NULL,
            claimed_at REAL,
            completed_at REAL,
            updated_at REAL NOT NULL,
            generated_ms INTEGER,
            UNIQUE(profile_id, external_event_id),
            UNIQUE(profile_id, external_comment_id)
        );

        CREATE TABLE IF NOT EXISTS processed_event_keys (
            profile_id TEXT NOT NULL,
            event_key TEXT NOT NULL,
            key_type TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(profile_id, event_key, key_type)
        );

        CREATE TABLE IF NOT EXISTS outgoing_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            incoming_event_id INTEGER,
            post_id TEXT NOT NULL,
            root_comment_id TEXT NOT NULL DEFAULT '',
            parent_comment_id TEXT NOT NULL DEFAULT '',
            external_comment_id TEXT NOT NULL DEFAULT '',
            content TEXT,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            attempted_at REAL NOT NULL,
            confirmed_at REAL,
            error TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(incoming_event_id) REFERENCES incoming_events(id)
        );

        CREATE TABLE IF NOT EXISTS self_comment_ids (
            profile_id TEXT NOT NULL,
            external_comment_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            root_comment_id TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            PRIMARY KEY(profile_id, external_comment_id)
        );

        CREATE TABLE IF NOT EXISTS session_mappings (
            profile_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            root_comment_id TEXT NOT NULL DEFAULT '',
            last_used_at REAL NOT NULL,
            PRIMARY KEY(profile_id, session_id)
        );

        CREATE TABLE IF NOT EXISTS feed_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            post_title TEXT NOT NULL DEFAULT '',
            post_author_uid TEXT NOT NULL DEFAULT '',
            generated_text TEXT NOT NULL,
            edited_text TEXT,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            reviewed_at REAL,
            sent_comment_id TEXT NOT NULL DEFAULT '',
            UNIQUE(profile_id, post_id)
        );

        CREATE TABLE IF NOT EXISTS runtime_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            resolved_at REAL
        );

        CREATE TABLE IF NOT EXISTS daily_counters (
            profile_id TEXT NOT NULL,
            day TEXT NOT NULL,
            reply_count INTEGER NOT NULL DEFAULT 0,
            proactive_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(profile_id, day)
        );
        """,
    ),
    (
        2,
        """
        CREATE INDEX IF NOT EXISTS idx_incoming_status_updated
            ON incoming_events(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_incoming_profile_time
            ON incoming_events(profile_id, discovered_at DESC);
        CREATE INDEX IF NOT EXISTS idx_incoming_sender
            ON incoming_events(profile_id, sender_uid, discovered_at DESC);
        CREATE INDEX IF NOT EXISTS idx_incoming_post
            ON incoming_events(profile_id, post_id, discovered_at DESC);
        CREATE INDEX IF NOT EXISTS idx_processed_created
            ON processed_event_keys(created_at);
        CREATE INDEX IF NOT EXISTS idx_outgoing_status_time
            ON outgoing_replies(status, attempted_at);
        CREATE INDEX IF NOT EXISTS idx_outgoing_target_hash
            ON outgoing_replies(profile_id, post_id, root_comment_id, content_hash);
        CREATE INDEX IF NOT EXISTS idx_session_last_used
            ON session_mappings(last_used_at);
        CREATE INDEX IF NOT EXISTS idx_feed_status_time
            ON feed_candidates(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_errors_created
            ON runtime_errors(created_at);
        """,
    ),
    (
        3,
        """
        ALTER TABLE incoming_events
            ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE incoming_events
            ADD COLUMN next_retry_at REAL;
        CREATE INDEX IF NOT EXISTS idx_incoming_retry
            ON incoming_events(status, next_retry_at, updated_at);
        """,
    ),
)

MIGRATION_MARKERS = {
    1: "INSERT INTO schema_migrations(version) VALUES (1);",
    2: "INSERT INTO schema_migrations(version) VALUES (2);",
    3: "INSERT INTO schema_migrations(version) VALUES (3);",
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        self._closed = False

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("数据库尚未打开")
        return self._connection

    async def open(self) -> None:
        if self._connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")
        await self._connection.execute("PRAGMA busy_timeout=5000")
        await self._connection.execute("PRAGMA synchronous=NORMAL")
        await self._connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        await self._connection.commit()
        await self.migrate()

    async def migrate(self) -> None:
        connection = self.connection
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await connection.commit()
        cursor = await connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        )
        row = await cursor.fetchone()
        current = int(row["version"]) if row else 0
        for version, script in MIGRATIONS:
            if version <= current:
                continue
            async with self._write_lock:
                wrapped = "\n".join(
                    (
                        "BEGIN IMMEDIATE;",
                        script,
                        MIGRATION_MARKERS[version],
                        "COMMIT;",
                    )
                )
                try:
                    await connection.executescript(wrapped)
                except BaseException:
                    with suppress(aiosqlite.OperationalError):
                        await connection.execute("ROLLBACK")
                    raise

    async def schema_version(self) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        )
        return int(row["version"]) if row else 0

    async def execute(self, sql: str, parameters: Iterable[Any] = ()) -> ExecuteResult:
        async with self._write_lock:
            cursor = await self.connection.execute(sql, tuple(parameters))
            try:
                await self.connection.commit()
                return ExecuteResult(
                    rowcount=cursor.rowcount,
                    lastrowid=cursor.lastrowid,
                )
            finally:
                await cursor.close()

    async def executemany(
        self,
        sql: str,
        parameters: Iterable[Iterable[Any]],
    ) -> ExecuteResult:
        async with self._write_lock:
            cursor = await self.connection.executemany(sql, [tuple(items) for items in parameters])
            try:
                await self.connection.commit()
                return ExecuteResult(
                    rowcount=cursor.rowcount,
                    lastrowid=cursor.lastrowid,
                )
            finally:
                await cursor.close()

    async def fetchone(self, sql: str, parameters: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.connection.execute(sql, tuple(parameters)) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, sql: str, parameters: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.connection.execute(sql, tuple(parameters)) as cursor:
            return list(await cursor.fetchall())

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._write_lock:
            connection = self.connection
            await connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def checkpoint(self) -> None:
        async with self._write_lock:
            async with self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)") as cursor:
                await cursor.fetchall()

    async def incremental_vacuum(self, pages: int = 200) -> None:
        safe_pages = max(1, min(int(pages), 5000))
        async with self._write_lock:
            async with self.connection.execute(
                f"PRAGMA incremental_vacuum({safe_pages})"
            ) as cursor:
                await cursor.fetchall()
            await self.connection.commit()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        connection = self._connection
        self._connection = None
        if connection is not None:
            await connection.close()
