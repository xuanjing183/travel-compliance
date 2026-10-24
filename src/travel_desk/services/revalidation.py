"""Re-validation against the currently published policy version (#14, D-12, REQ-019).

D-12: reuse the candidates already selected for the current revision, recompute the
cost and re-judge with the *published* rules; no provider is called. The old card
is superseded, the revision goes up by one and the trip is bound to the new version.
Both judgements stay in the audit trail (`revalidated` carries before and after).

Outcomes (owner decisions IMP-04/IMP-05):
- still needs approval → PENDING_APPROVAL at the new revision with a new card;
- no longer needs approval → AUTO_APPROVED, then the normal delivery;
- a hard block → POLICY_BLOCKED at the new revision (plan cleared, kept in the audit).

Everything up to the final state is one transaction; delivery (AUTO_APPROVED) runs
afterwards through the shared idempotent `deliver()`.
"""
from __future__ import annotations

import secrets
import sqlite3
from typing import Any

from travel_desk.db.repositories import list_policy_rules
from travel_desk.domain.models import HotelCandidate, TransportCandidate
from travel_desk.domain.policy import RuleSet
from travel_desk.domain.rules import PlanChoice, check_blocks, evaluate_trip
from travel_desk.domain.states import TripEvent, TripStatus
from travel_desk.services import audit
from travel_desk.services.approvals import create_approval, pending_approval
from travel_desk.services.errors import ServiceError, invalid_state, stale_revision
from travel_desk.services.trips import (
    PLAN_CLEARED,
    apply_transition,
    dumps,
    require_trip,
    slot_state,
    update_columns,
)


def check_actor(actor: dict[str, Any], owner: dict[str, Any]) -> None:
    """§6.4: the applicant, or the applicant's direct manager. Admin cannot revalidate."""
    if actor["id"] == owner["id"] and actor["role"] in ("employee", "manager"):
        return
    if actor["role"] == "manager" and owner.get("manager_id") == actor["id"]:
        return
    raise ServiceError(403, "FORBIDDEN", "只有申请人本人或其直属经理可以按新版本重新校验",
                       {"allowed": ["owner", "direct_manager"]})


def check_revalidatable(trip: dict[str, Any], revision: int) -> None:
    if revision != trip["revision"]:
        raise stale_revision(revision, trip["revision"])
    if trip["status"] != TripStatus.PENDING_APPROVAL.value:
        raise invalid_state(trip["status"], [TripStatus.PENDING_APPROVAL.value])


def _published_rules(conn: sqlite3.Connection, settings) -> RuleSet:
    row = conn.execute("SELECT version FROM policy_versions WHERE status = 'published'").fetchone()
    if row is None:
        raise ServiceError(409, "INVALID_STATE", "当前没有已发布的制度版本", {})
    return RuleSet.from_rules(row["version"], list_policy_rules(settings.db_path, row["version"]))


def _choice(plan: dict[str, Any]) -> PlanChoice:
    """The candidates selected for the current revision, re-validated against the shared models."""
    transport, hotel = plan["transport"], plan["hotel"]
    return PlanChoice(
        outbound=TransportCandidate.model_validate(transport["outbound"]) if transport["outbound"] else None,
        return_leg=TransportCandidate.model_validate(transport["return"]) if transport["return"] else None,
        hotel=HotelCandidate.model_validate(hotel["candidate"]) if hotel["candidate"] else None,
    )


def _judgement(trip: dict[str, Any], outcome: str) -> dict[str, Any]:
    return {"revision": trip["revision"], "policy_version": trip["policy_version"], "outcome": outcome,
            "cost_breakdown": trip["cost"], "violations": trip["violations"] or [],
            "approval_reasons": trip["approval_reasons"] or [], "blocks": trip["blocks"] or []}


def revalidate(conn: sqlite3.Connection, settings, trip: dict[str, Any], owner: dict[str, Any],
               actor: dict[str, Any], *, trace_id: str) -> tuple[dict[str, Any], str]:
    """Apply D-12 inside the caller's transaction. Returns (trip, rule outcome)."""
    rules = _published_rules(conn, settings)
    before = _judgement(trip, TripStatus.PENDING_APPROVAL.value)
    old_plan = trip["plan"]
    choice = _choice(old_plan)
    previous_revision = trip["revision"]

    old_card = pending_approval(conn, trip["id"], previous_revision)
    if old_card is not None:
        conn.execute("UPDATE approvals SET status = 'superseded' WHERE id = ? AND status = 'pending'",
                     (old_card["id"],))
    # A revalidated revision has no checkpoint thread: its decision takes the direct path
    # (`apply_decision` + `deliver`, the same functions the graph uses).
    thread_id = f"{trip['id']}:rev-{previous_revision + 1}:revalidate-{secrets.token_hex(4)}"
    trip = apply_transition(conn, trip, TripEvent.REVALIDATE, actor=actor["id"], trace_id=trace_id,
                            updates={"policy_version": rules.version, "thread_id": thread_id})
    revision = trip["revision"]
    audit.record(conn, trip_id=trip["id"], revision=revision, actor="system",
                 event_type="approval_superseded",
                 payload={"approval_id": old_card["id"] if old_card is not None else None,
                          "superseded_revision": previous_revision, "reason": "revalidate"},
                 trace_id=trace_id)
    audit.record(conn, trip_id=trip["id"], revision=revision, actor="system", event_type="policy_bound",
                 payload={"version": rules.version, "previous": before["policy_version"],
                          "changed": before["policy_version"] != rules.version},
                 trace_id=trace_id, refs=[{"kind": "policy", "id": rules.version}])

    slots = slot_state(trip).to_slots()
    has_blocks = bool(check_blocks(slots, rules, settings.demo_today))
    evaluation = evaluate_trip(slots, rules, owner["role"], settings.demo_today,
                               None if has_blocks else choice)
    refs = [r.model_dump(mode="json") for r in evaluation.refs]
    violations = [v.model_dump(mode="json") for v in evaluation.violations]
    reasons = [r.model_dump(mode="json") for r in evaluation.approval_reasons]
    blocks = [b.model_dump(mode="json") for b in evaluation.blocks]
    audit.record(conn, trip_id=trip["id"], revision=revision, actor="system", event_type="rules_evaluated",
                 payload={"policy_version": rules.version, "source": "policy", "outcome": evaluation.outcome,
                          "violations": violations, "approval_reasons": reasons, "blocks": blocks,
                          "providers_called": False}, trace_id=trace_id, refs=refs)

    if blocks:
        trip = apply_transition(conn, trip, TripEvent.BLOCK, actor="system", trace_id=trace_id,
                                updates={**PLAN_CLEARED, "blocks_json": dumps(blocks)})
        audit.record(conn, trip_id=trip["id"], revision=revision, actor="system",
                     event_type="validation_blocked", payload={"blocks": blocks}, trace_id=trace_id,
                     refs=[{"kind": "rule", "id": b["rule_id"], "version": b["policy_version"],
                            "clause": b["clause"]} for b in blocks if b["rule_id"]])
    else:
        trip = apply_transition(conn, trip, TripEvent.VALIDATED, actor="system", trace_id=trace_id)
        plan = {**old_plan, "revision": revision, "policy_version": rules.version}
        cost = evaluation.cost_breakdown.model_dump(mode="json")
        update_columns(conn, trip, {"plan_json": dumps(plan), "cost_json": dumps(cost),
                                    "violations_json": dumps(violations),
                                    "approval_reasons_json": dumps(reasons)})
        trip = require_trip(conn, trip["id"])

    after = _judgement(trip, evaluation.outcome)
    audit.record(conn, trip_id=trip["id"], revision=revision, actor=actor["id"], event_type="revalidated",
                 payload={"previous_revision": previous_revision, "revision": revision,
                          "reused_candidates": {"outbound": choice.outbound.id if choice.outbound else None,
                                                "return": choice.return_leg.id if choice.return_leg else None,
                                                "hotel": choice.hotel.id if choice.hotel else None},
                          "providers_called": False, "before": before, "after": after,
                          "previous_plan": old_plan},
                 trace_id=trace_id,
                 refs=[{"kind": "policy", "id": before["policy_version"]},
                       {"kind": "policy", "id": rules.version}])

    if evaluation.outcome == TripStatus.AUTO_APPROVED.value:
        trip = apply_transition(conn, trip, TripEvent.AUTO_APPROVE, actor="system", trace_id=trace_id)
        audit.record(conn, trip_id=trip["id"], revision=revision, actor="system", event_type="auto_approved",
                     payload={"total_fen": trip["cost"]["total_fen"],
                              "declared_budget_fen": trip["cost"]["declared_budget_fen"],
                              "via": "revalidate"}, trace_id=trace_id)
    elif evaluation.outcome == TripStatus.PENDING_APPROVAL.value:
        if owner["manager_id"] is None:  # D-21 can only arise if the reporting line changed meanwhile
            audit.record(conn, trip_id=trip["id"], revision=revision, actor="system",
                         event_type="no_approver",
                         payload={"reason": "申请人没有直属经理，无法创建审批卡（D-21）",
                                  "policy_version": rules.version, "plan": trip["plan"],
                                  "cost_breakdown": trip["cost"], "violations": trip["violations"],
                                  "approval_reasons": trip["approval_reasons"]}, trace_id=trace_id)
            trip = apply_transition(conn, trip, TripEvent.NO_APPROVER, actor="system", trace_id=trace_id,
                                    updates=dict(PLAN_CLEARED))
            return trip, "no_approver"
        trip, _ = create_approval(conn, trip, owner, trace_id=trace_id)
    return trip, evaluation.outcome
