"""Connection helpers and database initialization.

Business data uses short-lived synchronous connections (SQLite is local and the
write volume is small); LangGraph checkpoints live in a separate database file
and are accessed asynchronously by `AsyncSqliteSaver` (see workflow/persistence).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from travel_desk.config import Settings
from travel_desk.db.schema import SCHEMA_SQL, SCHEMA_VERSION
from travel_desk.db.seed import reset_demo_data, seed_if_empty


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def db_session(path: Path) -> Iterator[sqlite3.Connection]:
    """Transactional write session: commit on success, roll back on error."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def read_session(path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


class SchemaMismatchError(RuntimeError):
    """The database file was created by a different schema version."""


def initialize_database(settings: Settings) -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    with db_session(settings.db_path) as conn:
        found = conn.execute("PRAGMA user_version").fetchone()[0]
        if found not in (0, SCHEMA_VERSION):
            # No silent migration: refuse to run against an incompatible file.
            raise SchemaMismatchError(
                f"{settings.db_path.name} has schema version {found}, expected {SCHEMA_VERSION};"
                " delete the local data directory or point TRAVEL_DESK_DATA_DIR elsewhere"
            )
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        seed_if_empty(conn, settings)


def reset_database(settings: Settings) -> None:
    """Admin demo reset: drop dynamic data and restore the seed state."""
    with db_session(settings.db_path) as conn:
        reset_demo_data(conn, settings)
