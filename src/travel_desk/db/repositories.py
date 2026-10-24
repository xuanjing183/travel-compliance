"""Read/write helpers for business entities."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def list_employees(db_path: Path) -> list[dict[str, Any]]:
    with read_session(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, role, department, manager_id, home_city, preferences_json"
            " FROM employees ORDER BY CASE role WHEN 'employee' THEN 0 WHEN 'manager' THEN 1"
            " ELSE 2 END, name"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["preferences"] = json.loads(item.pop("preferences_json") or "{}")
        result.append(item)
    return result


def get_employee(db_path: Path, employee_id: str) -> dict[str, Any] | None:
    with read_session(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, role, department, manager_id, home_city, preferences_json"
            " FROM employees WHERE id = ?",
            (employee_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["preferences"] = json.loads(item.pop("preferences_json") or "{}")
    return item


def get_published_policy_version(db_path: Path) -> dict[str, Any] | None:
    with read_session(db_path) as conn:
        row = conn.execute(
            "SELECT version, status, effective_date, document_path, document_hash,"
            " created_at, published_at FROM policy_versions WHERE status = 'published'"
        ).fetchone()
    return _row_to_dict(row)


def active_faults(db_path: Path) -> frozenset[str]:
    with read_session(db_path) as conn:
        rows = conn.execute("SELECT key FROM faults WHERE enabled = 1").fetchall()
    return frozenset(row["key"] for row in rows)


def list_faults(db_path: Path) -> dict[str, bool]:
    with read_session(db_path) as conn:
        rows = conn.execute("SELECT key, enabled FROM faults ORDER BY key").fetchall()
    return {row["key"]: bool(row["enabled"]) for row in rows}


def set_fault(db_path: Path, key: str, enabled: bool, now: str) -> None:
    with db_session(db_path) as conn:
        cursor = conn.execute(
            "UPDATE faults SET enabled = ?, updated_at = ? WHERE key = ?", (int(enabled), now, key)
        )
        if cursor.rowcount != 1:
            raise KeyError(key)


def list_policy_rules(db_path: Path, version: str) -> list[dict[str, Any]]:
    with read_session(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM policy_rules WHERE version = ? ORDER BY id", (version,)
        ).fetchall()
    rules = []
    for row in rows:
        item = dict(row)
        item["condition"] = json.loads(item.pop("condition_json"))
        item["value"] = json.loads(item.pop("value_json"))
        rules.append(item)
    return rules


def upsert_preferences(db_path: Path, employee_id: str, preferences: dict[str, Any]) -> None:
    with db_session(db_path) as conn:
        conn.execute(
            "UPDATE employees SET preferences_json = ? WHERE id = ?",
            (json.dumps(preferences, ensure_ascii=False), employee_id),
        )


def clear_preferences(db_path: Path, employee_id: str) -> None:
    upsert_preferences(db_path, employee_id, {})


def insert_audit_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    trip_id: str | None,
    revision: int,
    ts: str,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    trace_id: str,
    refs: list[dict[str, Any]] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_events (id, trip_id, revision, ts, actor, type, payload_json,"
        " refs_json, trace_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            trip_id,
            revision,
            ts,
            actor,
            event_type,
            json.dumps(payload, ensure_ascii=False),
            json.dumps(refs or [], ensure_ascii=False),
            trace_id,
        ),
    )


TRIP_JSON_COLUMNS = (
    "slots_json",
    "preference_snapshot_json",
    "plan_json",
    "cost_json",
    "violations_json",
    "approval_reasons_json",
    "blocks_json",
)


def decode_trip(row: sqlite3.Row) -> dict[str, Any]:
    """Trip row with JSON columns decoded and the `_json` suffix dropped."""
    item = dict(row)
    for key in TRIP_JSON_COLUMNS:
        raw = item.pop(key, None)
        item[key.removesuffix("_json")] = json.loads(raw) if raw else None
    return item


def get_trip(db_path: Path, trip_id: str) -> dict[str, Any] | None:
    with read_session(db_path) as conn:
        row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    return decode_trip(row) if row is not None else None


def list_trips_for_user(
    settings: Settings, employee_id: str, role: str, include_reports: list[str]
) -> list[dict[str, Any]]:
    db_path = settings.db_path
    with read_session(db_path) as conn:
        if role == "admin":
            rows = conn.execute(
                "SELECT * FROM trips ORDER BY created_at DESC"
            ).fetchall()
        elif role == "manager":
            ids = [employee_id, *include_reports]
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT * FROM trips WHERE employee_id IN ({placeholders}) ORDER BY created_at DESC",
                ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trips WHERE employee_id = ? ORDER BY created_at DESC",
                (employee_id,),
            ).fetchall()
    return [dict(row) for row in rows]
