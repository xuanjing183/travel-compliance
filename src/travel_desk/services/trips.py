"""Trip persistence and the only path that changes `trips.status` (§6.8).

`apply_transition` validates the edge with `domain.states.transition`, then
performs a guarded UPDATE (`WHERE status = ? AND revision = ?`). A concurrent
writer that already moved the trip makes the rowcount 0 and the whole
transaction rolls back, so two requests can never both apply an edge.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from travel_desk.config import Settings
from travel_desk.db.database import db_session
from travel_desk.db.repositories import decode_trip
from travel_desk.domain.slots import SLOT_FIELDS, evaluate_slots, field_problem, normalize_value
from travel_desk.domain.states import EDITABLE_STATES, TripEvent, TripStatus, transition
from travel_desk.services import audit
from travel_desk.services.access import can_view, load_employee
from travel_desk.services.errors import (
    ServiceError,
    invalid_state,
    not_found,
    require_revision,
    stale_revision,
)

# Columns cleared whenever a computed plan must not survive (abort, D-21, request_changes).
PLAN_CLEARED: dict[str, Any] = {
    "plan_json": None, "cost_json": None, "violations_json": "[]", "approval_reasons_json": "[]",
}

_UPDATABLE = frozenset({
    "slots_json", "preference_snapshot_json", "preference_source", "policy_version", "plan_json",
    "cost_json", "violations_json", "approval_reasons_json", "blocks_json", "thread_id",
    "delivery_error", "delivery_attempts",
})


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def load_trip(conn: sqlite3.Connection, trip_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    return decode_trip(row) if row is not None else None


def require_trip(conn: sqlite3.Connection, trip_id: str) -> dict[str, Any]:
    trip = load_trip(conn, trip_id)
    if trip is None:
        raise not_found(trip_id)
    return trip


def update_columns(conn: sqlite3.Connection, trip: dict[str, Any], updates: dict[str, Any]) -> None:
    """Non-status column update, guarded by the trip's current status and revision."""
    unknown = set(updates) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    assignments = ", ".join(f"{column} = ?" for column in updates)
    cursor = conn.execute(
        f"UPDATE trips SET {assignments}, updated_at = ? WHERE id = ? AND status = ? AND revision = ?",
        (*updates.values(), audit.now_ts(), trip["id"], trip["status"], trip["revision"]),
    )
    if cursor.rowcount != 1:
        raise ServiceError(409, "CONCURRENT_MODIFICATION", "差旅申请已被其他请求修改，请刷新后重试",
                           {"trip_id": trip["id"]})


def apply_transition(conn: sqlite3.Connection, trip: dict[str, Any], event: TripEvent, *, actor: str,
                     trace_id: str, updates: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply one state-machine edge, audit it as `status_changed`, and return the reloaded trip."""
    step = transition(trip["status"], trip["revision"], event)
    updates = dict(updates or {})
    unknown = set(updates) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    assignments = "".join(f", {column} = ?" for column in updates)
    cursor = conn.execute(
        f"UPDATE trips SET status = ?, revision = ?, updated_at = ?{assignments}"
        " WHERE id = ? AND status = ? AND revision = ?",
        (step.target.value, step.revision, audit.now_ts(), *updates.values(),
         trip["id"], trip["status"], trip["revision"]),
    )
    if cursor.rowcount != 1:
        raise ServiceError(409, "CONCURRENT_MODIFICATION", "差旅申请已被其他请求修改，请刷新后重试",
                           {"trip_id": trip["id"]})
    audit.record(conn, trip_id=trip["id"], revision=step.revision, actor=actor, event_type="status_changed",
                 payload={"from": step.source.value, "to": step.target.value, "event": event.value,
                          "previous_revision": trip["revision"], "revision": step.revision},
                 trace_id=trace_id)
    return require_trip(conn, trip["id"])


def visible_trip(conn: sqlite3.Connection, trip_id: str, actor: dict[str, Any]) -> tuple[dict, dict]:
    """(trip, owner) or 404 when the trip does not exist or the actor may not see it."""
    trip = load_trip(conn, trip_id)
    if trip is None:
        raise not_found(trip_id)
    owner = load_employee(conn, trip["employee_id"])
    if owner is None or not can_view(actor, owner):
        raise not_found(trip_id)
    return trip, owner


def owned_trip(conn: sqlite3.Connection, trip_id: str, actor: dict[str, Any]) -> tuple[dict, dict]:
    """Visible (else 404) and owned by the actor (else 403)."""
    trip, owner = visible_trip(conn, trip_id, actor)
    if owner["id"] != actor["id"]:
        raise ServiceError(403, "FORBIDDEN", "只有申请人本人可以执行该操作", {"owner_required": True})
    return trip, owner


# ------------------------------------------------------------------ use cases


def create_trip(settings: Settings, actor: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
    if actor["role"] not in ("employee", "manager"):
        raise ServiceError(403, "FORBIDDEN", "管理员不能创建差旅申请", {"role": actor["role"]})
    trip_id = str(uuid.uuid4())
    now = audit.now_ts()
    empty = {name: None for name in SLOT_FIELDS}
    with db_session(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO trips (id, employee_id, status, revision, slots_json, thread_id,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
            (trip_id, actor["id"], TripStatus.COLLECTING.value, dumps(empty), f"{trip_id}:rev-1", now, now),
        )
        audit.record(conn, trip_id=trip_id, revision=1, actor=actor["id"], event_type="trip_created",
                     payload={"employee_id": actor["id"]}, trace_id=trace_id)
        return require_trip(conn, trip_id)


def check_slot_changes(changes: Any) -> dict[str, Any]:
    """Format check of a `PATCH slots` body (422 INVALID_SLOT before any lookup, §1.3)."""
    if not isinstance(changes, dict) or not changes:
        raise ServiceError(422, "INVALID_SLOT", "slots 必须是非空对象", {"invalid_fields": []})
    problems = []
    for name, value in changes.items():
        if name not in SLOT_FIELDS:
            problems.append({"field": name, "code": "UNKNOWN_FIELD", "message": "未知字段"})
        elif value is not None:
            problem = field_problem(name, value)
            if problem:
                problems.append(problem)
    if problems:
        raise ServiceError(422, "INVALID_SLOT", "槽位格式不合法", {"invalid_fields": problems})
    return {name: normalize_value(name, value) for name, value in changes.items()}


def editable_states() -> list[str]:
    return sorted(s.value for s in EDITABLE_STATES)


def check_editable(trip: dict[str, Any], expected: int) -> None:
    """409 checks shared by PATCH slots, messages and image confirmation."""
    if expected != trip["revision"]:
        raise stale_revision(expected, trip["revision"])
    if TripStatus(trip["status"]) not in EDITABLE_STATES:
        raise invalid_state(trip["status"], editable_states())


def apply_slot_changes(conn: sqlite3.Connection, trip: dict[str, Any], *, actor_id: str,
                       normalized: dict[str, Any], trace_id: str, origin: str,
                       extra: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Write already-validated slot values; returns (trip, diff). No-op when nothing changes.

    Audit `slots_changed` carries before/after per field and the origin (patch, message, image).
    In POLICY_BLOCKED the first effective edit returns to COLLECTING, revision unchanged (D-09).
    """
    before = dict(trip["slots"] or {})
    after = {**{name: None for name in SLOT_FIELDS}, **before, **normalized}
    diff = [{"field": name, "before": before.get(name), "after": after[name]}
            for name in SLOT_FIELDS if before.get(name) != after[name]]
    if not diff:
        return trip, []
    update_columns(conn, trip, {"slots_json": dumps(after)})
    audit.record(conn, trip_id=trip["id"], revision=trip["revision"], actor=actor_id,
                 event_type="slots_changed", payload={"changes": diff, "origin": origin, **(extra or {})},
                 trace_id=trace_id)
    trip = require_trip(conn, trip["id"])
    if trip["status"] == TripStatus.POLICY_BLOCKED.value:
        cleared = trip["blocks"] or []
        trip = apply_transition(conn, trip, TripEvent.EDIT_AFTER_BLOCK, actor=actor_id,
                                trace_id=trace_id, updates={"blocks_json": "[]"})
        audit.record(conn, trip_id=trip["id"], revision=trip["revision"], actor=actor_id,
                     event_type="blocked_cleared",
                     payload={"cleared_blocks": [b["code"] for b in cleared]}, trace_id=trace_id)
    return trip, diff


def update_slots(settings: Settings, actor: dict[str, Any], trip_id: str, *, expected_revision: Any,
                 changes: Any, trace_id: str, origin: str = "patch") -> dict[str, Any]:
    """Apply slot edits (#7). In POLICY_BLOCKED the first effective edit returns to COLLECTING (D-09)."""
    expected = require_revision(expected_revision, "expected_revision")
    normalized = check_slot_changes(changes)
    with db_session(settings.db_path) as conn:
        trip, _ = owned_trip(conn, trip_id, actor)
        check_editable(trip, expected)
        trip, _ = apply_slot_changes(conn, trip, actor_id=actor["id"], normalized=normalized,
                                     trace_id=trace_id, origin=origin)
        return trip


def slot_state(trip: dict[str, Any]):
    return evaluate_slots(trip["slots"] or {})
