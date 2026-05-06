from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


_KIND_NOTIFIED_COMPLETED = "notified_completed"
_KIND_JAV_PROCESSED = "jav_processed"
_NOTIFIED_COMPLETED_TTL_SECONDS = 90 * 24 * 60 * 60
_JAV_PROCESSED_TTL_SECONDS = 180 * 24 * 60 * 60


@dataclass
class BotState:
    notified_completed_hashes: set[str] = field(default_factory=set)
    jav_processed_hashes: set[str] = field(default_factory=set)
    jellyfin_duplicate_codes: dict[str, int] = field(default_factory=dict)
    notified_completed_at: dict[str, int] = field(default_factory=dict, repr=False)
    jav_processed_at: dict[str, int] = field(default_factory=dict, repr=False)


class StateStore:
    def __init__(self, path: str) -> None:
        configured_path = Path(path)
        if configured_path.suffix.lower() == ".json":
            self.path = configured_path.with_suffix(".sqlite3")
            self.legacy_json_path = configured_path
        else:
            self.path = configured_path
            self.legacy_json_path = configured_path.with_suffix(".json")
        self.state = BotState()
        self._save_lock = asyncio.Lock()

    def load(self) -> BotState:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            self._migrate_legacy_json(connection)
            self._purge_expired(connection)
            self.state = self._load_from_db(connection)
        return self.state

    def save(self, state: BotState | None = None) -> None:
        current = state or self.state
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            self._sync_state(connection, current)
            self._purge_expired(connection)
            self.state = self._load_from_db(connection)

    async def save_async(self, state: BotState | None = None) -> None:
        async with self._save_lock:
            current = state or self.state
            snapshot = BotState(
                notified_completed_hashes=set(current.notified_completed_hashes),
                jav_processed_hashes=set(current.jav_processed_hashes),
                jellyfin_duplicate_codes=dict(current.jellyfin_duplicate_codes),
                notified_completed_at=dict(current.notified_completed_at),
                jav_processed_at=dict(current.jav_processed_at),
            )
            await asyncio.to_thread(self.save, snapshot)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS state_hashes (
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (kind, value)
            );
            CREATE INDEX IF NOT EXISTS idx_state_hashes_kind_created
                ON state_hashes (kind, created_at);

            CREATE TABLE IF NOT EXISTS jellyfin_duplicate_codes (
                code TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jellyfin_duplicate_expires
                ON jellyfin_duplicate_codes (expires_at);

            CREATE TABLE IF NOT EXISTS state_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    def _migrate_legacy_json(self, connection: sqlite3.Connection) -> None:
        if not self.legacy_json_path.exists():
            return
        migrated = connection.execute(
            "SELECT value FROM state_meta WHERE key = 'legacy_json_migrated'"
        ).fetchone()
        if migrated:
            return

        try:
            payload = json.loads(self.legacy_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning(
                "Skipping legacy JSON state migration from %s: %s",
                self.legacy_json_path,
                exc,
            )
            with connection:
                connection.execute(
                    "INSERT OR REPLACE INTO state_meta (key, value) VALUES (?, ?)",
                    ("legacy_json_migrated", str(int(time.time()))),
                )
            return
        legacy_state = BotState(
            notified_completed_hashes=set(payload.get("notified_completed_hashes", [])),
            jav_processed_hashes=set(payload.get("jav_processed_hashes", [])),
            jellyfin_duplicate_codes={
                str(key): int(value)
                for key, value in payload.get("jellyfin_duplicate_codes", {}).items()
            },
        )
        self._normalize_state(legacy_state)
        self._sync_state(connection, legacy_state)
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO state_meta (key, value) VALUES (?, ?)",
                ("legacy_json_migrated", str(int(time.time()))),
            )

    def _load_from_db(self, connection: sqlite3.Connection) -> BotState:
        notified_at = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT value, created_at FROM state_hashes WHERE kind = ?",
                (_KIND_NOTIFIED_COMPLETED,),
            )
        }
        jav_processed_at = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT value, created_at FROM state_hashes WHERE kind = ?",
                (_KIND_JAV_PROCESSED,),
            )
        }
        duplicate_codes = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT code, expires_at FROM jellyfin_duplicate_codes"
            )
        }
        return BotState(
            notified_completed_hashes=set(notified_at),
            jav_processed_hashes=set(jav_processed_at),
            jellyfin_duplicate_codes=duplicate_codes,
            notified_completed_at=notified_at,
            jav_processed_at=jav_processed_at,
        )

    def _sync_state(self, connection: sqlite3.Connection, state: BotState) -> None:
        self._normalize_state(state)
        with connection:
            self._sync_hash_set(
                connection,
                _KIND_NOTIFIED_COMPLETED,
                state.notified_completed_at,
            )
            self._sync_hash_set(
                connection,
                _KIND_JAV_PROCESSED,
                state.jav_processed_at,
            )
            connection.execute("DELETE FROM jellyfin_duplicate_codes")
            connection.executemany(
                """
                INSERT INTO jellyfin_duplicate_codes (code, expires_at)
                VALUES (?, ?)
                """,
                sorted(state.jellyfin_duplicate_codes.items()),
            )

    def _sync_hash_set(
        self,
        connection: sqlite3.Connection,
        kind: str,
        values: dict[str, int],
    ) -> None:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT value FROM state_hashes WHERE kind = ?",
                (kind,),
            )
        }
        current_values = set(values)
        removed = existing - current_values
        for value in removed:
            connection.execute(
                "DELETE FROM state_hashes WHERE kind = ? AND value = ?",
                (kind, value),
            )
        added = current_values - existing
        connection.executemany(
            """
            INSERT INTO state_hashes (kind, value, created_at)
            VALUES (?, ?, ?)
            """,
            ((kind, value, values[value]) for value in sorted(added)),
        )

    def _normalize_state(self, state: BotState) -> None:
        now = int(time.time())
        self._normalize_hash_timestamps(
            state.notified_completed_hashes,
            state.notified_completed_at,
            now,
            _NOTIFIED_COMPLETED_TTL_SECONDS,
        )
        self._normalize_hash_timestamps(
            state.jav_processed_hashes,
            state.jav_processed_at,
            now,
            _JAV_PROCESSED_TTL_SECONDS,
        )
        state.jellyfin_duplicate_codes = {
            code: expires_at
            for code, expires_at in state.jellyfin_duplicate_codes.items()
            if expires_at > now
        }

    def _normalize_hash_timestamps(
        self,
        values: set[str],
        timestamps: dict[str, int],
        now: int,
        ttl_seconds: int,
    ) -> None:
        for value in values:
            timestamps.setdefault(value, now)
        for value in set(timestamps) - values:
            timestamps.pop(value, None)
        expired = {
            value
            for value, created_at in timestamps.items()
            if created_at < now - ttl_seconds
        }
        values.difference_update(expired)
        for value in expired:
            timestamps.pop(value, None)

    def _purge_expired(self, connection: sqlite3.Connection) -> None:
        now = int(time.time())
        with connection:
            connection.execute(
                "DELETE FROM state_hashes WHERE kind = ? AND created_at < ?",
                (_KIND_NOTIFIED_COMPLETED, now - _NOTIFIED_COMPLETED_TTL_SECONDS),
            )
            connection.execute(
                "DELETE FROM state_hashes WHERE kind = ? AND created_at < ?",
                (_KIND_JAV_PROCESSED, now - _JAV_PROCESSED_TTL_SECONDS),
            )
            connection.execute(
                "DELETE FROM jellyfin_duplicate_codes WHERE expires_at <= ?",
                (now,),
            )
