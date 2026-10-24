"""Idempotent delivery: itinerary + `trip_delivery` email (§4.7, HC-04, REQ-018).

One function serves the workflow `deliver` node, the admin retry endpoint and
the startup auto-retry. It runs in two transactions:

1. `APPROVED`/`AUTO_APPROVED` → `DELIVERY_PENDING` (already pending: continue; delivered: no-op).
2. Write the itinerary and the email (`INSERT OR IGNORE` on `(trip_id, revision)` and
   `unique_key = <trip_id>:<revision>:trip_delivery`), then `DELIVERY_PENDING → DELIVERED`.

If step 2 fails, nothing from it is kept; a third transaction records the
error and the attempt, and the trip stays `DELIVERY_PENDING` for a retry.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from travel_desk.config import Settings
from travel_desk.db.database import db_session
from travel_desk.domain.dates import to_local
from travel_desk.domain.money import format_yuan
from travel_desk.domain.states import TripEvent, TripStatus
from travel_desk.services import audit
from travel_desk.services.access import load_employee
from travel_desk.services.errors import invalid_state
from travel_desk.services.trips import apply_transition, dumps, require_trip, update_columns
from travel_desk.services.views import itinerary_url

logger = logging.getLogger("travel_desk.delivery")

# Test-only hook (§4.7: delivery-write faults are injected by automated tests, never by the fault panel).
DeliveryFault = Callable[[str, int], None]

MANUAL = "待人工补录"


@dataclass
class DeliveryResult:
    status: str
    revision: int
    transitions: list[tuple[str, str]] = field(default_factory=list)  # (previous, new)
    error: str | None = None


def _hhmm(iso: str) -> str:
    return to_local(datetime.fromisoformat(iso)).strftime("%m-%d %H:%M")


def _leg_line(label: str, leg: dict[str, Any] | None) -> str:
    if leg is None:
        return f"  {label}：{MANUAL}"
    return (f"  {label}：{leg.get('service_no') or leg['id']} {leg['origin']}→{leg['destination']}，"
            f"{_hhmm(leg['depart_at'])} 出发，{format_yuan(leg['price_fen'])}（来源 {leg['source']}）")


def _approval_line(approval: sqlite3.Row | None, approver: dict[str, Any] | None) -> str:
    if approval is None:
        return "审批：满足自动通过条件，系统自动通过"
    comment = f"，意见：{approval['comment']}" if approval["comment"] else ""
    return f"审批：{approver['name'] if approver else approval['approver_id']} 批准{comment}"


def _summary_block(plan: dict[str, Any]) -> list[str]:
    summary = plan.get("summary") or {}
    if summary.get("status") != "ok":
        return ["行程摘要：暂不可用（不影响上方行程与金额）。"]
    label = ("Fake 模型生成（确定性测试替身）" if summary.get("generator") == "fake" else "模型生成")
    return [f"行程摘要（{label}，仅供参考，安排与金额以上文为准）：", summary["text"]]


def build_delivery_email(trip: dict[str, Any], owner: dict[str, Any], approval: sqlite3.Row | None,
                         approver: dict[str, Any] | None) -> tuple[str, str]:
    slots, plan, cost = trip["slots"], trip["plan"], trip["cost"]
    hotel_plan = plan["hotel"]
    if hotel_plan["status"] == "not_needed":
        hotel_line = "  住宿：当天往返，无需住宿"
    elif hotel_plan["candidate"] is None:
        hotel_line = f"  住宿：{MANUAL}"
    else:
        hotel = hotel_plan["candidate"]
        hotel_line = (f"  住宿：{hotel['name']}，{hotel_plan['nights']} 晚"
                      f" × {format_yuan(hotel['price_fen'])}"
                      f" = {format_yuan(cost['hotel_fen'])}（来源 {hotel['source']}）")
    total = (format_yuan(cost["total_fen"]) if cost["total_fen"] is not None
             else f"待补录（已知部分 {format_yuan(cost['known_subtotal_fen'])}）")
    subject = (f"[差旅行程单] {slots['origin']}→{slots['destination']} "
               f"{slots['start_date']}～{slots['end_date']}（revision {trip['revision']}）")
    lines = [
        f"{owner['name']}，您好：",
        "",
        f"您的差旅申请（{slots['origin']}→{slots['destination']}，{slots['start_date']}～{slots['end_date']}，"
        f"事由：{slots['purpose']}）已{'自动通过' if approval is None else '批准'}。"
        f"revision {trip['revision']}，制度版本 {trip['policy_version']}。",
        "",
        f"行程单：{itinerary_url(trip['id'], trip['revision'])}",
        "",
        "交通：",
        _leg_line("去程", plan["transport"]["outbound"]),
        _leg_line("返程", plan["transport"]["return"]),
        hotel_line,
        f"  伙食补贴：{cost['days']} 天 × {format_yuan(cost['meal_per_day_fen'])}"
        f" = {format_yuan(cost['meal_fen'])}",
        f"合计：{total}；申报预算：{format_yuan(cost['declared_budget_fen'])}",
    ]
    if plan["missing"]:
        lines.append("注意：标为“待人工补录”的部分没有可用候选，请由行政人工补录，不要按本邮件预订。")
    lines += ["", _approval_line(approval, approver), "", *_summary_block(plan)]
    return subject, "\n".join(lines)


def itinerary_content(trip: dict[str, Any], owner: dict[str, Any], approval: sqlite3.Row | None) -> dict:
    return {
        "trip_id": trip["id"], "revision": trip["revision"], "policy_version": trip["policy_version"],
        "employee": {"id": owner["id"], "name": owner["name"], "department": owner["department"]},
        "slots": trip["slots"], "plan": trip["plan"], "cost_breakdown": trip["cost"],
        "approval": ({"mode": "auto"} if approval is None else
                     {"mode": "manager", "approval_id": approval["id"],
                      "approver_id": approval["approver_id"],
                      "comment": approval["comment"], "decided_at": approval["decided_at"]}),
    }


def _approved_row(conn: sqlite3.Connection, trip_id: str, revision: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM approvals WHERE trip_id = ? AND revision = ? AND status = 'decided'"
                        " AND decision = 'approve'", (trip_id, revision)).fetchone()


def deliver(settings: Settings, trip_id: str, *, actor: str, trace_id: str,
            fault_hook: DeliveryFault | None = None) -> DeliveryResult:
    # ---- transaction 1: enter DELIVERY_PENDING
    with db_session(settings.db_path) as conn:
        trip = require_trip(conn, trip_id)
        result = DeliveryResult(status=trip["status"], revision=trip["revision"])
        if trip["status"] == TripStatus.DELIVERED.value:
            return result
        if trip["status"] in (TripStatus.APPROVED.value, TripStatus.AUTO_APPROVED.value):
            previous = trip["status"]
            trip = apply_transition(conn, trip, TripEvent.START_DELIVERY, actor=actor, trace_id=trace_id)
            result.transitions.append((previous, trip["status"]))
        elif trip["status"] != TripStatus.DELIVERY_PENDING.value:
            raise invalid_state(trip["status"], [TripStatus.APPROVED.value, TripStatus.AUTO_APPROVED.value,
                                                 TripStatus.DELIVERY_PENDING.value])
    revision = trip["revision"]
    attempt = trip["delivery_attempts"] + 1

    # ---- transaction 2: itinerary + email + DELIVERED, all or nothing
    try:
        with db_session(settings.db_path) as conn:
            trip = require_trip(conn, trip_id)
            if trip["status"] != TripStatus.DELIVERY_PENDING.value or trip["revision"] != revision:
                result.status, result.revision = trip["status"], trip["revision"]
                return result
            owner = load_employee(conn, trip["employee_id"])
            approval = _approved_row(conn, trip_id, revision)
            approver = load_employee(conn, approval["approver_id"]) if approval is not None else None
            now = audit.now_ts()
            conn.execute("INSERT OR IGNORE INTO itineraries (trip_id, revision, content_json, created_at)"
                         " VALUES (?, ?, ?, ?)",
                         (trip_id, revision, dumps(itinerary_content(trip, owner, approval)), now))
            kind = "trip_delivery"
            unique_key = f"{trip_id}:{revision}:{kind}"
            subject, body = build_delivery_email(trip, owner, approval, approver)
            cc = [owner["manager_id"]] if owner["manager_id"] else []
            email_id = str(uuid.uuid4())
            inserted = conn.execute(
                "INSERT OR IGNORE INTO outbox_emails (id, trip_id, revision, kind, to_json, cc_json, subject,"
                " body, created_at, unique_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (email_id, trip_id, revision, kind, dumps([owner["id"]]), dumps(cc), subject, body, now,
                 unique_key)).rowcount == 1
            if fault_hook is not None:
                fault_hook(trip_id, revision)
            trip = apply_transition(conn, trip, TripEvent.DELIVERED, actor=actor, trace_id=trace_id,
                                    updates={"delivery_error": None, "delivery_attempts": attempt})
            audit.record(conn, trip_id=trip_id, revision=revision, actor=actor, event_type="delivery_written",
                         payload={"email_id": email_id if inserted else None, "unique_key": unique_key,
                                  "email_inserted": inserted, "to": [owner["id"]], "cc": cc,
                                  "itinerary_url": itinerary_url(trip_id, revision), "attempt": attempt},
                         trace_id=trace_id)
        result.transitions.append((TripStatus.DELIVERY_PENDING.value, TripStatus.DELIVERED.value))
        result.status = TripStatus.DELIVERED.value
        return result
    except Exception as exc:  # noqa: BLE001 - every failure must leave a retryable DELIVERY_PENDING
        error_type = type(exc).__name__
        error = f"{error_type}：交付写入失败，可由管理员重试"
        logger.warning("delivery failed trip=%s revision=%s attempt=%s type=%s trace_id=%s",
                       trip_id, revision, attempt, error_type, trace_id)

    # ---- transaction 3: record the failure (only the error type, never exception text)
    with db_session(settings.db_path) as conn:
        trip = require_trip(conn, trip_id)
        if trip["status"] == TripStatus.DELIVERY_PENDING.value and trip["revision"] == revision:
            update_columns(conn, trip, {"delivery_error": error, "delivery_attempts": attempt})
            audit.record(conn, trip_id=trip_id, revision=revision, actor=actor, event_type="delivery_failed",
                         payload={"attempt": attempt, "error_type": error_type, "message": error},
                         trace_id=trace_id)
        result.status, result.revision, result.error = trip["status"], trip["revision"], error
    return result
