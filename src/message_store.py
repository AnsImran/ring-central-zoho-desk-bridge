"""SQLite persistence layer for the BEEtexting <-> Teams bridge.

This module provides message, thread, and bot state storage used by upcoming
Teams and Zoho Desk integration steps.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "bridge.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    timestamp   INTEGER NOT NULL,
    direction   TEXT NOT NULL,
    from_number TEXT NOT NULL,
    to_number   TEXT NOT NULL,
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    phone_number     TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    service_url      TEXT NOT NULL,
    created_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_from_ts ON messages(from_number, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_to_ts ON messages(to_number, timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_threads_conversation ON threads(conversation_id);
"""


def now_ms() -> int:
    """Return current unix time in milliseconds."""
    return int(time.time() * 1000)


def _resolve_db_path(db_path: str | Path | None = None) -> Path:
    if db_path is None:
        return DEFAULT_DB_PATH
    return Path(db_path)


@contextmanager
def _connect(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    path = _resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(db_path: str | Path | None = None) -> Path:
    """Create SQLite tables/indexes if they do not already exist."""
    path = _resolve_db_path(db_path)
    with _connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
    return path


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _validate_direction(direction: str) -> None:
    if direction not in {"inbound", "outbound"}:
        raise ValueError("direction must be 'inbound' or 'outbound'")


def upsert_message(
    message_id: str,
    timestamp: int,
    direction: str,
    from_number: str,
    to_number: str,
    text: str,
    db_path: str | Path | None = None,
) -> None:
    """Insert or update a message record by id."""
    _validate_direction(direction)
    initialize(db_path)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO messages (id, timestamp, direction, from_number, to_number, text)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                timestamp   = excluded.timestamp,
                direction   = excluded.direction,
                from_number = excluded.from_number,
                to_number   = excluded.to_number,
                text        = excluded.text
            """,
            (message_id, timestamp, direction, from_number, to_number, text),
        )


def get_message(message_id: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """Fetch one message by id."""
    initialize(db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, timestamp, direction, from_number, to_number, text
            FROM messages
            WHERE id = ?
            """,
            (message_id,),
        ).fetchone()

    return _row_to_dict(row)


def list_messages_for_phone(
    phone_number: str,
    start_timestamp: int | None = None,
    end_timestamp: int | None = None,
    limit: int = 500,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return messages where the phone number is either sender or recipient."""
    initialize(db_path)

    where_clauses = ["(from_number = ? OR to_number = ?)"]
    params: list[Any] = [phone_number, phone_number]

    if start_timestamp is not None:
        where_clauses.append("timestamp >= ?")
        params.append(start_timestamp)

    if end_timestamp is not None:
        where_clauses.append("timestamp <= ?")
        params.append(end_timestamp)

    params.append(max(limit, 1))

    sql = f"""
        SELECT id, timestamp, direction, from_number, to_number, text
        FROM messages
        WHERE {' AND '.join(where_clauses)}
        ORDER BY timestamp ASC
        LIMIT ?
    """

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [dict(row) for row in rows]


def list_messages_between_ids(
    phone_number: str,
    from_id: str,
    to_id: str,
    limit: int = 1000,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return a contiguous transcript bounded by two message ids (inclusive)."""
    initialize(db_path)

    with _connect(db_path) as conn:
        if from_id == to_id:
            boundary_row = conn.execute(
                """
                SELECT timestamp
                FROM messages
                WHERE id = ?
                  AND (from_number = ? OR to_number = ?)
                """,
                (from_id, phone_number, phone_number),
            ).fetchone()
            if boundary_row is None:
                return []
            start_timestamp = boundary_row["timestamp"]
            end_timestamp = boundary_row["timestamp"]
        else:
            boundary_rows = conn.execute(
                """
                SELECT id, timestamp
                FROM messages
                WHERE id IN (?, ?)
                  AND (from_number = ? OR to_number = ?)
                """,
                (from_id, to_id, phone_number, phone_number),
            ).fetchall()

            if len(boundary_rows) != 2:
                return []

            timestamps = {row["id"]: row["timestamp"] for row in boundary_rows}
            start_timestamp = min(timestamps[from_id], timestamps[to_id])
            end_timestamp = max(timestamps[from_id], timestamps[to_id])

        rows = conn.execute(
            """
            SELECT id, timestamp, direction, from_number, to_number, text
            FROM messages
            WHERE (from_number = ? OR to_number = ?)
              AND timestamp >= ?
              AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (
                phone_number,
                phone_number,
                start_timestamp,
                end_timestamp,
                max(limit, 1),
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def upsert_thread(
    phone_number: str,
    conversation_id: str,
    service_url: str,
    created_at: int | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Insert or update Teams thread mapping for a customer phone number."""
    initialize(db_path)

    created_at = created_at if created_at is not None else now_ms()

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO threads (phone_number, conversation_id, service_url, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(phone_number) DO UPDATE SET
                conversation_id = excluded.conversation_id,
                service_url = excluded.service_url
            """,
            (phone_number, conversation_id, service_url, created_at),
        )


def get_thread_by_phone(
    phone_number: str,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Look up Teams thread metadata by phone number."""
    initialize(db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT phone_number, conversation_id, service_url, created_at
            FROM threads
            WHERE phone_number = ?
            """,
            (phone_number,),
        ).fetchone()

    return _row_to_dict(row)


def get_phone_by_conversation(
    conversation_id: str,
    db_path: str | Path | None = None,
) -> str | None:
    """Reverse lookup: conversation id -> customer phone number."""
    initialize(db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT phone_number
            FROM threads
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()

    if row is None:
        return None
    return row["phone_number"]


def set_bot_state(
    key: str,
    value: str,
    db_path: str | Path | None = None,
) -> None:
    """Store a named bot state value (e.g., captured Teams service_url)."""
    initialize(db_path)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO bot_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value
            """,
            (key, value),
        )


def get_bot_state(
    key: str,
    db_path: str | Path | None = None,
    default: str | None = None,
) -> str | None:
    """Read bot state value by key."""
    initialize(db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT value
            FROM bot_state
            WHERE key = ?
            """,
            (key,),
        ).fetchone()

    if row is None:
        return default
    return row["value"]


def list_threads(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Return all thread mappings ordered by creation time."""
    initialize(db_path)

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT phone_number, conversation_id, service_url, created_at
            FROM threads
            ORDER BY created_at ASC
            """
        ).fetchall()

    return [dict(row) for row in rows]


if __name__ == "__main__":
    path = initialize()
    print(f"Initialized SQLite store at: {path}")
