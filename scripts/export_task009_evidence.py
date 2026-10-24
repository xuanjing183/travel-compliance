"""Export TASK-009a evidence: AC-023 (preferences over HTTP) and AC-024 / S-12 (revalidate).

Runs the real app in-process (`TestClient`) with `llm_mode=fake` on a throwaway data
directory: no network, no model calls. Every expectation is checked; exit code 0 only if
all pass. Usage:

    uv run --offline python scripts/export_task009_evidence.py artifacts/acceptance/TASK-009

Limitation (stated in the README too): the publish API (#20–#23) is TASK-008. Until then v1.3
is written straight into the throwaway database by `publish_direct` — same rules as v1.2 except
RULE-HOTEL-001 A = 45000 fen/night, quote and locator taken from tests/fixtures/policies/v1.3.md,
old published version archived in the same transaction. S-12 through the admin publish flow
(#20–#23) is in scripts/export_task008_evidence.py.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from travel_desk.config import get_settings
from travel_desk.db.database import db_session, read_session
from travel_desk.db.seed import QUOTES_V12, _rules_for, locate_line
from travel_desk.main import create_app

ZHANG, LISI, WANG = "u-zhangsan", "u-lisi", "u-wangwu"
S01 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
       "purpose": "供应商大会", "declared_budget_fen": 400000}
AC023 = {"origin": "北京", "destination": "上海", "start_date": "2026-11-05", "end_date": "2026-11-06",
         "purpose": "客户拜访", "declared_budget_fen": 250000}
V13_HOTEL_QUOTE = "员工住宿上限：A 类城市每晚上限 450 元；B 类城市每晚上限 400 元；C 类城市每晚上限 300 元。"


def h(user: str, key: str | None = None) -> dict[str, str]:
    headers = {"X-Demo-User": user}
    if key:
        headers["Idempotency-Key"] = key
    return headers


class Run:
    def __init__(self, client: TestClient, settings, out: Path):
        self.client, self.settings, self.out = client, settings, out
        self.checks: list[dict[str, Any]] = []

    def check(self, ac: str, name: str, ok: bool, observed: Any = None) -> None:
        self.checks.append({"ac": ac, "check": name, "result": "PASS" if ok else "FAIL",
                            "observed": observed})

    def write(self, rel: str, data: Any) -> None:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def trip(self, trip_id: str, user: str = ZHANG) -> dict[str, Any]:
        return self.client.get(f"/api/trips/{trip_id}", headers=h(user)).json()

    def audit(self, trip_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        events = self.client.get(f"/api/trips/{trip_id}/audit", headers=h(ZHANG)).json()["events"]
        return [e for e in events if kind is None or e["type"] == kind]

    def submitted_trip(self, slots: dict[str, Any]) -> str:
        trip_id = self.client.post("/api/trips", json={}, headers=h(ZHANG)).json()["trip"]["id"]
        self.client.patch(f"/api/trips/{trip_id}/slots", json={"expected_revision": 1, "slots": slots},
                          headers=h(ZHANG))
        response = self.client.post(f"/api/trips/{trip_id}/submit", json={"expected_revision": 1},
                                    headers=h(ZHANG, f"submit-{trip_id}"))
        assert response.status_code == 200, response.text
        return trip_id

    def approvals(self, trip_id: str) -> list[dict[str, Any]]:
        with read_session(self.settings.db_path) as conn:
            rows = conn.execute("SELECT id, revision, status, decision, card_json FROM approvals"
                                " WHERE trip_id = ? ORDER BY revision", (trip_id,)).fetchall()
        return [{**{k: r[k] for k in ("id", "revision", "status", "decision")},
                 "card": json.loads(r["card_json"])} for r in rows]


def publish_v13_direct(settings) -> None:
    path = settings.policies_dir / "v1.3.md"
    text = path.read_text(encoding="utf-8")
    now = "2026-10-20T00:00:00+00:00"
    quotes = {**QUOTES_V12, "hotel_employee": V13_HOTEL_QUOTE}
    with db_session(settings.db_path) as conn:
        conn.execute("UPDATE policy_versions SET status = 'archived' WHERE status = 'published'")
        conn.execute("INSERT INTO policy_versions (version, status, effective_date, document_path,"
                     " document_hash, created_at, published_at)"
                     " VALUES ('v1.3', 'published', '2026-11-01', ?, ?, ?, ?)",
                     ("tests/fixtures/policies/v1.3.md", hashlib.sha256(path.read_bytes()).hexdigest(),
                      now, now))
        for rule in _rules_for("v1.3", quotes):
            value = ({"caps_fen": {"A": 45000, "B": 40000, "C": 30000}} if rule["id"] == "RULE-HOTEL-001"
                     else rule["value"])
            conn.execute("INSERT INTO policy_rules (version, id, type, condition_json, value_json, unit,"
                         " clause, quote, locator, quote_status, status, confirmed_by, created_at,"
                         " updated_at)"
                         " VALUES ('v1.3',?,?,?,?,?,?,?,?,'valid','confirmed','u-wangwu',?,?)",
                         (rule["id"], rule["type"], json.dumps(rule["condition"], ensure_ascii=False),
                          json.dumps(value, ensure_ascii=False), rule["unit"], rule["clause"], rule["quote"],
                          locate_line(text, rule["quote"]), now, now))


def ac023(run: Run) -> None:
    url = f"/api/users/{ZHANG}/preferences"
    phases = {}
    for phase, action in (("1-none", None), ("2-avoid-early", "put"), ("3-cleared", "delete")):
        pref_response = None
        if action == "put":
            pref_response = run.client.put(url, json={"avoid_early_departure": True}, headers=h(ZHANG)).json()
        elif action == "delete":
            pref_response = run.client.delete(url, headers=h(ZHANG)).json()
        trip_id = run.submitted_trip(AC023)
        trip = run.trip(trip_id)
        phases[phase] = {"preferences_api": pref_response, "trip_id": trip_id, "status": trip["status"],
                         "outbound": trip["plan"]["transport"]["outbound"]["id"],
                         "total_fen": trip["cost_breakdown"]["total_fen"],
                         "preference_source": trip["preference_source"],
                         "preferences_used": trip["preferences_used"], "ranking": trip["plan"]["ranking"],
                         "audit": [e for e in run.audit(trip_id)
                                   if e["type"] in ("preference_snapshot", "candidates_selected")]}
        run.write(f"AC-023/{phase}-plan.json", trip["plan"])
    run.write("AC-023/phases.json", phases)
    with read_session(run.settings.db_path) as conn:
        rows = conn.execute("SELECT seq, ts, actor, type, payload_json FROM audit_events"
                            " WHERE type = 'preferences_changed' ORDER BY seq").fetchall()
    pref_audit = [{**{k: r[k] for k in ("seq", "ts", "actor", "type")},
                   "payload": json.loads(r["payload_json"])} for r in rows]
    run.write("AC-023/preferences-audit.json", pref_audit)
    one, two, three = phases["1-none"], phases["2-avoid-early"], phases["3-cleared"]
    run.check("AC-023", "no preference → stable secondary key (EARLY)",
              one["outbound"] == "T-BJS-SHA-1105-EARLY" and one["preference_source"] == "none",
              one["outbound"])
    run.check("AC-023", "avoid early (personal, PUT #24) → LATE, ranking says the preference decided it",
              two["outbound"] == "T-BJS-SHA-1105-LATE" and two["preference_source"] == "personal_snapshot"
              and two["ranking"]["preference_applied"] and two["ranking"]["preference_affected"],
              [two["outbound"], two["ranking"]["preference_affected"]])
    run.check("AC-023", "cleared (DELETE #24) → back to EARLY, preference not applied",
              three["outbound"] == "T-BJS-SHA-1105-EARLY" and not three["ranking"]["preference_applied"],
              three["outbound"])
    run.check("AC-023", "all three: total 190000 and AUTO_APPROVED→DELIVERED",
              all(p["total_fen"] == 190000 and p["status"] == "DELIVERED" for p in phases.values()),
              [(p["total_fen"], p["status"]) for p in phases.values()])
    first_snapshot = run.trip(one["trip_id"])["preferences_used"]
    run.check("AC-023", "D-20: earlier trip keeps its snapshot after the change", first_snapshot == {},
              first_snapshot)
    run.check("AC-023", "preferences_changed audited (put, delete)",
              [p["payload"]["action"] for p in pref_audit] == ["put", "delete"],
              [p["payload"]["action"] for p in pref_audit])


def ac024(run: Run) -> None:
    trip_id = run.submitted_trip(S01)
    before = run.trip(trip_id)
    cards_before = run.approvals(trip_id)
    publish_v13_direct(run.settings)
    notice = run.trip(trip_id)
    url = f"/api/trips/{trip_id}/revalidate"
    response = run.client.post(url, json={"revision": 1}, headers=h(ZHANG, "rv-1"))
    body = response.json()
    replay = run.client.post(url, json={"revision": 1}, headers=h(ZHANG, "rv-1"))
    after = run.trip(trip_id)
    cards_after = run.approvals(trip_id)
    pending = run.client.get("/api/approvals?status=pending", headers=h(LISI)).json()["approvals"]
    stale = run.client.post(f"/api/trips/{trip_id}/decision", json={"revision": 1, "decision": "approve"},
                            headers=h(LISI, "d-old"))
    audit = run.audit(trip_id)
    run.write("AC-024/trip-before.json", before)
    run.write("AC-024/approval-cards-before.json", cards_before)
    run.write("AC-024/trip-with-notice.json", {"policy_change_notice": notice["policy_change_notice"],
                                                "revision": notice["revision"], "status": notice["status"]})
    run.write("AC-024/revalidate-response.json", {"status_code": response.status_code, "body": body})
    run.write("AC-024/trip-after.json", after)
    run.write("AC-024/approval-cards-after.json", cards_after)
    run.write("AC-024/audit.json", audit)
    hotel = [v for v in after["violations"] if v["code"] == "HOTEL_OVER_CAP"]
    (revalidated,) = [e for e in audit if e["type"] == "revalidated"]
    judged = (revalidated["payload"]["before"]["policy_version"],
              revalidated["payload"]["after"]["policy_version"])
    run.check("AC-024", "notice shown while bound v1.2 ≠ published v1.3",
              notice["policy_change_notice"] == {"bound_version": "v1.2", "published_version": "v1.3"},
              notice["policy_change_notice"])
    run.check("AC-024", "revision 1→2, policy_version v1.3, still PENDING_APPROVAL",
              (after["revision"], after["policy_version"], after["status"]) == (2, "v1.3", "PENDING_APPROVAL")
              and body["previous_revision"] == 1,
              [after["revision"], after["policy_version"], after["status"]])
    run.check("AC-024", "old card superseded, new pending card at revision 2",
              [(c["revision"], c["status"]) for c in cards_after] == [(1, "superseded"), (2, "pending")]
              and [(a["trip_id"], a["revision"]) for a in pending] == [(trip_id, 2)],
              [(c["revision"], c["status"]) for c in cards_after])
    run.check("AC-024", "new violation HOTEL_OVER_CAP 48000 > 45000 (RULE-HOTEL-001 v1.3)",
              len(hotel) == 1
              and (hotel[0]["actual_value"], hotel[0]["limit_value"], hotel[0]["policy_version"])
              == (48000, 45000, "v1.3"), hotel)
    run.check("AC-024", "same candidates reused, no provider call",
              after["plan"]["hotel"]["candidate"] == before["plan"]["hotel"]["candidate"]
              and after["plan"]["transport"] == before["plan"]["transport"]
              and revalidated["payload"]["providers_called"] is False
              and all(e["revision"] == 1 for e in audit if e["type"] == "provider_call"),
              revalidated["payload"]["reused_candidates"])
    run.check("AC-024", "audit keeps both judgements (v1.2 before, v1.3 after)",
              judged == ("v1.2", "v1.3")
              and [e["payload"]["policy_version"] for e in audit if e["type"] == "rules_evaluated"]
              == ["v1.2", "v1.3"], judged)
    run.check("AC-024", "same Idempotency-Key replays; old revision decision → 409 STALE_REVISION",
              replay.json() == body and stale.status_code == 409
              and stale.json()["error"]["code"] == "STALE_REVISION", stale.status_code)


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="task009-evidence-")) / "var"
    settings = replace(get_settings(), llm_mode="fake", data_dir=work, db_path=work / "app.sqlite",
                       checkpoint_db_path=work / "checkpoints.sqlite", upload_dir=work / "uploads")
    with TestClient(create_app(settings)) as client:
        run = Run(client, settings, out)
        ac023(run)
        ac024(run)
    failed = [c for c in run.checks if c["result"] != "PASS"]
    summary = {"mode": "fake (deterministic parser); no network; v1.3 published by test-only direct insert",
               "checks": run.checks, "passed": len(run.checks) - len(failed), "failed": len(failed)}
    run.write("summary.json", summary)
    for c in run.checks:
        print(f"{c['result']}  {c['ac']}  {c['check']}")
    print(f"{summary['passed']} passed, {summary['failed']} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/acceptance/TASK-009")))
