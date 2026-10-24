"""LangGraph definition of one trip run (§6.8, TASK-005).

One run = one checkpoint thread (`trips.thread_id`). Nodes:

    validate ─┬─ blocked ──────────────────────────────────────────────► END
              └─ plan ─┬─ auto ──────── auto_approve ─► deliver ─► END
                       ├─ approval ──── request_approval ─► approval[interrupt] ─► decide ─┬─► deliver ─► END
                       │                                                                    └─► END
                       └─ no_approver ─ no_approver ─► END

Rules followed by every node:
- Business state lives in the app database; the graph state only carries ids and the
  routing outcome. Each node commits its own short transaction.
- Every status change goes through `services.trips.apply_transition` (guarded UPDATE).
- `approval` contains nothing but `interrupt()`: LangGraph re-executes the interrupted
  node from the top on resume (RISK-003), so it must stay free of side effects.
- SSE events are emitted with the stream writer *after* the transaction commits, so a
  client never sees an event for a write that was rolled back.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session
from travel_desk.db.repositories import active_faults, list_policy_rules
from travel_desk.domain.models import Preferences
from travel_desk.domain.policy import RuleSet
from travel_desk.domain.rules import check_blocks, evaluate_trip
from travel_desk.domain.states import TripEvent, TripStatus
from travel_desk.llm.summary import Summarizer, make_summary
from travel_desk.planning.planner import build_plan
from travel_desk.providers.demo import build_demo_providers
from travel_desk.services import audit
from travel_desk.services.access import load_employee
from travel_desk.services.approvals import apply_decision, create_approval
from travel_desk.services.delivery import DeliveryFault, deliver
from travel_desk.services.idempotency import IdemRequest
from travel_desk.services.trips import (
    PLAN_CLEARED,
    apply_transition,
    dumps,
    require_trip,
    slot_state,
    update_columns,
)
from travel_desk.services.views import approval_view
from travel_desk.workflow import events


class WorkflowStateError(RuntimeError):
    """A node found the trip in a state it does not expect (e.g. modified behind the workflow)."""


class RunState(TypedDict, total=False):
    trip_id: str
    trace_id: str
    actor: str
    outcome: str  # blocked | auto | approval | no_approver | deliver | end
    decision: dict[str, Any]
    result: str


@dataclass
class GraphContext:
    settings: Settings
    summarizer: Summarizer
    fault_hook: Callable[[], DeliveryFault | None]


def _expect(trip: dict[str, Any], status: TripStatus) -> None:
    if trip["status"] != status.value:
        raise WorkflowStateError(f"trip {trip['id']} is {trip['status']}, expected {status.value}")


def _published_rules(settings: Settings) -> RuleSet:
    with read_session(settings.db_path) as conn:
        row = conn.execute("SELECT version FROM policy_versions WHERE status = 'published'").fetchone()
    if row is None:
        raise WorkflowStateError("no published policy version")
    return RuleSet.from_rules(row["version"], list_policy_rules(settings.db_path, row["version"]))


def build_graph(ctx: GraphContext) -> StateGraph:
    settings = ctx.settings

    async def validate(state: RunState) -> RunState:
        emit = get_stream_writer()
        trace_id = state["trace_id"]
        rules = _published_rules(settings)
        with db_session(settings.db_path) as conn:
            trip = require_trip(conn, state["trip_id"])
            _expect(trip, TripStatus.VALIDATING)
            previous_version = trip["policy_version"]
            update_columns(conn, trip, {"policy_version": rules.version})
            audit.record(conn, trip_id=trip["id"], revision=trip["revision"], actor="system",
                         event_type="policy_bound",
                         payload={"version": rules.version, "previous": previous_version,
                                  "changed": (previous_version is not None
                                              and previous_version != rules.version)},
                         trace_id=trace_id, refs=[{"kind": "policy", "id": rules.version}])
            trip = require_trip(conn, trip["id"])
            blocks = [b.model_dump(mode="json") for b in check_blocks(slot_state(trip).to_slots(), rules,
                                                                       settings.demo_today)]
            if blocks:
                trip = apply_transition(conn, trip, TripEvent.BLOCK, actor="system", trace_id=trace_id,
                                        updates={**PLAN_CLEARED, "blocks_json": dumps(blocks)})
                audit.record(conn, trip_id=trip["id"], revision=trip["revision"], actor="system",
                             event_type="validation_blocked", payload={"blocks": blocks},
                             trace_id=trace_id,
                             refs=[{"kind": "rule", "id": b["rule_id"], "version": b["policy_version"],
                                    "clause": b["clause"]} for b in blocks if b["rule_id"]])
            else:
                trip = apply_transition(conn, trip, TripEvent.VALIDATED, actor="system", trace_id=trace_id)
        if blocks:
            emit(events.make_event("violations", trip_id=trip["id"], revision=trip["revision"],
                                   trace_id=trace_id,
                                   payload={"violations": [], "approval_reasons": [], "blocks": blocks}))
            emit(events.trip_status(trip, previous=TripStatus.VALIDATING.value, trace_id=trace_id))
            return {"outcome": "blocked", "result": TripStatus.POLICY_BLOCKED.value}
        emit(events.stage(trip, "planning", "正在查询交通、酒店与天气候选", trace_id=trace_id))
        return {"outcome": "plan"}

    async def plan(state: RunState) -> RunState:
        emit = get_stream_writer()
        trace_id = state["trace_id"]
        with read_session(settings.db_path) as conn:
            trip = require_trip(conn, state["trip_id"])
            owner = load_employee(conn, trip["employee_id"])
        _expect(trip, TripStatus.PLANNING)
        version = trip["policy_version"]
        rules = RuleSet.from_rules(version, list_policy_rules(settings.db_path, version))
        slots = slot_state(trip).to_slots()
        snapshot = trip["preference_snapshot"] or {}
        preferences = Preferences.model_validate(snapshot) if snapshot else None
        # One fault snapshot per planning run: every provider sees the same configuration.
        # Provider mode switch: demo (default) or live (AUTH-02 required).
        if settings.provider_mode == "live":
            from travel_desk.providers.live import build_live_providers
            transport_p, hotel_p, weather_p = build_live_providers(
                tavily_api_key=settings.tavily_api_key,
                kiwi_mcp_url=settings.kiwi_mcp_url,
                providers_dir=settings.providers_dir,
                qweather_api_host=settings.qweather_api_host,
                qweather_api_key=settings.qweather_api_key,
            )
            from travel_desk.providers.base import Providers
            providers = Providers(transport=transport_p, hotel=hotel_p, weather=weather_p,
                                  mode="live", faults=frozenset())
        else:
            providers = build_demo_providers(settings.providers_dir, active_faults(settings.db_path))
        result = await build_plan(slots=slots, rules=rules, role=owner["role"], today=settings.demo_today,
                                  revision=trip["revision"], preferences=preferences, providers=providers)
        evaluation = evaluate_trip(slots, rules, owner["role"], settings.demo_today, result.choice)
        summary, summary_error = await make_summary(ctx.summarizer, slots, result.plan)
        plan_json = result.plan.model_copy(update={"summary": summary}).to_json()
        cost = evaluation.cost_breakdown.model_dump(mode="json")
        violations = [v.model_dump(mode="json") for v in evaluation.violations]
        reasons = [r.model_dump(mode="json") for r in evaluation.approval_reasons]
        refs = [r.model_dump(mode="json") for r in evaluation.refs]
        choice = result.choice

        with db_session(settings.db_path) as conn:
            trip = require_trip(conn, trip["id"])
            _expect(trip, TripStatus.PLANNING)
            revision = trip["revision"]

            def record(event_type: str, payload: dict[str, Any], refs: list | None = None) -> None:
                audit.record(conn, trip_id=trip["id"], revision=revision, actor="system",
                             event_type=event_type,
                             payload=payload, trace_id=trace_id, refs=refs)

            for call in result.calls:
                record("provider_call", call.model_dump(mode="json"))
            record("candidates_selected", {
                "outbound": choice.outbound.id if choice.outbound else None,
                "return": choice.return_leg.id if choice.return_leg else None,
                "hotel": choice.hotel.id if choice.hotel else None,
                "exception_legs": plan_json["transport"]["exception_legs"],
                "hotel_exception": plan_json["hotel"]["exception"],
                "ranking": plan_json["ranking"],
            })
            if summary_error is not None:
                record("error", {"stage": "summary", "message": summary_error,
                                 "generator": plan_json["summary"]["generator"], "retryable": True})
            record("plan_created", {"plan": plan_json, "cost_breakdown": cost})
            record("rules_evaluated", {"policy_version": version, "source": "policy",
                                       "outcome": evaluation.outcome,
                                       "violations": violations, "approval_reasons": reasons}, refs)
            update_columns(conn, trip, {"plan_json": dumps(plan_json), "cost_json": dumps(cost),
                                        "violations_json": dumps(violations),
                                        "approval_reasons_json": dumps(reasons)})
        emit(events.make_event("plan", trip_id=trip["id"], revision=revision, trace_id=trace_id,
                               payload={**plan_json, "cost_breakdown": cost}))
        emit(events.make_event("violations", trip_id=trip["id"], revision=revision, trace_id=trace_id,
                               payload={"violations": violations, "approval_reasons": reasons, "blocks": []}))
        if evaluation.outcome == TripStatus.AUTO_APPROVED.value:
            return {"outcome": "auto"}
        if owner["manager_id"] is None:
            return {"outcome": "no_approver"}  # D-21
        return {"outcome": "approval"}

    async def auto_approve(state: RunState) -> RunState:
        emit = get_stream_writer()
        trace_id = state["trace_id"]
        with db_session(settings.db_path) as conn:
            trip = require_trip(conn, state["trip_id"])
            trip = apply_transition(conn, trip, TripEvent.AUTO_APPROVE, actor="system", trace_id=trace_id)
            audit.record(conn, trip_id=trip["id"], revision=trip["revision"], actor="system",
                         event_type="auto_approved",
                         payload={"total_fen": trip["cost"]["total_fen"],
                                  "declared_budget_fen": trip["cost"]["declared_budget_fen"]},
                         trace_id=trace_id)
        emit(events.trip_status(trip, previous=TripStatus.PLANNING.value, trace_id=trace_id))
        return {}

    async def request_approval(state: RunState) -> RunState:
        emit = get_stream_writer()
        trace_id = state["trace_id"]
        with db_session(settings.db_path) as conn:
            trip = require_trip(conn, state["trip_id"])
            owner = load_employee(conn, trip["employee_id"])
            previous = trip["status"]
            trip, row = create_approval(conn, trip, owner, trace_id=trace_id)
            card = approval_view(conn, row)
        emit(events.make_event("approval_required", trip_id=trip["id"], revision=trip["revision"],
                               trace_id=trace_id, payload=card))
        emit(events.trip_status(trip, previous=previous, trace_id=trace_id))
        return {}

    def approval(state: RunState) -> RunState:
        # Side-effect free: re-executed from the top when the run is resumed (RISK-003).
        decision = interrupt({"trip_id": state["trip_id"], "awaiting": "manager_decision"})
        return {"decision": decision}

    async def decide(state: RunState) -> RunState:
        emit = get_stream_writer()
        d = state["decision"]
        idem = IdemRequest(**d["idem"]) if d.get("idem") else None
        outcome = apply_decision(settings, trip_id=state["trip_id"], revision=d["revision"],
                                 approver_id=d["approver_id"], decision=d["decision"],
                                 comment=d.get("comment"),
                                 trace_id=d["trace_id"], via="graph", idem=idem)
        with read_session(settings.db_path) as conn:
            trip = require_trip(conn, state["trip_id"])
        emit(events.trip_status(trip, previous=TripStatus.PENDING_APPROVAL.value, trace_id=d["trace_id"]))
        nxt = "deliver" if outcome["status"] == TripStatus.APPROVED.value else "end"
        return {"trace_id": d["trace_id"], "outcome": nxt, "result": outcome["status"]}

    async def deliver_node(state: RunState) -> RunState:
        emit = get_stream_writer()
        trace_id = state["trace_id"]
        outcome = deliver(settings, state["trip_id"], actor="system", trace_id=trace_id,
                          fault_hook=ctx.fault_hook())
        with read_session(settings.db_path) as conn:
            trip = require_trip(conn, state["trip_id"])
        for previous, new in outcome.transitions:
            emit(events.trip_status(trip, previous=previous, status=new, trace_id=trace_id))
        return {"result": outcome.status}

    async def no_approver(state: RunState) -> RunState:
        emit = get_stream_writer()
        trace_id = state["trace_id"]
        with db_session(settings.db_path) as conn:
            trip = require_trip(conn, state["trip_id"])
            # IMP-06: the computed plan is kept in the audit trail only, not as the current plan.
            audit.record(conn, trip_id=trip["id"], revision=trip["revision"], actor="system",
                         event_type="no_approver",
                         payload={"reason": "申请人没有直属经理，无法创建审批卡（D-21）",
                                  "policy_version": trip["policy_version"], "plan": trip["plan"],
                                  "cost_breakdown": trip["cost"], "violations": trip["violations"],
                                  "approval_reasons": trip["approval_reasons"]},
                         trace_id=trace_id)
            trip = apply_transition(conn, trip, TripEvent.NO_APPROVER, actor="system", trace_id=trace_id,
                                    updates=dict(PLAN_CLEARED))
        emit(events.trip_status(trip, previous=TripStatus.PLANNING.value, trace_id=trace_id))
        return {"result": "no_approver"}

    graph = StateGraph(RunState)
    graph.add_node("validate", validate)
    graph.add_node("plan", plan)
    graph.add_node("auto_approve", auto_approve)
    graph.add_node("request_approval", request_approval)
    graph.add_node("approval", approval)
    graph.add_node("decide", decide)
    graph.add_node("deliver", deliver_node)
    graph.add_node("no_approver", no_approver)
    graph.add_edge(START, "validate")
    graph.add_conditional_edges("validate", lambda s: s["outcome"], {"blocked": END, "plan": "plan"})
    graph.add_conditional_edges("plan", lambda s: s["outcome"],
                                {"auto": "auto_approve", "approval": "request_approval",
                                 "no_approver": "no_approver"})
    graph.add_edge("auto_approve", "deliver")
    graph.add_edge("request_approval", "approval")
    graph.add_edge("approval", "decide")
    graph.add_conditional_edges("decide", lambda s: s["outcome"], {"deliver": "deliver", "end": END})
    graph.add_edge("deliver", END)
    graph.add_edge("no_approver", END)
    return graph
