"""TASK-005: LangGraph workflow, approvals, idempotency and delivery (AC-008/009/010/011).

Every test drives the real workflow (seeded rules → Demo providers → selector →
rules → graph with an AsyncSqliteSaver checkpoint) through the same service
entry points the API layer uses. Scenario inputs and expected numbers are the
contract's (§7.2).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

import pytest

from travel_desk.config import Settings
from travel_desk.db.database import initialize_database, read_session
from travel_desk.db.repositories import set_fault
from travel_desk.services import trips as trip_service
from travel_desk.services.access import load_employee
from travel_desk.services.audit import list_events
from travel_desk.services.errors import ServiceError
from travel_desk.services.views import list_outbox, trip_view
from travel_desk.workflow.engine import Workflow

S01 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
       "purpose": "供应商大会", "declared_budget_fen": 400000}
S02 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-25", "end_date": "2026-10-26",
       "purpose": "客户拜访", "declared_budget_fen": 250000}
S06 = {**S02, "start_date": "2026-10-19", "end_date": "2026-10-20"}


@pytest.fixture
def seeded(settings: Settings) -> Settings:
    initialize_database(settings)
    return settings


@pytest.fixture
async def wf(seeded: Settings):
    workflow = await Workflow(seeded).open()
    yield workflow
    await workflow.close()


def user(settings: Settings, employee_id: str) -> dict[str, Any]:
    with read_session(settings.db_path) as conn:
        return load_employee(conn, employee_id)


def trip_of(settings: Settings, trip_id: str) -> dict[str, Any]:
    with read_session(settings.db_path) as conn:
        return trip_view(conn, trip_service.load_trip(conn, trip_id))


def audit_types(settings: Settings, trip_id: str) -> list[str]:
    with read_session(settings.db_path) as conn:
        return [e["type"] for e in list_events(conn, trip_id)]


def audits(settings: Settings, trip_id: str, event_type: str) -> list[dict[str, Any]]:
    with read_session(settings.db_path) as conn:
        return [e for e in list_events(conn, trip_id) if e["type"] == event_type]


def outbox(settings: Settings, trip_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    with read_session(settings.db_path) as conn:
        return list_outbox(conn, trip_id=trip_id, kind=kind)


def new_trip(settings: Settings, slots: dict[str, Any], owner: str = "u-zhangsan") -> str:
    actor = user(settings, owner)
    trip = trip_service.create_trip(settings, actor, trace_id="t-create")
    trip_service.update_slots(settings, actor, trip["id"], expected_revision=1, changes=slots, trace_id="t-slots")
    return trip["id"]


async def submit(wf: Workflow, trip_id: str, *, owner: str = "u-zhangsan", revision: int = 1,
                 key: str = "submit-1") -> list[dict[str, Any]]:
    run = await wf.submit(trip_id, user(wf.settings, owner), {"expected_revision": revision},
                          idempotency_key=key, trace_id="t-submit")
    events = await run.collect()
    await run.wait()
    return events


async def decide(wf: Workflow, trip_id: str, decision: str, *, revision: int = 1, comment: str | None = None,
                 approver: str = "u-lisi", key: str = "decide-1") -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = {"revision": revision, "decision": decision}
    if comment is not None:
        body["comment"] = comment
    return await wf.decide(trip_id, user(wf.settings, approver), body, idempotency_key=key, trace_id="t-decide")


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def statuses(events: list[dict[str, Any]]) -> list[str]:
    return [e["payload"]["status"] for e in events if e["type"] == "trip_status"]


def assert_one_terminal(events: list[dict[str, Any]]) -> None:
    terminal = [e for e in events if e["type"] in ("done", "error")]
    assert len(terminal) == 1 and events[-1] is terminal[0]


# ---------------------------------------------------------------------- S-01 / AC-008


async def test_s01_golden_path_pending_then_approved_and_delivered_once(wf, seeded):
    trip_id = new_trip(seeded, S01)
    events = await submit(wf, trip_id)
    assert_one_terminal(events)
    assert types(events) == ["trip_status", "status", "plan", "violations", "approval_required",
                             "trip_status", "done"]
    assert statuses(events) == ["VALIDATING", "PENDING_APPROVAL"]
    assert {e["trace_id"] for e in events} == {"t-submit"}

    trip = trip_of(seeded, trip_id)
    assert trip["status"] == "PENDING_APPROVAL" and trip["revision"] == 1 and trip["policy_version"] == "v1.2"
    plan, cost = trip["plan"], trip["cost_breakdown"]
    assert plan["transport"]["outbound"]["id"] == "T-BJS-SHA-1028-A"
    assert plan["transport"]["return"]["id"] == "T-SHA-BJS-1030-A"
    assert plan["hotel"]["candidate"]["id"] == "H-SHA-CONTRACT-01"
    assert (cost["nights"], cost["days"], cost["transport_fen"], cost["hotel_fen"], cost["meal_fen"],
            cost["total_fen"]) == (2, 3, 350000, 96000, 30000, 476000)
    assert trip["violations"] == []
    assert [r["code"] for r in trip["approval_reasons"]] == ["BUDGET_EXCEEDED", "OVER_AUTO_APPROVAL_LIMIT"]
    assert plan["summary"]["status"] == "ok" and plan["summary"]["generator"] == "fake"

    # AC-008: the card snapshot carries everything a manager needs.
    card = trip["current_approval"]["card"]
    assert trip["current_approval"]["status"] == "pending" and trip["current_approval"]["approver_id"] == "u-lisi"
    assert card["revision"] == 1 and card["policy_version"] == "v1.2"
    assert card["cost_breakdown"]["total_fen"] == 476000 and card["budget_delta_fen"] == 76000
    assert {r["code"] for r in card["approval_reasons"]} == {"BUDGET_EXCEEDED", "OVER_AUTO_APPROVAL_LIMIT"}
    limit = next(r for r in card["approval_reasons"] if r["code"] == "OVER_AUTO_APPROVAL_LIMIT")
    assert limit["basis"] == {"rule_id": "RULE-AUTO-001", "policy_version": "v1.2", "clause_ref": "POL-6"}
    assert {"demo_fixture", "policy", "model"} <= set(card["sources"])
    assert outbox(seeded, trip_id) == []

    status, body = await decide(wf, trip_id, "approve")
    assert status == 200 and body["trip"]["status"] == "DELIVERED"
    assert body["approval"]["decision"] == "approve" and body["approval"]["status"] == "decided"
    deliveries = outbox(seeded, trip_id, "trip_delivery")
    assert len(deliveries) == 1 and outbox(seeded, trip_id, "workflow_notification") == []
    email = deliveries[0]
    assert email["to"] == ["u-zhangsan"] and email["cc"] == ["u-lisi"]
    assert email["unique_key"] == f"{trip_id}:1:trip_delivery"
    assert f"/api/trips/{trip_id}/itinerary?revision=1" in email["body"]
    assert "合计：4,760.00 元" in email["body"] and "李四 批准" in email["body"]
    assert body["trip"]["delivery"]["itinerary_url"] == f"/api/trips/{trip_id}/itinerary?revision=1"

    kinds = audit_types(seeded, trip_id)
    for expected in ("trip_created", "slots_changed", "submitted", "preference_snapshot", "policy_bound",
                     "provider_call", "candidates_selected", "plan_created", "rules_evaluated",
                     "approval_requested", "decision_recorded", "delivery_written"):
        assert expected in kinds, expected
    assert kinds.index("approval_requested") < kinds.index("decision_recorded") < kinds.index("delivery_written")
    evaluated = audits(seeded, trip_id, "rules_evaluated")[0]
    assert {"kind": "rule", "id": "RULE-AUTO-001", "version": "v1.2", "clause": "POL-6"} in evaluated["refs"]
    # HC-04: the delivery email is written strictly after approval.
    assert audits(seeded, trip_id, "decision_recorded")[0]["seq"] < audits(seeded, trip_id, "delivery_written")[0]["seq"]


async def test_s02_auto_approved_is_delivered_without_an_approval(wf, seeded):
    trip_id = new_trip(seeded, S02)
    events = await submit(wf, trip_id)
    assert statuses(events) == ["VALIDATING", "AUTO_APPROVED", "DELIVERY_PENDING", "DELIVERED"]
    assert "approval_required" not in types(events)
    trip = trip_of(seeded, trip_id)
    assert trip["status"] == "DELIVERED" and trip["current_approval"] is None
    assert trip["plan"]["hotel"]["candidate"]["id"] == "H-SHA-CONTRACT-02"
    assert trip["cost_breakdown"]["total_fen"] == 190000
    with read_session(seeded.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approvals WHERE trip_id = ?", (trip_id,)).fetchone()[0] == 0
    emails = outbox(seeded, trip_id)
    assert [e["kind"] for e in emails] == ["trip_delivery"]
    assert "自动通过" in emails[0]["body"]


async def test_s06_hard_block_never_calls_a_provider(wf, seeded):
    trip_id = new_trip(seeded, S06)
    events = await submit(wf, trip_id)
    assert types(events) == ["trip_status", "violations", "trip_status", "done"]
    assert statuses(events) == ["VALIDATING", "POLICY_BLOCKED"]
    assert [b["code"] for b in events[1]["payload"]["blocks"]] == ["START_DATE_IN_PAST"]
    trip = trip_of(seeded, trip_id)
    assert trip["status"] == "POLICY_BLOCKED" and trip["plan"] is None
    kinds = audit_types(seeded, trip_id)
    assert "validation_blocked" in kinds
    assert "provider_call" not in kinds and "plan_created" not in kinds
    blocked = audits(seeded, trip_id, "validation_blocked")[0]
    assert blocked["payload"]["blocks"][0]["actual_value"] == "2026-10-19"

    # D-09: editing returns to COLLECTING without a revision bump; resubmission then works.
    actor = user(seeded, "u-zhangsan")
    trip_service.update_slots(seeded, actor, trip_id, expected_revision=1,
                              changes={"start_date": "2026-10-25", "end_date": "2026-10-26"}, trace_id="t-fix")
    trip = trip_of(seeded, trip_id)
    assert trip["status"] == "COLLECTING" and trip["revision"] == 1 and trip["blocks"] == []
    assert "blocked_cleared" in audit_types(seeded, trip_id)
    events = await submit(wf, trip_id, key="submit-2")
    assert statuses(events)[-1] == "DELIVERED"


# ---------------------------------------------------------------------- S-07 / S-08


async def test_s07_reject_writes_one_notification_and_no_delivery(wf, seeded):
    trip_id = new_trip(seeded, S01)
    await submit(wf, trip_id)
    status, body = await decide(wf, trip_id, "reject", comment="预算过高，请改为线上参会")
    assert status == 200 and body["trip"]["status"] == "REJECTED"
    assert outbox(seeded, trip_id, "trip_delivery") == []
    notes = outbox(seeded, trip_id, "workflow_notification")
    assert len(notes) == 1
    assert "驳回" in notes[0]["body"] and "预算过高，请改为线上参会" in notes[0]["body"]
    assert "revision：1" in notes[0]["body"]
    assert notes[0]["to"] == ["u-zhangsan"] and notes[0]["cc"] == ["u-lisi"]

    with pytest.raises(ServiceError) as err:
        await decide(wf, trip_id, "approve", key="decide-2")
    assert err.value.status_code == 409 and err.value.code == "ALREADY_DECIDED"
    assert len(outbox(seeded, trip_id)) == 1


async def test_s08_request_changes_bumps_revision_and_reapproval_delivers(wf, seeded):
    trip_id = new_trip(seeded, S01)
    await submit(wf, trip_id)
    first_thread = trip_of(seeded, trip_id)["thread_id"]
    status, body = await decide(wf, trip_id, "request_changes", comment="请确认预算")
    assert status == 200
    trip = body["trip"]
    assert trip["status"] == "COLLECTING" and trip["revision"] == 2 and trip["plan"] is None
    assert len(outbox(seeded, trip_id, "workflow_notification")) == 1

    actor = user(seeded, "u-zhangsan")
    trip_service.update_slots(seeded, actor, trip_id, expected_revision=2,
                              changes={"declared_budget_fen": 500000}, trace_id="t-budget")
    events = await submit(wf, trip_id, revision=2, key="submit-rev2")
    assert statuses(events)[-1] == "PENDING_APPROVAL"
    trip = trip_of(seeded, trip_id)
    assert trip["revision"] == 2 and trip["thread_id"] != first_thread
    assert trip["cost_breakdown"]["total_fen"] == 476000
    assert [r["code"] for r in trip["approval_reasons"]] == ["OVER_AUTO_APPROVAL_LIMIT"]

    # The revision-1 approval is no longer actionable.
    with pytest.raises(ServiceError) as err:
        await decide(wf, trip_id, "approve", revision=1, key="old-rev")
    assert err.value.code == "STALE_REVISION" and err.value.details["current_revision"] == 2

    status, body = await decide(wf, trip_id, "approve", revision=2, key="decide-rev2")
    assert body["trip"]["status"] == "DELIVERED" and body["trip"]["revision"] == 2
    deliveries = outbox(seeded, trip_id, "trip_delivery")
    assert len(deliveries) == 1 and deliveries[0]["revision"] == 2
    decisions = audits(seeded, trip_id, "decision_recorded")
    assert [(d["revision"], d["payload"]["decision"]) for d in decisions] == [(1, "request_changes"), (2, "approve")]
    plans = audits(seeded, trip_id, "plan_created")
    assert [p["revision"] for p in plans] == [1, 2]  # the old plan stays auditable


# ---------------------------------------------------------------------- S-09 / D-21


async def test_s09_transport_timeout_forces_approval_with_known_subtotal(wf, seeded):
    set_fault(seeded.db_path, "transport_timeout", True, "2026-10-20T00:00:00Z")
    trip_id = new_trip(seeded, S02)
    events = await submit(wf, trip_id)
    assert statuses(events)[-1] == "PENDING_APPROVAL"
    trip = trip_of(seeded, trip_id)
    cost = trip["cost_breakdown"]
    assert trip["plan"]["incomplete"] is True and trip["plan"]["transport"]["outbound"] is None
    assert cost["total_fen"] is None and cost["known_subtotal_fen"] == 60000
    assert "PLAN_INCOMPLETE" in [r["code"] for r in trip["approval_reasons"]]
    calls = [e["payload"] for e in audits(seeded, trip_id, "provider_call")]
    assert any(c["provider"] == "transport" and c["status"] == "timeout" for c in calls)
    assert any(c["provider"] == "hotel_contract" and c["status"] == "ok" for c in calls)


async def test_d21_manager_without_manager_gets_no_approver_and_no_kept_plan(wf, seeded):
    trip_id = new_trip(seeded, S01, owner="u-lisi")
    events = await submit(wf, trip_id, owner="u-lisi")
    assert events[-1]["type"] == "error" and events[-1]["payload"]["code"] == "NO_APPROVER"
    assert_one_terminal(events)
    trip = trip_of(seeded, trip_id)
    assert trip["status"] == "COLLECTING" and trip["revision"] == 1
    assert trip["plan"] is None and trip["cost_breakdown"] is None and trip["current_approval"] is None
    recorded = audits(seeded, trip_id, "no_approver")
    assert len(recorded) == 1 and recorded[0]["payload"]["cost_breakdown"]["total_fen"] is not None
    assert outbox(seeded, trip_id) == []


# ---------------------------------------------------------------------- AC-009 permissions & idempotency


async def test_ac009_decision_permissions_and_validation(wf, seeded):
    trip_id = new_trip(seeded, S01)
    await submit(wf, trip_id)

    async def code_of(**kwargs) -> tuple[int, str]:
        with pytest.raises(ServiceError) as err:
            await decide(wf, trip_id, **kwargs)
        return err.value.status_code, err.value.code

    assert await code_of(decision="approve", approver="u-zhangsan") == (403, "FORBIDDEN")
    assert await code_of(decision="approve", approver="u-wangwu") == (403, "FORBIDDEN")
    assert await code_of(decision="approve", approver="u-zhaoliu") == (403, "NOT_DIRECT_MANAGER")
    assert await code_of(decision="maybe") == (400, "INVALID_DECISION")
    assert await code_of(decision="reject") == (422, "COMMENT_REQUIRED")
    assert await code_of(decision="request_changes", comment="   ") == (422, "COMMENT_REQUIRED")
    assert await code_of(decision="approve", revision=2) == (409, "STALE_REVISION")
    with pytest.raises(ServiceError) as err:
        await wf.decide(trip_id, user(seeded, "u-lisi"), {"revision": 1, "decision": "approve"},
                        idempotency_key=None, trace_id="t")
    assert err.value.code == "IDEMPOTENCY_KEY_REQUIRED"
    with pytest.raises(ServiceError) as err:
        await wf.decide("missing-trip", user(seeded, "u-lisi"), {"revision": 1, "decision": "approve"},
                        idempotency_key="k", trace_id="t")
    assert err.value.status_code == 404
    # Nothing was decided by any rejected attempt.
    assert trip_of(seeded, trip_id)["status"] == "PENDING_APPROVAL"
    assert audits(seeded, trip_id, "decision_recorded") == []


async def test_self_approval_is_forbidden(wf, seeded):
    with sqlite3.connect(seeded.db_path) as conn:
        conn.execute("UPDATE employees SET manager_id = 'u-lisi' WHERE id = 'u-lisi'")
    trip_id = new_trip(seeded, S01, owner="u-lisi")
    await submit(wf, trip_id, owner="u-lisi")
    with pytest.raises(ServiceError) as err:
        await decide(wf, trip_id, "approve", approver="u-lisi")
    assert (err.value.status_code, err.value.code) == (403, "SELF_APPROVAL")


async def test_ac009_same_key_replays_and_other_keys_conflict(wf, seeded):
    trip_id = new_trip(seeded, S01)
    await submit(wf, trip_id)
    first = await decide(wf, trip_id, "approve", key="k-1")
    events_before = len(audit_types(seeded, trip_id))

    replay = await decide(wf, trip_id, "approve", key="k-1")
    assert replay == first
    assert len(audit_types(seeded, trip_id)) == events_before  # no side effect on replay
    assert len(outbox(seeded, trip_id, "trip_delivery")) == 1

    with pytest.raises(ServiceError) as err:
        await decide(wf, trip_id, "reject", comment="改主意", key="k-1")
    assert (err.value.status_code, err.value.code) == (422, "IDEMPOTENCY_KEY_REUSED")
    with pytest.raises(ServiceError) as err:
        await decide(wf, trip_id, "approve", key="k-2")
    assert (err.value.status_code, err.value.code) == (409, "ALREADY_DECIDED")


async def test_ac009_concurrent_decisions_apply_exactly_once(wf, seeded):
    trip_id = new_trip(seeded, S01)
    await submit(wf, trip_id)
    results = await asyncio.gather(
        decide(wf, trip_id, "approve", key="c-1"),
        decide(wf, trip_id, "approve", key="c-2"),
        decide(wf, trip_id, "reject", comment="并发", key="c-3"),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, Exception)]
    failed = [r for r in results if isinstance(r, ServiceError)]
    assert len(ok) == 1 and len(failed) == 2
    assert {f.code for f in failed} == {"ALREADY_DECIDED"}
    assert len(audits(seeded, trip_id, "decision_recorded")) == 1
    assert len(outbox(seeded, trip_id)) == 1


async def test_submit_validation_order_and_idempotent_replay(wf, seeded):
    actor = user(seeded, "u-zhangsan")
    empty = trip_service.create_trip(seeded, actor, trace_id="t")["id"]
    with pytest.raises(ServiceError) as err:
        await wf.submit(empty, actor, {"expected_revision": 1}, idempotency_key=None, trace_id="t")
    assert err.value.code == "IDEMPOTENCY_KEY_REQUIRED"
    with pytest.raises(ServiceError) as err:
        await wf.submit(empty, actor, {"expected_revision": "1"}, idempotency_key="k", trace_id="t")
    assert err.value.code == "VALIDATION_ERROR"
    with pytest.raises(ServiceError) as err:
        await wf.submit(empty, user(seeded, "u-zhaoliu"), {"expected_revision": 1}, idempotency_key="k",
                        trace_id="t")
    assert err.value.status_code == 404  # not visible to an unrelated manager
    with pytest.raises(ServiceError) as err:
        await wf.submit(empty, user(seeded, "u-lisi"), {"expected_revision": 1}, idempotency_key="k",
                        trace_id="t")
    assert (err.value.status_code, err.value.code) == (403, "FORBIDDEN")  # visible manager, not the owner
    with pytest.raises(ServiceError) as err:
        await wf.submit(empty, actor, {"expected_revision": 2}, idempotency_key="k", trace_id="t")
    assert err.value.code == "STALE_REVISION"
    with pytest.raises(ServiceError) as err:
        await wf.submit(empty, actor, {"expected_revision": 1}, idempotency_key="k", trace_id="t")
    assert err.value.code == "SLOTS_INCOMPLETE"
    assert err.value.details["missing_fields"] == ["origin", "destination", "start_date", "end_date",
                                                   "purpose", "declared_budget_fen"]

    # The 4xx attempts above stored no key, so "k" is still usable for the real submit.
    trip_service.update_slots(seeded, actor, empty, expected_revision=1, changes=S01, trace_id="t")
    run = await wf.submit(empty, actor, {"expected_revision": 1}, idempotency_key="k", trace_id="t")
    await run.collect()
    replay = await (await wf.submit(empty, actor, {"expected_revision": 1}, idempotency_key="k",
                                    trace_id="t2")).collect()
    assert types(replay) == ["trip_status", "done"]
    assert replay[0]["payload"]["replayed"] is True and replay[0]["payload"]["status"] == "PENDING_APPROVAL"
    assert len(audits(seeded, empty, "submitted")) == 1
    with pytest.raises(ServiceError) as err:
        await wf.submit(empty, actor, {"expected_revision": 1}, idempotency_key="k-new", trace_id="t")
    assert err.value.code == "INVALID_STATE"


async def test_preference_snapshot_sources(wf, seeded):
    with sqlite3.connect(seeded.db_path) as conn:
        conn.execute("UPDATE employees SET preferences_json = ? WHERE id = 'u-zhangsan'",
                     (json.dumps({"avoid_early_departure": True}),))
    trip_id = new_trip(seeded, S02)
    await submit(wf, trip_id)
    trip = trip_of(seeded, trip_id)
    assert trip["preference_source"] == "personal_snapshot"
    assert trip["preferences_used"] == {"avoid_early_departure": True}

    override = new_trip(seeded, {**S02, "preferences": {"avoid_early_departure": False}})
    await submit(wf, override)
    trip = trip_of(seeded, override)
    assert trip["preference_source"] == "trip_override"
    assert trip["preferences_used"] == {"avoid_early_departure": False}


# ---------------------------------------------------------------------- AC-010 restart / AC-011 delivery


async def test_s10_restart_resumes_from_the_approval_interrupt(seeded):
    first = await Workflow(seeded).open()
    trip_id = new_trip(seeded, S01)
    await submit(first, trip_id)
    await first.close()  # the process is gone; only the two SQLite files remain

    second = await Workflow(seeded).open()
    try:
        report = await second.recover()
        assert report == {"aborted": [], "delivered": [], "still_pending": []}
        trip = trip_of(seeded, trip_id)
        assert trip["status"] == "PENDING_APPROVAL" and trip["current_approval"]["status"] == "pending"
        state = await second.graph.aget_state({"configurable": {"thread_id": trip["thread_id"]}})
        assert state.interrupts  # the checkpoint survived and is waiting for the decision
        status, body = await decide(second, trip_id, "approve")
        assert body["trip"]["status"] == "DELIVERED"
        assert audits(seeded, trip_id, "decision_recorded")[0]["payload"]["via"] == "graph"
        assert len(outbox(seeded, trip_id, "trip_delivery")) == 1
        assert len(audits(seeded, trip_id, "approval_requested")) == 1
    finally:
        await second.close()


async def test_recover_aborts_runs_cut_off_mid_planning(seeded):
    trip_id = new_trip(seeded, S01)
    with sqlite3.connect(seeded.db_path) as conn:
        conn.execute("UPDATE trips SET status = 'PLANNING', plan_json = '{}' WHERE id = ?", (trip_id,))
    workflow = await Workflow(seeded).open()
    try:
        report = await workflow.recover()
    finally:
        await workflow.close()
    assert report["aborted"] == [trip_id]
    trip = trip_of(seeded, trip_id)
    assert trip["status"] == "COLLECTING" and trip["plan"] is None
    errors = audits(seeded, trip_id, "error")
    assert errors[-1]["payload"]["reason"] == "interrupted_by_restart"


async def test_ac011_delivery_failure_stays_pending_and_retry_writes_one_email(wf, seeded):
    attempts: list[tuple[str, int]] = []

    def broken(trip_id: str, revision: int) -> None:
        attempts.append((trip_id, revision))
        raise OSError("disk full: secret-looking detail")

    wf.delivery_fault_hook = broken
    trip_id = new_trip(seeded, S01)
    await submit(wf, trip_id)
    status, body = await decide(wf, trip_id, "approve")
    assert body["trip"]["status"] == "DELIVERY_PENDING"
    assert body["trip"]["delivery"]["error"].startswith("OSError")
    assert "secret-looking" not in json.dumps(body, ensure_ascii=False)
    assert outbox(seeded, trip_id, "trip_delivery") == []
    assert body["trip"]["delivery"]["itinerary_url"] is None  # the itinerary rolled back too
    failed = audits(seeded, trip_id, "delivery_failed")
    assert len(failed) == 1 and "secret-looking" not in json.dumps(failed[0], ensure_ascii=False)

    with pytest.raises(ServiceError) as err:
        await wf.retry_delivery(trip_id, user(seeded, "u-lisi"), trace_id="t")
    assert err.value.status_code == 403
    still = await wf.retry_delivery(trip_id, user(seeded, "u-wangwu"), trace_id="t-retry")
    assert still["status"] == "DELIVERY_PENDING" and still["delivery"]["attempts"] == 2

    wf.delivery_fault_hook = None
    done = await wf.retry_delivery(trip_id, user(seeded, "u-wangwu"), trace_id="t-retry")
    assert done["status"] == "DELIVERED" and done["delivery"]["error"] is None
    assert done["delivery"]["attempts"] == 3
    assert len(outbox(seeded, trip_id, "trip_delivery")) == 1
    with pytest.raises(ServiceError) as err:
        await wf.retry_delivery(trip_id, user(seeded, "u-wangwu"), trace_id="t")
    assert err.value.status_code == 409

    # Replaying the original decision returns the stored final response, not a new delivery.
    replay = await decide(wf, trip_id, "approve")
    assert replay[1]["trip"]["status"] == "DELIVERY_PENDING"  # first result, as stored
    assert len(outbox(seeded, trip_id, "trip_delivery")) == 1


async def test_startup_recovery_redelivers_pending_trips(seeded):
    workflow = await Workflow(seeded, delivery_fault_hook=lambda *_: (_ for _ in ()).throw(OSError())).open()
    trip_id = new_trip(seeded, S02)
    await submit(workflow, trip_id)
    assert trip_of(seeded, trip_id)["status"] == "DELIVERY_PENDING"
    await workflow.close()

    restarted = await Workflow(seeded).open()
    try:
        report = await restarted.recover()
        assert report["delivered"] == [trip_id]
        assert len(outbox(seeded, trip_id, "trip_delivery")) == 1
        assert (await restarted.recover())["delivered"] == []  # nothing left to do
    finally:
        await restarted.close()


async def test_summary_with_money_is_dropped_but_the_plan_continues(seeded):
    from travel_desk.llm.summary import SummaryText

    class Chatty:
        name = "chatty"

        async def summarize(self, context):
            return SummaryText(text="总价约 4760 元，非常划算", generator="chatty")

    workflow = await Workflow(seeded, summarizer=Chatty()).open()
    try:
        trip_id = new_trip(seeded, S02)
        await submit(workflow, trip_id)
    finally:
        await workflow.close()
    trip = trip_of(seeded, trip_id)
    assert trip["status"] == "DELIVERED"
    assert trip["plan"]["summary"]["status"] == "unavailable" and trip["plan"]["summary"]["text"] is None
    assert any(e["payload"].get("stage") == "summary" for e in audits(seeded, trip_id, "error"))
    assert "4760 元" not in outbox(seeded, trip_id)[0]["body"]


async def test_unexpected_failure_aborts_to_collecting_with_one_error_event(seeded):
    class Exploding:
        name = "exploding"

        async def summarize(self, context):
            raise RuntimeError("boom")

    workflow = await Workflow(seeded, summarizer=Exploding()).open()
    try:
        trip_id = new_trip(seeded, S02)
        events = await submit(workflow, trip_id)
    finally:
        await workflow.close()
    assert_one_terminal(events)
    assert events[-1]["payload"]["code"] == "WORKFLOW_FAILED" and events[-1]["payload"]["retryable"] is True
    trip = trip_of(seeded, trip_id)
    assert trip["status"] == "COLLECTING" and trip["plan"] is None
    assert outbox(seeded, trip_id) == []


async def test_reset_clears_trips_and_checkpoint_threads(wf, seeded):
    trip_id = new_trip(seeded, S01)
    await submit(wf, trip_id)
    assert [item async for item in wf.saver.alist(None)]
    await wf.reset(user(seeded, "u-wangwu"), trace_id="t-reset")
    assert [item async for item in wf.saver.alist(None)] == []
    with read_session(seeded.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 0
        reset_events = conn.execute("SELECT type FROM audit_events").fetchall()
    assert [r["type"] for r in reset_events] == ["demo_reset"]
