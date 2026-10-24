"""Append-only audit trail (REQ-020). Writers always share the business transaction."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from travel_desk.db.repositories import insert_audit_event


def now_ts() -> str:
    """UTC RFC 3339 with milliseconds, e.g. `2026-10-20T01:30:00.123Z`."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def record(conn: sqlite3.Connection, *, trip_id: str | None, revision: int | None, actor: str,
           event_type: str, payload: dict[str, Any], trace_id: str,
           refs: list[dict[str, Any]] | None = None) -> str:
    event_id = str(uuid.uuid4())
    insert_audit_event(conn, event_id=event_id, trip_id=trip_id, revision=revision, ts=now_ts(),
                       actor=actor, event_type=event_type, payload=payload, trace_id=trace_id, refs=refs)
    return event_id


def audit_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "seq": row["seq"], "trip_id": row["trip_id"], "revision": row["revision"],
        "ts": row["ts"], "actor": row["actor"], "type": row["type"],
        "payload": json.loads(row["payload_json"]), "refs": json.loads(row["refs_json"]),
        "trace_id": row["trace_id"],
    }


def list_events(conn: sqlite3.Connection, trip_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM audit_events WHERE trip_id = ? ORDER BY seq", (trip_id,)).fetchall()
    return [audit_view(row) for row in rows]
