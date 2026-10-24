"""AC-015 / REQ-024: every datum the user sees says where it came from.

Runs S-01 (approve → delivered), S-03 (backup hotel, pending) and one policy question over HTTP
and scans the JSON of every endpoint that shows such data — trip, approval list, itinerary,
audit and the answer — for `source` / `sources`. The scan fails on any value outside the
§6.1 enum, on a null outside the employee-entered itinerary rows (IMP-26), and on provider,
policy or model data that carries no source at all ("无来源数据判失败"). The browser view of the
same data is in artifacts/acceptance/TASK-007 (screenshots 08, 11, 12).
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from test_api import LISI, S01, ZHANG, h, stream, trip_with

ENUM = {"demo_fixture", "backup_fixture", "live", "policy", "model"}
PROVIDER = {"demo_fixture", "backup_fixture", "live"}
S03 = {"origin": "北京", "destination": "成都", "start_date": "2026-10-27", "end_date": "2026-10-29",
       "purpose": "培训", "declared_budget_fen": 400000}


def scan(doc: Any) -> tuple[set[str], list[str]]:
    """Return (sources seen, problems). Paths look like `$.plan.itinerary[].items[]`."""
    seen: set[str] = set()
    problems: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, path + "[]")
            return
        if not isinstance(node, dict):
            return
        if "source" in node:
            value = node["source"]
            if value is None:
                if not path.endswith("itinerary[].items[]"):
                    problems.append(f"{path}: null source")
            elif value not in ENUM:
                problems.append(f"{path}: source {value!r} not in enum")
            else:
                seen.add(value)
        if "sources" in node:
            values = node["sources"]
            if not values or not set(values) <= ENUM:
                problems.append(f"{path}: sources {values!r}")
            seen.update(v for v in values or () if v in ENUM)
        # Data that must say where it came from, whatever key it sits under.
        if "price_fen" in node and node.get("source") not in PROVIDER:
            problems.append(f"{path}: priced candidate without provider source")
        if "temp_low_c" in node and node.get("source") not in PROVIDER:
            problems.append(f"{path}: weather without provider source")
        if "quote" in node and "locator" in node and node.get("source") != "policy":
            problems.append(f"{path}: policy quote without source=policy")
        if "rule_id" in node and "code" in node and node.get("source") != "policy":
            problems.append(f"{path}: violation without source=policy")
        if "generator" in node and "status" in node and node.get("source") != "model":
            problems.append(f"{path}: summary without source=model")
        for key, value in node.items():
            walk(value, f"{path}.{key}")

    walk(doc, "$")
    return seen, problems


def checked(doc: Any) -> set[str]:
    seen, problems = scan(doc)
    assert problems == [], problems
    return seen


def get(client: TestClient, url: str, user: str = ZHANG) -> Any:
    res = client.get(url, headers=h(user))
    assert res.status_code == 200, res.text
    return res.json()


def test_scanner_rejects_unsourced_and_unknown_data():
    # The checker itself must fail on the cases AC-015 names, or a green run proves nothing.
    _, problems = scan({"plan": {"transport": {"outbound": {"id": "T-1", "price_fen": 100}},
                                 "weather": {"days": [{"temp_low_c": 1, "source": "web"}]},
                                 "summary": {"status": "ok", "generator": "fake", "source": None}},
                        "violations": [{"code": "X", "rule_id": "R", "evidence": {"quote": "q", "locator": "l"}}],
                        "card": {"sources": []}})
    # 1 unsourced price, 2 for the "web" weather (not in enum, not a provider), 2 for the null
    # summary, 1 violation, 1 quote, 1 empty card list.
    assert len(problems) == 8, problems
    assert checked({"plan": {"itinerary": [{"items": [{"title": "会议", "source": None}]}]}}) == set()


def test_s01_s03_and_answer_carry_sources_in_api_view_and_audit(client: TestClient):
    s01 = trip_with(client, {**S01, "meeting_address": "上海市浦东新区滨江大道2727号"})
    stream(client, f"/api/trips/{s01}/submit", {"expected_revision": 1}, user=ZHANG, key="src-s01")
    s03 = trip_with(client, {**S03, "meeting_address": "成都市高新区天府大道1000号"})
    stream(client, f"/api/trips/{s03}/submit", {"expected_revision": 1}, user=ZHANG, key="src-s03")

    pending = get(client, "/api/approvals?status=pending", LISI)["approvals"]
    cards = {a["trip_id"]: a["card"] for a in pending}
    assert set(cards) == {s01, s03}
    assert checked(pending) == {"demo_fixture", "backup_fixture", "policy", "model"}
    # The card's list summarises the plan it was built from (weather included, which the card does
    # not repeat), plus `policy` because every card is a rule judgement.
    for trip_id, card in cards.items():
        plan = get(client, f"/api/trips/{trip_id}")["plan"]
        assert set(card["sources"]) == checked(plan) | {"policy"}, trip_id
    assert cards[s01]["sources"] == ["demo_fixture", "model", "policy"]
    assert cards[s03]["sources"] == ["backup_fixture", "demo_fixture", "model", "policy"]

    approve = client.post(f"/api/trips/{s01}/decision", json={"revision": 1, "decision": "approve"},
                          headers=h(LISI, "src-d1"))
    assert approve.status_code == 200, approve.text

    trip01, trip03 = get(client, f"/api/trips/{s01}"), get(client, f"/api/trips/{s03}")
    assert trip01["status"] == "DELIVERED" and trip03["status"] == "PENDING_APPROVAL"
    assert {"demo_fixture", "policy", "model"} <= checked(trip01)
    assert checked(trip03) >= {"backup_fixture", "demo_fixture", "policy", "model"}
    assert trip03["plan"]["hotel"]["candidate"]["source"] == "backup_fixture"
    assert [v["source"] for v in trip03["violations"]] == ["policy"]

    itinerary = get(client, f"/api/trips/{s01}/itinerary")
    assert checked(itinerary) >= {"demo_fixture", "model"}
    rows = [i for day in itinerary["itinerary"]["plan"]["itinerary"] for i in day["items"]]
    assert {i["source"] for i in rows} >= {"demo_fixture", None}  # None = employee-entered rows (IMP-26)

    for trip_id, expected in ((s01, {"demo_fixture", "model", "policy"}),
                              (s03, {"backup_fixture", "demo_fixture", "model", "policy"})):
        events = get(client, f"/api/trips/{trip_id}/audit")["events"]
        assert expected <= checked(events), trip_id
        plans = [e for e in events if e["type"] == "plan_created"]
        assert plans, trip_id
        # The judgement and the card request name their own sources, so even a trip without a
        # violation shows `policy` in its audit (not only inside the plan snapshot).
        [judged] = [e for e in events if e["type"] == "rules_evaluated"]
        assert judged["payload"]["source"] == "policy"
        [requested] = [e for e in events if e["type"] == "approval_requested"]
        assert requested["payload"]["sources"] == cards[trip_id]["sources"]

    answer = client.post("/api/policy/ask", json={"question": "上海住宿标准是多少"}, headers=h(ZHANG))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["answerable"] is True and body["citations"]
    assert body["source"] == "model" and body["generator"] == "fake:extractive"
    assert checked(body) == {"model", "policy"}
    assert {c["source"] for c in body["citations"]} == {"policy"}

    # Demo mode never claims live data (AUTH-02: no Live provider is configured).
    for doc in (pending, trip01, trip03, itinerary, body):
        assert "live" not in checked(doc)
