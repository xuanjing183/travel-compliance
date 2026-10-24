"""AC-023 over HTTP (#24 preferences) and AC-024 / S-12 (#14 revalidate, D-12, IMP-04/IMP-05).

The revalidate edge cases here (IMP-04/IMP-05, unknown-version and cabin variants) need rule
values that no recorded extraction produces, so they publish through `publish_direct` — a
test-only shortcut performing the same atomic swap as #23 (old published → archived). The S-12
end-to-end run through the real upload/extract/review/publish API (#20–#23) lives in
`test_policies.py::test_ac024_s12_publish_v13_through_the_api_then_revalidate`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi.testclient import TestClient
from test_api import (
    LISI,
    S01,
    WANG,
    ZHANG,
    ZHAO,
    audit_events,
    error_code,
    get_trip,
    h,
    pending_s01,
    stream,
    trip_with,
)

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session
from travel_desk.db.seed import QUOTES_V12, _rules_for, locate_line

V13_HOTEL_QUOTE = "员工住宿上限：A 类城市每晚上限 450 元；B 类城市每晚上限 400 元；C 类城市每晚上限 300 元。"
AC023 = {"origin": "北京", "destination": "上海", "start_date": "2026-11-05", "end_date": "2026-11-06",
         "purpose": "客户拜访", "declared_budget_fen": 250000}


def publish_direct(settings: Settings, version: str, *, values: dict[str, dict[str, Any]] | None = None,
                   quotes: dict[str, str] | None = None, document: str = "v1.2.md") -> None:
    """Test-only: publish `version` = v1.2 rules with `values` overrides (rule id → value)."""
    path = settings.policies_dir / document
    text = path.read_text(encoding="utf-8")
    now = "2026-10-20T00:00:00+00:00"
    with db_session(settings.db_path) as conn:
        conn.execute("UPDATE policy_versions SET status = 'archived' WHERE status = 'published'")
        conn.execute("INSERT INTO policy_versions (version, status, effective_date, document_path,"
                     " document_hash, created_at, published_at) VALUES (?, 'published', ?, ?, ?, ?, ?)",
                     (version, "2026-11-01", str(path), hashlib.sha256(path.read_bytes()).hexdigest(), now, now))
        for rule in _rules_for(version, quotes or QUOTES_V12):
            value = (values or {}).get(rule["id"], rule["value"])
            conn.execute("INSERT INTO policy_rules (version, id, type, condition_json, value_json, unit, clause,"
                         " quote, locator, quote_status, status, confirmed_by, created_at, updated_at)"
                         " VALUES (?,?,?,?,?,?,?,?,?,'valid','confirmed','u-wangwu',?,?)",
                         (version, rule["id"], rule["type"], json.dumps(rule["condition"], ensure_ascii=False),
                          json.dumps(value, ensure_ascii=False), rule["unit"], rule["clause"], rule["quote"],
                          locate_line(text, rule["quote"]), now, now))


def publish_v13(settings: Settings) -> None:
    publish_direct(settings, "v1.3", document="v1.3.md",
                   quotes={**QUOTES_V12, "hotel_employee": V13_HOTEL_QUOTE},
                   values={"RULE-HOTEL-001": {"caps_fen": {"A": 45000, "B": 40000, "C": 30000}}})


def revalidate(client: TestClient, trip_id: str, *, user: str = ZHANG, revision: int = 1,
               key: str | None = "rv-1"):
    return client.post(f"/api/trips/{trip_id}/revalidate", json={"revision": revision}, headers=h(user, key))


def approvals(settings: Settings, trip_id: str) -> list[dict[str, Any]]:
    with read_session(settings.db_path) as conn:
        rows = conn.execute("SELECT revision, status, decision FROM approvals WHERE trip_id = ?"
                            " ORDER BY revision", (trip_id,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ AC-024 / S-12


def test_ac024_s12_revalidate_against_v13(client: TestClient, settings: Settings):
    trip_id = pending_s01(client)
    before = get_trip(client, trip_id)
    assert before["policy_version"] == "v1.2" and before["policy_change_notice"] is None
    assert before["plan"]["hotel"]["candidate"]["price_fen"] == 48000
    assert not [v for v in before["violations"] if v["code"] == "HOTEL_OVER_CAP"]

    publish_v13(settings)
    notice = get_trip(client, trip_id)["policy_change_notice"]
    assert notice == {"bound_version": "v1.2", "published_version": "v1.3"}
    # The manager's view shows the same notice; nothing changed until someone revalidates.
    assert get_trip(client, trip_id, LISI)["revision"] == 1

    response = revalidate(client, trip_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["previous_revision"] == 1 and body["outcome"] == "PENDING_APPROVAL"
    trip = body["trip"]
    assert (trip["status"], trip["revision"], trip["policy_version"]) == ("PENDING_APPROVAL", 2, "v1.3")
    assert trip["policy_change_notice"] is None
    (hotel,) = [v for v in trip["violations"] if v["code"] == "HOTEL_OVER_CAP"]
    assert (hotel["actual_value"], hotel["limit_value"], hotel["policy_version"]) == (48000, 45000, "v1.3")
    assert hotel["rule_id"] == "RULE-HOTEL-001" and hotel["quote"] == V13_HOTEL_QUOTE
    # D-12: same candidates, only the binding moved.
    for leg in ("outbound", "return"):
        assert trip["plan"]["transport"][leg] == before["plan"]["transport"][leg]
    assert trip["plan"]["hotel"]["candidate"] == before["plan"]["hotel"]["candidate"]
    assert (trip["plan"]["revision"], trip["plan"]["policy_version"]) == (2, "v1.3")
    assert trip["cost_breakdown"] == before["cost_breakdown"]

    assert approvals(settings, trip_id) == [{"revision": 1, "status": "superseded", "decision": None},
                                            {"revision": 2, "status": "pending", "decision": None}]
    pending = client.get("/api/approvals?status=pending", headers=h(LISI)).json()["approvals"]
    assert [(a["trip_id"], a["revision"]) for a in pending] == [(trip_id, 2)]
    card_codes = [v["code"] for v in pending[0]["card"]["violations"]]
    assert "HOTEL_OVER_CAP" in card_codes

    (event,) = audit_events(client, trip_id, "revalidated")
    payload = event["payload"]
    assert payload["providers_called"] is False
    assert (payload["before"]["policy_version"], payload["after"]["policy_version"]) == ("v1.2", "v1.3")
    assert payload["before"]["revision"] == 1 and payload["after"]["revision"] == 2
    assert not [v for v in payload["before"]["violations"] if v["code"] == "HOTEL_OVER_CAP"]
    assert [v for v in payload["after"]["violations"] if v["code"] == "HOTEL_OVER_CAP"]
    assert payload["reused_candidates"]["hotel"] == before["plan"]["hotel"]["candidate"]["id"]
    assert {"kind": "policy", "id": "v1.2"} in event["refs"] and {"kind": "policy", "id": "v1.3"} in event["refs"]
    assert [e["revision"] for e in audit_events(client, trip_id, "approval_superseded")] == [2]
    bound = audit_events(client, trip_id, "policy_bound")
    assert bound[-1]["payload"] == {"version": "v1.3", "previous": "v1.2", "changed": True}
    rules_runs = audit_events(client, trip_id, "rules_evaluated")
    assert [e["payload"]["policy_version"] for e in rules_runs] == ["v1.2", "v1.3"]
    assert [e["payload"]["source"] for e in rules_runs] == ["policy", "policy"]  # AC-015, both writers
    # No provider was called by the revalidation.
    assert all(e["revision"] == 1 for e in audit_events(client, trip_id, "provider_call"))

    # Replay with the same key returns the stored response without a second revalidation.
    replay = revalidate(client, trip_id)
    assert replay.status_code == 200 and replay.json() == body
    assert len(audit_events(client, trip_id, "revalidated")) == 1
    reused = client.post(f"/api/trips/{trip_id}/revalidate", json={"revision": 2}, headers=h(ZHANG, "rv-1"))
    assert reused.status_code == 422 and error_code(reused) == "IDEMPOTENCY_KEY_REUSED"

    # The old revision can no longer be decided; the new card is decided on the direct path.
    stale = client.post(f"/api/trips/{trip_id}/decision", json={"revision": 1, "decision": "approve"},
                        headers=h(LISI, "d-old"))
    assert stale.status_code == 409 and error_code(stale) == "STALE_REVISION"
    decided = client.post(f"/api/trips/{trip_id}/decision", json={"revision": 2, "decision": "approve"},
                          headers=h(LISI, "d-new"))
    assert decided.status_code == 200, decided.text
    assert decided.json()["trip"]["status"] == "DELIVERED" and decided.json()["approval"]["revision"] == 2
    decisions = audit_events(client, trip_id, "decision_recorded")
    assert [(e["revision"], e["payload"].get("via")) for e in decisions] == [(2, "direct")]
    emails = client.get(f"/api/outbox?trip_id={trip_id}", headers=h(WANG)).json()["emails"]
    assert [(e["kind"], e["revision"]) for e in emails] == [("trip_delivery", 2)]


def test_revalidate_by_the_direct_manager(client: TestClient, settings: Settings):
    trip_id = pending_s01(client)
    publish_v13(settings)
    response = revalidate(client, trip_id, user=LISI, key="rv-mgr")
    assert response.status_code == 200, response.text
    assert response.json()["trip"]["revision"] == 2
    (event,) = audit_events(client, trip_id, "revalidated")
    assert event["actor"] == LISI


def test_revalidate_errors_follow_the_documented_order(client: TestClient, settings: Settings):
    trip_id = pending_s01(client)
    publish_v13(settings)
    url = f"/api/trips/{trip_id}/revalidate"
    assert client.post(url, json={"revision": 1}).status_code == 401
    no_key = client.post(url, json={"revision": 1}, headers=h(ZHANG))
    assert no_key.status_code == 400 and error_code(no_key) == "IDEMPOTENCY_KEY_REQUIRED"
    for bad in ({}, {"revision": "1"}, {"revision": 0}, {"revision": True}):
        response = client.post(url, json=bad, headers=h(ZHANG, "k"))
        assert response.status_code == 422 and error_code(response) == "VALIDATION_ERROR", bad
    missing = client.post("/api/trips/no-such-trip/revalidate", json={"revision": 1}, headers=h(ZHANG, "k"))
    assert missing.status_code == 404
    hidden = revalidate(client, trip_id, user=ZHAO, key="k")
    assert hidden.status_code == 404  # not visible to a manager who is not the direct manager
    admin = revalidate(client, trip_id, user=WANG, key="k")
    assert admin.status_code == 403 and error_code(admin) == "FORBIDDEN"
    stale = revalidate(client, trip_id, revision=2, key="k")
    assert stale.status_code == 409 and error_code(stale) == "STALE_REVISION"
    assert get_trip(client, trip_id)["revision"] == 1 and audit_events(client, trip_id, "revalidated") == []

    client.post(f"/api/trips/{trip_id}/decision", json={"revision": 1, "decision": "approve"},
                headers=h(LISI, "d-1"))
    delivered = revalidate(client, trip_id, key="k2")
    assert delivered.status_code == 409 and error_code(delivered) == "INVALID_STATE"
    assert delivered.json()["error"]["details"]["allowed_states"] == ["PENDING_APPROVAL"]


def test_imp04_revalidate_without_approval_need_auto_approves_and_delivers(client: TestClient,
                                                                           settings: Settings):
    # S-01 within budget: the only approval reason is the v1.2 auto-approval limit (476000 > 300000).
    trip_id = trip_with(client, {**S01, "declared_budget_fen": 500000})
    stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG, key="submit-1")
    reasons = get_trip(client, trip_id)["approval_reasons"]
    assert [r["code"] for r in reasons] == ["OVER_AUTO_APPROVAL_LIMIT"]
    publish_direct(settings, "v9.4-test", values={"RULE-AUTO-001": {"value_fen": 1_000_000}})
    response = revalidate(client, trip_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "AUTO_APPROVED"
    assert (body["trip"]["status"], body["trip"]["revision"]) == ("DELIVERED", 2)
    assert body["trip"]["approval_reasons"] == [] and body["trip"]["violations"] == []
    assert approvals(settings, trip_id) == [{"revision": 1, "status": "superseded", "decision": None}]
    assert client.get("/api/approvals?status=pending", headers=h(LISI)).json()["approvals"] == []
    (auto,) = audit_events(client, trip_id, "auto_approved")
    assert auto["revision"] == 2 and auto["payload"]["via"] == "revalidate"
    assert len(audit_events(client, trip_id, "approval_superseded")) == 1
    statuses = [(e["payload"]["from"], e["payload"]["to"]) for e in audit_events(client, trip_id, "status_changed")
                if e["revision"] == 2]
    assert statuses == [("PENDING_APPROVAL", "VALIDATING"), ("VALIDATING", "PLANNING"),
                        ("PLANNING", "AUTO_APPROVED"), ("AUTO_APPROVED", "DELIVERY_PENDING"),
                        ("DELIVERY_PENDING", "DELIVERED")]
    emails = client.get(f"/api/outbox?trip_id={trip_id}", headers=h(WANG)).json()["emails"]
    assert [(e["kind"], e["revision"]) for e in emails] == [("trip_delivery", 2)]
    assert client.get(f"/api/trips/{trip_id}/itinerary", headers=h(ZHANG)).json()["revision"] == 2


def test_imp04_delivery_failure_after_revalidate_stays_retryable(client: TestClient, settings: Settings):
    trip_id = trip_with(client, {**S01, "declared_budget_fen": 500000})
    stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG, key="submit-1")
    publish_direct(settings, "v9.4-test", values={"RULE-AUTO-001": {"value_fen": 1_000_000}})

    def broken(*_):
        raise OSError("disk full")

    workflow = client.app.state.workflow
    workflow.delivery_fault_hook = broken
    try:
        response = revalidate(client, trip_id)
    finally:
        workflow.delivery_fault_hook = None
    assert response.status_code == 200, response.text
    trip = response.json()["trip"]
    assert response.json()["outcome"] == "AUTO_APPROVED"
    assert (trip["status"], trip["revision"]) == ("DELIVERY_PENDING", 2)
    assert trip["delivery"]["error"].startswith("OSError") and "disk full" not in trip["delivery"]["error"]
    assert client.get(f"/api/outbox?trip_id={trip_id}", headers=h(WANG)).json()["emails"] == []

    retried = client.post(f"/api/trips/{trip_id}/delivery/retry", headers=h(WANG))
    assert retried.status_code == 200 and retried.json()["status"] == "DELIVERED"
    # The stored idempotent response is the final one of the original request, not the retry.
    assert revalidate(client, trip_id).json()["trip"]["status"] == "DELIVERY_PENDING"


def test_d21_on_revalidate_returns_to_collecting_without_a_card(client: TestClient, settings: Settings):
    trip_id = pending_s01(client)
    publish_v13(settings)
    with db_session(settings.db_path) as conn:  # the reporting line changed while the card was pending
        conn.execute("UPDATE employees SET manager_id = NULL WHERE id = ?", (ZHANG,))
    response = revalidate(client, trip_id)
    assert response.status_code == 200, response.text
    trip = response.json()["trip"]
    assert response.json()["outcome"] == "no_approver"
    assert (trip["status"], trip["revision"], trip["plan"]) == ("COLLECTING", 2, None)
    assert approvals(settings, trip_id) == [{"revision": 1, "status": "superseded", "decision": None}]
    (event,) = audit_events(client, trip_id, "no_approver")
    assert event["revision"] == 2 and event["payload"]["policy_version"] == "v1.3"


def test_imp05_revalidate_hitting_a_hard_block_is_policy_blocked_at_the_new_revision(client: TestClient,
                                                                                     settings: Settings):
    trip_id = pending_s01(client)
    publish_direct(settings, "v9.5-test", values={"RULE-DURATION-001": {"days": 2}})
    response = revalidate(client, trip_id)
    assert response.status_code == 200, response.text
    body = response.json()
    trip = body["trip"]
    assert body["outcome"] == "POLICY_BLOCKED"
    assert (trip["status"], trip["revision"], trip["policy_version"]) == ("POLICY_BLOCKED", 2, "v9.5-test")
    assert trip["plan"] is None and trip["cost_breakdown"] is None
    assert [b["code"] for b in trip["blocks"]] == ["DURATION_EXCEEDED"]
    assert approvals(settings, trip_id) == [{"revision": 1, "status": "superseded", "decision": None}]
    (event,) = audit_events(client, trip_id, "revalidated")
    assert event["payload"]["previous_plan"]["hotel"]["candidate"]["price_fen"] == 48000
    assert event["payload"]["after"]["blocks"][0]["code"] == "DURATION_EXCEEDED"
    assert audit_events(client, trip_id, "validation_blocked")[-1]["revision"] == 2


# ------------------------------------------------------------------ #24 preferences


def test_preferences_get_put_delete_with_audit(client: TestClient, settings: Settings):
    url = f"/api/users/{ZHANG}/preferences"
    assert client.get(url, headers=h(ZHANG)).json() == {"user_id": ZHANG, "preferences": {}}
    put = client.put(url, json={"avoid_early_departure": True, "notes": "  靠窗   座位 "}, headers=h(ZHANG))
    assert put.status_code == 200, put.text
    assert put.json() == {"user_id": ZHANG, "preferences": {"avoid_early_departure": True, "notes": "靠窗 座位"}}
    assert client.get(url, headers=h(ZHANG)).json() == put.json()
    users = {u["id"]: u for u in client.get("/api/users").json()["users"]}
    assert users[ZHANG]["preferences"] == put.json()["preferences"]

    deleted = client.delete(url, headers=h(ZHANG))
    assert deleted.status_code == 200 and deleted.json() == {"user_id": ZHANG, "preferences": {}}

    with read_session(settings.db_path) as conn:
        rows = conn.execute("SELECT trip_id, actor, payload_json FROM audit_events"
                            " WHERE type = 'preferences_changed' ORDER BY seq").fetchall()
    payloads = [json.loads(r["payload_json"]) for r in rows]
    assert [r["trip_id"] for r in rows] == [None, None] and {r["actor"] for r in rows} == {ZHANG}
    assert [(p["action"], p["before"], p["after"]) for p in payloads] == [
        ("put", {}, {"avoid_early_departure": True, "notes": "靠窗 座位"}),
        ("delete", {"avoid_early_departure": True, "notes": "靠窗 座位"}, {}),
    ]


def test_preferences_are_only_for_oneself_and_validated(client: TestClient):
    url = f"/api/users/{ZHANG}/preferences"
    assert client.get(url).status_code == 401
    for actor in (LISI, ZHAO, WANG):
        for method in ("get", "delete"):
            response = getattr(client, method)(url, headers=h(actor))
            assert response.status_code == 403 and error_code(response) == "FORBIDDEN", (actor, method)
        response = client.put(url, json={"avoid_early_departure": True}, headers=h(actor))
        assert response.status_code == 403, actor
    admin_self = client.get(f"/api/users/{WANG}/preferences", headers=h(WANG))
    assert admin_self.status_code == 403
    assert client.get("/api/users/u-nobody/preferences", headers=h(ZHANG)).status_code == 404
    manager_self = client.put(f"/api/users/{LISI}/preferences", json={"avoid_early_departure": False},
                              headers=h(LISI))
    assert manager_self.status_code == 200
    for bad in ({"avoid_early_departure": "yes"}, {"seat": "window"}, {"notes": "x" * 201}, [1], None):
        response = client.put(url, json=bad, headers=h(ZHANG))
        assert response.status_code == 422, bad
        assert error_code(response) in ("INVALID_PREFERENCES", "VALIDATION_ERROR"), bad
    # Format (422) is judged before identity (403).
    other = client.put(url, json={"seat": "window"}, headers=h(LISI))
    assert other.status_code == 422
    assert client.get(url, headers=h(ZHANG)).json()["preferences"] == {}


def _ac023_trip(client: TestClient) -> dict[str, Any]:
    trip_id = client.post("/api/trips", json={}, headers=h(ZHANG)).json()["trip"]["id"]
    patched = client.patch(f"/api/trips/{trip_id}/slots", json={"expected_revision": 1, "slots": AC023},
                           headers=h(ZHANG))
    assert patched.status_code == 200, patched.text
    events = stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG,
                    key=f"submit-{trip_id}")
    assert events[-1]["type"] == "done"
    return get_trip(client, trip_id)


def test_ac023_three_phases_over_http(client: TestClient):
    url = f"/api/users/{ZHANG}/preferences"
    neutral = _ac023_trip(client)
    assert neutral["status"] == "DELIVERED" and neutral["cost_breakdown"]["total_fen"] == 190000
    assert neutral["plan"]["transport"]["outbound"]["id"] == "T-BJS-SHA-1105-EARLY"
    assert neutral["preference_source"] == "none" and neutral["plan"]["ranking"]["preference_applied"] is False

    assert client.put(url, json={"avoid_early_departure": True}, headers=h(ZHANG)).status_code == 200
    # D-20: a trip already submitted keeps the snapshot it was planned with.
    assert get_trip(client, neutral["id"])["preferences_used"] == {}
    avoid = _ac023_trip(client)
    assert avoid["status"] == "DELIVERED" and avoid["cost_breakdown"]["total_fen"] == 190000
    assert avoid["plan"]["transport"]["outbound"]["id"] == "T-BJS-SHA-1105-LATE"
    assert avoid["preference_source"] == "personal_snapshot"
    assert avoid["preferences_used"] == {"avoid_early_departure": True}
    ranking = audit_events(client, avoid["id"], "candidates_selected")[0]["payload"]["ranking"]
    assert ranking["preference_applied"] is True and ranking["preference_affected"] is True
    snapshot = audit_events(client, avoid["id"], "preference_snapshot")[0]["payload"]
    assert snapshot == {"preference_source": "personal_snapshot", "preferences": {"avoid_early_departure": True}}

    assert client.delete(url, headers=h(ZHANG)).status_code == 200
    cleared = _ac023_trip(client)
    assert cleared["plan"]["transport"]["outbound"]["id"] == "T-BJS-SHA-1105-EARLY"
    assert cleared["preference_source"] == "none"
    ranking = audit_events(client, cleared["id"], "candidates_selected")[0]["payload"]["ranking"]
    assert ranking["preference_applied"] is False and ranking["preference_affected"] is False
