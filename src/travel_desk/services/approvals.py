"""Approval cards, decisions and workflow notifications (§4.6, §4.7, REQ-015/016/018)."""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from travel_desk.config import Settings
from travel_desk.db.database import db_session
from travel_desk.domain.states import TripEvent, TripStatus
from travel_desk.services import audit, idempotency
from travel_desk.services.access import load_employee
from travel_desk.services.errors import ServiceError, invalid_state, stale_revision
from travel_desk.services.idempotency import IdemRequest
from travel_desk.services.trips import PLAN_CLEARED, apply_transition, dumps, require_trip
from travel_desk.services.views import approval_view, trip_view

DECISIONS = ("approve", "reject", "request_changes")
DECISION_LABELS = {"approve": "批准", "reject": "驳回", "request_changes": "要求修改"}
_EVENTS = {"approve": TripEvent.APPROVE, "reject": TripEvent.REJECT,
           "request_changes": TripEvent.REQUEST_CHANGES}


def _leg_brief(leg: dict[str, Any] | None) -> dict[str, Any] | None:
    if leg is None:
        return None
    keys = ("id", "service_no", "mode", "cabin", "origin", "destination", "depart_at", "arrive_at",
            "price_fen", "source")
    return {key: leg.get(key) for key in keys}


def plan_sources(plan: dict[str, Any]) -> list[str]:
    sources = {"policy"}
    for leg in (plan["transport"]["outbound"], plan["transport"]["return"]):
        if leg is not None:
            sources.add(leg["source"])
    if plan["hotel"]["candidate"] is not None:
        sources.add(plan["hotel"]["candidate"]["source"])
    sources.update(day["source"] for day in plan["weather"]["days"])
    if (plan.get("summary") or {}).get("status") == "ok":
        sources.add("model")
    return sorted(sources)


def build_card(trip: dict[str, Any], owner: dict[str, Any]) -> dict[str, Any]:
    """Snapshot taken when the approval is created (AC-008)."""
    plan, cost = trip["plan"], trip["cost"]
    hotel = plan["hotel"]["candidate"]
    summary = plan.get("summary") or {}
    return {
        "employee": {"id": owner["id"], "name": owner["name"], "department": owner["department"]},
        "revision": trip["revision"],
        "slots": trip["slots"], "policy_version": trip["policy_version"],
        "cost_breakdown": cost, "budget_delta_fen": cost["budget_delta_fen"],
        "violations": trip["violations"] or [], "approval_reasons": trip["approval_reasons"] or [],
        "sources": plan_sources(plan),
        "plan_summary": {
            "incomplete": plan["incomplete"], "missing": plan["missing"],
            "outbound": _leg_brief(plan["transport"]["outbound"]),
            "return": _leg_brief(plan["transport"]["return"]),
            "exception_legs": plan["transport"]["exception_legs"],
            "transport_note": plan["transport"]["note"],
            "hotel_status": plan["hotel"]["status"], "nights": plan["hotel"]["nights"],
            "hotel": None if hotel is None else {key: hotel[key] for key in
                                                 ("id", "name", "city", "price_fen", "kind", "source")},
            "hotel_exception": plan["hotel"]["exception"], "hotel_note": plan["hotel"]["note"],
            "summary": summary.get("text"), "summary_generator": summary.get("generator"),
        },
    }


def pending_approval(conn: sqlite3.Connection, trip_id: str, revision: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM approvals WHERE trip_id = ? AND revision = ? AND status = 'pending'",
                        (trip_id, revision)).fetchone()


def create_approval(conn: sqlite3.Connection, trip: dict[str, Any], owner: dict[str, Any], *,
                    trace_id: str) -> tuple[dict[str, Any], sqlite3.Row]:
    """PLANNING → PENDING_APPROVAL plus one pending card. Re-running it is a no-op (RISK-003)."""
    if trip["status"] == TripStatus.PENDING_APPROVAL.value:
        existing = pending_approval(conn, trip["id"], trip["revision"])
        if existing is not None:
            return trip, existing
    approver_id = owner["manager_id"]
    if approver_id is None:
        raise ValueError("D-21: no direct manager; the workflow must take the no_approver branch")
    trip = apply_transition(conn, trip, TripEvent.REQUIRE_APPROVAL, actor="system", trace_id=trace_id)
    approval_id = str(uuid.uuid4())
    card = build_card(trip, owner)
    conn.execute("INSERT INTO approvals (id, trip_id, revision, approver_id, status, card_json, created_at)"
                 " VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                 (approval_id, trip["id"], trip["revision"], approver_id, dumps(card), audit.now_ts()))
    audit.record(conn, trip_id=trip["id"], revision=trip["revision"], actor="system",
                 event_type="approval_requested",
                 payload={"approval_id": approval_id, "approver_id": approver_id,
                          "approval_reasons": [r["code"] for r in trip["approval_reasons"] or []],
                          "violations": [v["code"] for v in trip["violations"] or []],
                          "sources": card["sources"]},
                 trace_id=trace_id)
    row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    return trip, row


def check_decision_allowed(conn: sqlite3.Connection, trip: dict[str, Any], revision: int) -> sqlite3.Row:
    """409 checks shared by the pre-validation and the decision transaction."""
    if revision != trip["revision"]:
        raise stale_revision(revision, trip["revision"])
    pending = pending_approval(conn, trip["id"], revision)
    if pending is None:
        decided = conn.execute("SELECT id, decision FROM approvals WHERE trip_id = ? AND revision = ?"
                               " AND status = 'decided'", (trip["id"], revision)).fetchone()
        if decided is not None:
            raise ServiceError(409, "ALREADY_DECIDED", "该 revision 的审批已处理",
                               {"approval_id": decided["id"], "decision": decided["decision"],
                                "current_revision": trip["revision"], "status": trip["status"]})
        raise invalid_state(trip["status"], [TripStatus.PENDING_APPROVAL.value])
    if trip["status"] != TripStatus.PENDING_APPROVAL.value:
        raise invalid_state(trip["status"], [TripStatus.PENDING_APPROVAL.value])
    return pending


def _notification(trip: dict[str, Any], owner: dict[str, Any], approver: dict[str, Any], decision: str,
                  comment: str) -> tuple[str, str]:
    slots = trip["slots"]
    label = DECISION_LABELS[decision]
    route = f"{slots['origin']}→{slots['destination']}，{slots['start_date']}～{slots['end_date']}"
    subject = f"[差旅审批] 您的申请被{label}（revision {trip['revision']}）"
    next_step = ("申请已终止，如需出行请重新发起。" if decision == "reject"
                 else f"请按意见修改后重新提交（新 revision 为 {trip['revision'] + 1}）。")
    body = "\n".join([
        f"{owner['name']}，您好：",
        "",
        f"您的差旅申请（{route}，事由：{slots['purpose']}）审批结果：{label}。",
        f"审批人：{approver['name']}",
        f"审批意见：{comment}",
        f"对应 revision：{trip['revision']}，制度版本：{trip['policy_version']}",
        "",
        next_step,
        "",
        "本邮件为审批流程通知，不是出行确认，不包含可用的行程单。",
    ])
    return subject, body


def write_notification(conn: sqlite3.Connection, trip: dict[str, Any], owner: dict[str, Any],
                       approver: dict[str, Any], decision: str, comment: str, *, trace_id: str) -> None:
    """`workflow_notification` for reject / request_changes, at the decided revision (D-03)."""
    kind = "workflow_notification"
    unique_key = f"{trip['id']}:{trip['revision']}:{kind}"
    subject, body = _notification(trip, owner, approver, decision, comment)
    email_id = str(uuid.uuid4())
    cursor = conn.execute(
        "INSERT OR IGNORE INTO outbox_emails (id, trip_id, revision, kind, to_json, cc_json, subject, body,"
        " created_at, unique_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (email_id, trip["id"], trip["revision"], kind, dumps([owner["id"]]), dumps([approver["id"]]),
         subject, body, audit.now_ts(), unique_key))
    if cursor.rowcount == 1:
        audit.record(conn, trip_id=trip["id"], revision=trip["revision"], actor="system",
                     event_type="notification_written",
                     payload={"email_id": email_id, "kind": kind, "unique_key": unique_key,
                              "decision": decision, "to": [owner["id"]], "cc": [approver["id"]]},
                     trace_id=trace_id)


def apply_decision(settings: Settings, *, trip_id: str, revision: int, approver_id: str, decision: str,
                   comment: str | None, trace_id: str, via: str,
                   idem: IdemRequest | None) -> dict[str, Any]:
    """One transaction: close the card, move the trip, notify, and store the idempotency key."""
    with db_session(settings.db_path) as conn:
        trip = require_trip(conn, trip_id)
        approval = check_decision_allowed(conn, trip, revision)
        if approval["approver_id"] != approver_id:
            raise ServiceError(403, "NOT_DIRECT_MANAGER", "只有直属经理可以审批",
                               {"approver_id": approver_id})
        decided_at = audit.now_ts()
        # request_changes invalidates the card instead of closing it: a new revision gets a new card,
        # so the old one is "superseded", the same status a revalidation leaves behind (IMP-95).
        closing = "superseded" if decision == "request_changes" else "decided"
        cursor = conn.execute(
            f"UPDATE approvals SET status = '{closing}', decision = ?, comment = ?, idempotency_key = ?,"
            " decided_at = ? WHERE id = ? AND status = 'pending'",
            (decision, comment, idem.key if idem else None, decided_at, approval["id"]))
        if cursor.rowcount != 1:
            raise ServiceError(409, "ALREADY_DECIDED", "该 revision 的审批已处理",
                               {"approval_id": approval["id"]})
        owner = load_employee(conn, trip["employee_id"])
        approver = load_employee(conn, approver_id)
        audit.record(conn, trip_id=trip_id, revision=revision, actor=approver_id,
                     event_type="decision_recorded",
                     payload={"approval_id": approval["id"], "decision": decision, "comment": comment,
                              "via": via}, trace_id=trace_id)
        if decision != "approve":
            write_notification(conn, trip, owner, approver, decision, comment or "", trace_id=trace_id)
        updates = dict(PLAN_CLEARED) if decision == "request_changes" else {}
        after = apply_transition(conn, trip, _EVENTS[decision], actor=approver_id, trace_id=trace_id,
                                 updates=updates)
        if decision == "request_changes":
            # S-08: say in the audit which card stopped being current, and why (IMP-95).
            audit.record(conn, trip_id=trip_id, revision=after["revision"], actor=approver_id,
                         event_type="approval_superseded",
                         payload={"approval_id": approval["id"], "superseded_revision": revision,
                                  "reason": "request_changes"}, trace_id=trace_id)
        if idem is not None:
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval["id"],)).fetchone()
            idempotency.store(conn, idem, 200,
                              {"trip": trip_view(conn, after), "approval": approval_view(conn, row)})
        return {"status": after["status"], "revision": after["revision"], "approval_id": approval["id"]}
