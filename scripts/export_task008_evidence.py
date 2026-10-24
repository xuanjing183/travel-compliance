"""Export TASK-008a evidence: AC-005 (policy upload → extract → review → publish) and
AC-024 / S-12 with v1.3 created and published through the admin API (#20–#23).

Runs the real app in-process (`TestClient`) with `llm_mode=fake` on two throwaway data
directories (one per AC, so the AC-005 test versions never touch the S-12 database): no
network, no model calls. Rule extraction uses the recorded output of §7.1.1
(`tests/fixtures/recorded/extract-v1.*.json`, `source: hand_written`). Every expectation is
checked; exit code 0 only if all pass. Usage:

    uv run --offline python scripts/export_task008_evidence.py artifacts/acceptance/TASK-008
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from travel_desk.config import get_settings
from travel_desk.db.database import read_session
from travel_desk.main import create_app

ZHANG, LISI, WANG = "u-zhangsan", "u-lisi", "u-wangwu"
S01 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
       "purpose": "供应商大会", "declared_budget_fen": 400000}
V13_HOTEL_QUOTE = "员工住宿上限：A 类城市每晚上限 450 元；B 类城市每晚上限 400 元；C 类城市每晚上限 300 元。"
CLOSED_IDS = ["RULE-CITY-001", "RULE-HOTEL-001", "RULE-HOTEL-002", "RULE-MEAL-001", "RULE-CLASS-001",
              "RULE-ADVANCE-001", "RULE-AUTO-001", "RULE-DURATION-001"]
INVALID_ID, UNSUPPORTED_ID = "RULE-DURATION-002", "RULE-OTHER-001"


def h(user: str, key: str | None = None) -> dict[str, str]:
    headers = {"X-Demo-User": user}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def settings_for(name: str):
    work = Path(tempfile.mkdtemp(prefix=f"task008-{name}-")) / "var"
    return replace(get_settings(), llm_mode="fake", data_dir=work, db_path=work / "app.sqlite",
                   checkpoint_db_path=work / "checkpoints.sqlite", upload_dir=work / "uploads")


class Run:
    def __init__(self, client: TestClient, settings, out: Path, checks: list[dict[str, Any]]):
        self.client, self.settings, self.out, self.checks = client, settings, out, checks

    def check(self, ac: str, name: str, ok: bool, observed: Any = None) -> None:
        self.checks.append({"ac": ac, "check": name, "result": "PASS" if ok else "FAIL",
                            "observed": observed})

    def write(self, rel: str, data: Any) -> None:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # --- policy API
    def upload(self, version: str, document: str, *, user: str = WANG):
        content = (self.settings.policies_dir / document).read_bytes()
        return self.client.post("/api/policies", headers=h(user),
                                data={"version": version, "effective_date": "2026-11-01"},
                                files={"file": (document, content, "application/octet-stream")})

    def extract(self, version: str, body: dict | None = None):
        return self.client.post(f"/api/policies/{version}/extract", json=body or {}, headers=h(WANG))

    def patch(self, version: str, rule_id: str, body: dict[str, Any]):
        return self.client.patch(f"/api/policies/{version}/rules/{rule_id}", json=body, headers=h(WANG))

    def publish(self, version: str, key: str | None, user: str = WANG):
        return self.client.post(f"/api/policies/{version}/publish", headers=h(user, key))

    # --- database views
    def snapshot(self) -> dict[str, Any]:
        with read_session(self.settings.db_path) as conn:
            versions = [dict(r) for r in conn.execute(
                "SELECT version, status, effective_date, document_path, document_hash, published_at"
                " FROM policy_versions ORDER BY version")]
            rules = [{**{k: r[k] for k in ("version", "id", "type", "unit", "clause", "quote", "locator",
                                           "quote_status", "status", "confirmed_by", "modification_reason")},
                      "condition": json.loads(r["condition_json"]), "value": json.loads(r["value_json"])}
                     for r in conn.execute("SELECT * FROM policy_rules ORDER BY version, rowid")]
        for row in versions:  # throwaway temp dir → stable placeholder (no local user path in evidence)
            row["document_path"] = row["document_path"].replace(str(self.settings.data_dir), "<data_dir>")
        return {"policy_versions": versions, "policy_rules": rules}

    def policy_audit(self) -> list[dict[str, Any]]:
        with read_session(self.settings.db_path) as conn:
            rows = conn.execute("SELECT seq, ts, actor, type, payload_json, refs_json FROM audit_events"
                                " WHERE trip_id IS NULL AND type LIKE 'policy_%' ORDER BY seq").fetchall()
        return [{**{k: r[k] for k in ("seq", "ts", "actor", "type")},
                 "payload": json.loads(r["payload_json"]), "refs": json.loads(r["refs_json"])} for r in rows]

    def statuses(self) -> dict[str, str]:
        return {v["version"]: v["status"] for v in self.snapshot()["policy_versions"]}

    # --- trips
    def trip(self, trip_id: str, user: str = ZHANG) -> dict[str, Any]:
        return self.client.get(f"/api/trips/{trip_id}", headers=h(user)).json()

    def audit(self, trip_id: str) -> list[dict[str, Any]]:
        return self.client.get(f"/api/trips/{trip_id}/audit", headers=h(ZHANG)).json()["events"]

    def approvals(self, trip_id: str) -> list[dict[str, Any]]:
        with read_session(self.settings.db_path) as conn:
            rows = conn.execute("SELECT id, revision, status, decision, card_json FROM approvals"
                                " WHERE trip_id = ? ORDER BY revision", (trip_id,)).fetchall()
        return [{**{k: r[k] for k in ("id", "revision", "status", "decision")},
                 "card": json.loads(r["card_json"])} for r in rows]


def code(response) -> str | None:
    try:
        return response.json()["error"]["code"]
    except (KeyError, TypeError, ValueError):
        return None


def exchange(response) -> dict[str, Any]:
    return {"status_code": response.status_code, "body": response.json()}


def ac005(run: Run) -> None:
    ac = "AC-005"
    run.write("AC-005/db-before.json", run.snapshot())
    md, pdf, scanned = (run.upload("v9.1-test", "v1.2.md"), run.upload("v9.2-test", "v1.2-text.pdf"),
                        run.upload("v9.3-test", "scanned.pdf"))
    run.write("AC-005/01-upload.json", {"markdown": exchange(md), "text_pdf": exchange(pdf),
                                        "scanned_pdf": exchange(scanned)})
    run.check(ac, "MD and text PDF → 201 draft; scanned PDF → 415 SCANNED_PDF",
              (md.status_code, md.json()["status"], pdf.status_code, pdf.json()["status"],
               pdf.json()["document"]["kind"], scanned.status_code, code(scanned))
              == (201, "draft", 201, "draft", "pdf", 415, "SCANNED_PDF"),
              [md.status_code, pdf.status_code, scanned.status_code, code(scanned)])
    run.check(ac, "independent test versions; v1.0/v1.2 untouched, no v1.3 created",
              run.statuses() == {"v1.0": "archived", "v1.2": "published", "v9.1-test": "draft",
                                 "v9.2-test": "draft"}, run.statuses())

    extracted = run.extract("v9.1-test")
    extracted_pdf = run.extract("v9.2-test")
    run.write("AC-005/02-extract-markdown.json", exchange(extracted))
    run.write("AC-005/02-extract-pdf.json", exchange(extracted_pdf))
    cands = {c["id"]: c for c in extracted.json()["candidates"]}
    run.check(ac, "recorded extraction (§7.1.1): 8 closed candidates + invalid_quote + unsupported",
              extracted.json()["generator"] == "recorded:hand_written"
              and list(cands) == CLOSED_IDS + [INVALID_ID, UNSUPPORTED_ID]
              and cands[INVALID_ID]["quote_status"] == "invalid_quote"
              and cands[UNSUPPORTED_ID]["status"] == "unsupported",
              {k: (c["status"], c["quote_status"]) for k, c in cands.items()})
    run.check(ac, "quotes located in the document: Markdown line:N, PDF page:N",
              all(cands[i]["locator"].startswith("line:") for i in CLOSED_IDS)
              and {c["locator"] for c in extracted_pdf.json()["candidates"] if c["quote_status"] == "valid"}
              == {"page:1", "page:2"},
              [cands["RULE-HOTEL-001"]["locator"]])

    early = run.publish("v9.1-test", "pub-early")
    confirm_invalid = run.patch("v9.1-test", INVALID_ID, {"status": "confirmed"})
    run.write("AC-005/03-publish-before-review.json", exchange(early))
    run.write("AC-005/04-confirm-invalid-quote.json", exchange(confirm_invalid))
    run.check(ac, "invalid_quote candidate cannot be confirmed → 422 QUOTE_INVALID",
              (confirm_invalid.status_code, code(confirm_invalid)) == (422, "QUOTE_INVALID"),
              code(confirm_invalid))
    run.check(ac, "publish with unprocessed candidates → 409 UNPROCESSED_CANDIDATES",
              (early.status_code, code(early)) == (409, "UNPROCESSED_CANDIDATES")
              and early.json()["error"]["details"]["rule_ids"] == CLOSED_IDS + [INVALID_ID],
              early.json()["error"]["details"])

    reviews = {}
    for rule_id in CLOSED_IDS:
        status = "rejected" if rule_id == "RULE-MEAL-001" else "confirmed"
        reviews[rule_id] = run.patch("v9.1-test", rule_id, {"status": status}).status_code
    reviews[INVALID_ID] = run.patch("v9.1-test", INVALID_ID, {"status": "rejected"}).status_code
    missing = run.publish("v9.1-test", "pub-missing")
    run.write("AC-005/05-publish-missing-closed-type.json", exchange(missing))
    run.check(ac, "closed type without a confirmed rule → 422 MISSING_RULE_TYPES",
              (missing.status_code, code(missing)) == (422, "MISSING_RULE_TYPES")
              and missing.json()["error"]["details"]["missing_types"] == ["meal_allowance"],
              missing.json()["error"]["details"].get("missing_types"))
    run.check(ac, "failed publish attempts changed nothing", run.statuses()["v1.2"] == "published"
              and run.statuses()["v9.1-test"] == "draft", run.statuses())

    reviews["RULE-MEAL-001"] = run.patch("v9.1-test", "RULE-MEAL-001", {"status": "confirmed"}).status_code
    done = run.publish("v9.1-test", "pub-1")
    replay = run.publish("v9.1-test", "pub-1")
    run.write("AC-005/06-review-status-codes.json", reviews)
    run.write("AC-005/07-publish.json", {"publish": exchange(done), "replay_same_key": exchange(replay)})
    run.write("AC-005/db-after.json", run.snapshot())
    audit = run.policy_audit()
    run.write("AC-005/audit.json", audit)
    run.check(ac, "atomic publish: v9.1-test published, v1.2 archived, exactly one published",
              done.status_code == 200 and done.json()["archived_version"] == "v1.2"
              and run.statuses() == {"v1.0": "archived", "v1.2": "archived", "v9.1-test": "published",
                                     "v9.2-test": "draft"}, run.statuses())
    run.check(ac, "publish is idempotent per key (same response replayed)",
              replay.status_code == 200 and replay.json() == done.json(), replay.status_code)
    types = [e["type"] for e in audit]
    published = [e for e in audit if e["type"] == "policy_published"]
    run.check(ac, "audit: uploaded ×2, extracted ×2, rule updates, one published event by the admin",
              types.count("policy_uploaded") == 2 and types.count("policy_extracted") == 2
              and types.count("policy_rule_updated") == len(CLOSED_IDS) + 2 and len(published) == 1
              and published[0]["actor"] == WANG and published[0]["payload"]["archived"] == "v1.2",
              {t: types.count(t) for t in sorted(set(types))})


def submitted_s01(run: Run) -> str:
    trip_id = run.client.post("/api/trips", json={}, headers=h(ZHANG)).json()["trip"]["id"]
    run.client.patch(f"/api/trips/{trip_id}/slots", json={"expected_revision": 1, "slots": S01},
                     headers=h(ZHANG))
    response = run.client.post(f"/api/trips/{trip_id}/submit", json={"expected_revision": 1},
                               headers=h(ZHANG, f"submit-{trip_id}"))
    assert response.status_code == 200, response.text
    return trip_id


def ac024(run: Run) -> None:
    ac = "AC-024"
    trip_id = submitted_s01(run)
    before = run.trip(trip_id)
    cards_before = run.approvals(trip_id)
    run.write("AC-024/db-policies-before.json", run.snapshot())

    upload = run.upload("v1.3", "v1.3.md")
    extracted = run.extract("v1.3")
    cands = {c["id"]: c for c in extracted.json()["candidates"]}
    for rule_id in CLOSED_IDS:
        run.patch("v1.3", rule_id, {"status": "confirmed"})
    run.patch("v1.3", INVALID_ID, {"status": "rejected"})
    published = run.publish("v1.3", "pub-v13")
    run.write("AC-024/01-create-and-publish-v1.3.json", {
        "upload": exchange(upload), "extract": exchange(extracted), "publish": exchange(published)})
    run.write("AC-024/db-policies-after-publish.json", run.snapshot())
    hotel_rule = cands.get("RULE-HOTEL-001", {})
    rules = run.snapshot()["policy_rules"]
    values = {v: {r["id"]: (r["condition"], r["value"]) for r in rules
                  if r["version"] == v and r["status"] == "confirmed"} for v in ("v1.2", "v1.3")}
    changed = sorted(i for i in values["v1.3"] if values["v1.3"][i] != values["v1.2"].get(i))
    run.check(ac, "S-12: v1.3 created and published by the admin via #20–#23 (v1.2 archived)",
              upload.status_code == 201 and published.status_code == 200
              and published.json()["archived_version"] == "v1.2"
              and run.statuses() == {"v1.0": "archived", "v1.2": "archived", "v1.3": "published"},
              run.statuses())
    run.check(ac, "v1.3 only changes employee A hotel cap to 45000, quote/locator from v1.3.md",
              hotel_rule.get("value") == {"caps_fen": {"A": 45000, "B": 40000, "C": 30000}}
              and hotel_rule.get("quote") == V13_HOTEL_QUOTE and hotel_rule.get("locator") == "line:13"
              and set(values["v1.3"]) == set(values["v1.2"]) == set(CLOSED_IDS)
              and changed == ["RULE-HOTEL-001"],
              {"changed_vs_v1.2": changed, "hotel": hotel_rule.get("value")})

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
    run.write("AC-024/revalidate-response.json", exchange(response))
    run.write("AC-024/trip-after.json", after)
    run.write("AC-024/approval-cards-after.json", cards_after)
    run.write("AC-024/audit.json", audit)
    run.write("AC-024/policy-audit.json", run.policy_audit())

    hotel = [v for v in after["violations"] if v["code"] == "HOTEL_OVER_CAP"]
    (revalidated,) = [e for e in audit if e["type"] == "revalidated"]
    judged = (revalidated["payload"]["before"]["policy_version"],
              revalidated["payload"]["after"]["policy_version"])
    run.check(ac, "before: bound v1.2, PENDING_APPROVAL, no HOTEL_OVER_CAP",
              (before["policy_version"], before["status"]) == ("v1.2", "PENDING_APPROVAL")
              and not [v for v in before["violations"] if v["code"] == "HOTEL_OVER_CAP"],
              [before["policy_version"], before["status"]])
    run.check(ac, "notice shown while bound v1.2 ≠ published v1.3",
              notice["policy_change_notice"] == {"bound_version": "v1.2", "published_version": "v1.3"},
              notice["policy_change_notice"])
    run.check(ac, "revision 1→2, policy_version v1.3, still PENDING_APPROVAL",
              (after["revision"], after["policy_version"], after["status"]) == (2, "v1.3", "PENDING_APPROVAL")
              and body["previous_revision"] == 1,
              [after["revision"], after["policy_version"], after["status"]])
    run.check(ac, "old card superseded, new pending card at revision 2",
              [(c["revision"], c["status"]) for c in cards_after] == [(1, "superseded"), (2, "pending")]
              and [(a["trip_id"], a["revision"]) for a in pending] == [(trip_id, 2)],
              [(c["revision"], c["status"]) for c in cards_after])
    run.check(ac, "new violation HOTEL_OVER_CAP 48000 > 45000 citing RULE-HOTEL-001 v1.3 (POL-2)",
              len(hotel) == 1
              and (hotel[0]["actual_value"], hotel[0]["limit_value"], hotel[0]["policy_version"],
                   hotel[0]["rule_id"], hotel[0]["quote"]) == (48000, 45000, "v1.3", "RULE-HOTEL-001",
                                                               V13_HOTEL_QUOTE), hotel)
    run.check(ac, "same candidates reused, no provider call",
              after["plan"]["hotel"]["candidate"] == before["plan"]["hotel"]["candidate"]
              and after["plan"]["transport"] == before["plan"]["transport"]
              and revalidated["payload"]["providers_called"] is False
              and all(e["revision"] == 1 for e in audit if e["type"] == "provider_call"),
              revalidated["payload"]["reused_candidates"])
    run.check(ac, "audit keeps both judgements (v1.2 before, v1.3 after)",
              judged == ("v1.2", "v1.3")
              and [e["payload"]["policy_version"] for e in audit if e["type"] == "rules_evaluated"]
              == ["v1.2", "v1.3"], judged)
    run.check(ac, "same Idempotency-Key replays; old revision decision → 409 STALE_REVISION",
              replay.json() == body and stale.status_code == 409 and code(stale) == "STALE_REVISION",
              stale.status_code)


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    for name, scenario in (("ac005", ac005), ("ac024", ac024)):
        settings = settings_for(name)
        with TestClient(create_app(settings)) as client:
            scenario(Run(client, settings, out, checks))
    failed = [c for c in checks if c["result"] != "PASS"]
    summary = {"mode": "fake (recorded extraction §7.1.1, source hand_written); no network; no model calls;"
                       " one throwaway database per AC",
               "checks": checks, "passed": len(checks) - len(failed), "failed": len(failed)}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    for c in checks:
        print(f"{c['result']}  {c['ac']}  {c['check']}")
    print(f"{summary['passed']} passed, {summary['failed']} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/acceptance/TASK-008")))
