"""JSON views of stored entities (docs/api.md §2.4–§2.7)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from travel_desk.domain.slots import evaluate_slots
from travel_desk.domain.states import TripStatus


def itinerary_url(trip_id: str, revision: int) -> str:
    return f"/api/trips/{trip_id}/itinerary?revision={revision}"


def approval_view(conn: sqlite3.Connection | None, row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = {
        "id": row["id"], "trip_id": row["trip_id"], "revision": row["revision"],
        "approver_id": row["approver_id"], "status": row["status"], "decision": row["decision"],
        "comment": row["comment"], "created_at": row["created_at"], "decided_at": row["decided_at"],
        "idempotency_key": row["idempotency_key"], "card": json.loads(row["card_json"]),
    }
    # Look up current trip status if connection is provided
    if conn is not None:
        trip_row = conn.execute("SELECT status FROM trips WHERE id = ?", (row["trip_id"],)).fetchone()
        result["trip_status"] = trip_row["status"] if trip_row else None
    return result


def current_approval_row(conn: sqlite3.Connection, trip_id: str, revision: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM approvals WHERE trip_id = ? AND revision = ?"
                        " ORDER BY created_at DESC, rowid DESC LIMIT 1", (trip_id, revision)).fetchone()


def email_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "trip_id": row["trip_id"], "revision": row["revision"], "kind": row["kind"],
        "to": json.loads(row["to_json"]), "cc": json.loads(row["cc_json"]), "subject": row["subject"],
        "body": row["body"], "created_at": row["created_at"], "unique_key": row["unique_key"],
    }


def trip_view(conn: sqlite3.Connection, trip: dict[str, Any]) -> dict[str, Any]:
    state = evaluate_slots(trip["slots"] or {})
    published = conn.execute("SELECT version FROM policy_versions WHERE status = 'published'").fetchone()
    notice = None
    if (trip["status"] == TripStatus.PENDING_APPROVAL.value and published is not None
            and trip["policy_version"] and published["version"] != trip["policy_version"]):
        notice = {"bound_version": trip["policy_version"], "published_version": published["version"]}
    has_itinerary = conn.execute("SELECT 1 FROM itineraries WHERE trip_id = ? AND revision = ?",
                                 (trip["id"], trip["revision"])).fetchone() is not None
    return {
        "id": trip["id"], "employee_id": trip["employee_id"], "status": trip["status"],
        "revision": trip["revision"], "slots": state.values, "missing_fields": state.missing_fields,
        "invalid_fields": state.invalid_fields, "policy_version": trip["policy_version"],
        "preference_source": trip["preference_source"], "preferences_used": trip["preference_snapshot"] or {},
        "plan": trip["plan"], "cost_breakdown": trip["cost"],
        "violations": trip["violations"] or [], "approval_reasons": trip["approval_reasons"] or [],
        "blocks": trip["blocks"] or [],
        "current_approval": approval_view(conn, current_approval_row(conn, trip["id"], trip["revision"])),
        "policy_change_notice": notice,
        "delivery": {"error": trip["delivery_error"], "attempts": trip["delivery_attempts"],
                     "itinerary_url": itinerary_url(trip["id"], trip["revision"]) if has_itinerary else None},
        "thread_id": trip["thread_id"], "created_at": trip["created_at"], "updated_at": trip["updated_at"],
    }


def list_trip_summaries(conn: sqlite3.Connection, employee_ids: list[str] | None) -> list[dict[str, Any]]:
    """Trip list rows (docs/api.md §2.4). `employee_ids=None` means every trip (admin)."""
    sql = ("SELECT t.*, e.name AS employee_name FROM trips t JOIN employees e ON e.id = t.employee_id")
    params: list[Any] = []
    if employee_ids is not None:
        if not employee_ids:
            return []
        sql += f" WHERE t.employee_id IN ({','.join('?' for _ in employee_ids)})"
        params = list(employee_ids)
    rows = conn.execute(sql + " ORDER BY t.updated_at DESC, t.rowid DESC", params).fetchall()
    result = []
    for row in rows:
        slots = json.loads(row["slots_json"] or "{}")
        cost = json.loads(row["cost_json"]) if row["cost_json"] else {}
        result.append({
            "id": row["id"], "employee_id": row["employee_id"], "employee_name": row["employee_name"],
            "status": row["status"], "revision": row["revision"], "origin": slots.get("origin"),
            "destination": slots.get("destination"), "start_date": slots.get("start_date"),
            "end_date": slots.get("end_date"), "total_fen": cost.get("total_fen"),
            "known_subtotal_fen": cost.get("known_subtotal_fen"), "policy_version": row["policy_version"],
            "updated_at": row["updated_at"],
        })
    return result


def list_outbox(conn: sqlite3.Connection, *, trip_id: str | None = None,
                kind: str | None = None) -> list[dict[str, Any]]:
    clauses, params = [], []
    if trip_id is not None:
        clauses.append("trip_id = ?")
        params.append(trip_id)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM outbox_emails{where} ORDER BY created_at, rowid", params).fetchall()
    return [email_view(row) for row in rows]
