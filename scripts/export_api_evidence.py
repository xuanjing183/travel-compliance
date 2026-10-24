"""Export TASK-006 HTTP-level evidence for AC-002/003/009/011 (deterministic mode).

Runs the real app in-process (`TestClient`, lifespan opens the workflow and its
checkpoint) with `llm_mode=fake` on a throwaway data directory: no network, no
model calls. The fake slot extractor is a deterministic parser (a test double,
not a model); the image extraction comes from the recorded output
`tests/fixtures/recorded/invitations.json` (source=model). Raw SSE bodies are
saved as received. Every expectation is checked; the exit code is 0 only if all
checks pass. Usage:

    uv run --offline python scripts/export_api_evidence.py artifacts/acceptance/TASK-006

Not covered here: browser screenshots (AC-003 UI, TASK-009) and the real
process kill (AC-010, see scripts/ac010_restart.py).
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from travel_desk.config import REPO_ROOT, get_settings
from travel_desk.db.database import read_session
from travel_desk.main import create_app

INVITATION = REPO_ROOT / "tests" / "fixtures" / "invitations" / "shanghai_supplier_conference.png"
S01 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
       "purpose": "供应商大会", "declared_budget_fen": 400000}
ZHANG, LISI, ZHAO, WANG = "u-zhangsan", "u-lisi", "u-zhaoliu", "u-wangwu"
S05_MISSING = ["origin", "start_date", "end_date", "purpose", "declared_budget_fen"]


def h(user: str | None, key: str | None = None) -> dict[str, str]:
    headers = {"X-Demo-User": user} if user else {}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def sse(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = [line[5:].lstrip() for line in block.split("\n") if line.startswith("data:")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


class Run:
    def __init__(self, client: TestClient, db_path: Path, out: Path):
        self.client, self.db_path, self.out = client, db_path, out
        self.checks: list[dict[str, Any]] = []

    def check(self, ac: str, name: str, ok: bool, observed: Any = None) -> None:
        self.checks.append({"ac": ac, "check": name, "result": "PASS" if ok else "FAIL",
                            "observed": observed})

    def write(self, rel: str, data: Any) -> None:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def trip(self, trip_id: str) -> dict[str, Any]:
        return self.client.get(f"/api/trips/{trip_id}", headers=h(ZHANG)).json()

    def audit(self, trip_id: str) -> list[dict[str, Any]]:
        return self.client.get(f"/api/trips/{trip_id}/audit", headers=h(ZHANG)).json()["events"]

    def outbox(self, trip_id: str) -> list[dict[str, Any]]:
        return self.client.get(f"/api/outbox?trip_id={trip_id}", headers=h(WANG)).json()["emails"]

    def timeline(self, trip_id: str) -> list[dict[str, Any]]:
        return [{"seq": e["seq"], "ts": e["ts"], "revision": e["revision"], **e["payload"]}
                for e in self.audit(trip_id) if e["type"] == "status_changed"]

    def counts(self, trip_id: str) -> dict[str, int]:
        queries = {
            "approvals": "SELECT COUNT(*) FROM approvals WHERE trip_id = ?",
            "decided_approvals": "SELECT COUNT(*) FROM approvals WHERE trip_id = ? AND status = 'decided'",
            "outbox_trip_delivery": "SELECT COUNT(*) FROM outbox_emails WHERE trip_id = ?"
                                    " AND kind = 'trip_delivery'",
            "outbox_workflow_notification": "SELECT COUNT(*) FROM outbox_emails WHERE trip_id = ?"
                                            " AND kind = 'workflow_notification'",
            "itineraries": "SELECT COUNT(*) FROM itineraries WHERE trip_id = ?",
            "decision_recorded_events": "SELECT COUNT(*) FROM audit_events WHERE trip_id = ?"
                                        " AND type = 'decision_recorded'",
        }
        with read_session(self.db_path) as conn:
            return {name: conn.execute(sql, (trip_id,)).fetchone()[0] for name, sql in queries.items()}

    def new_trip(self, slots: dict[str, Any] | None = None) -> str:
        trip_id = self.client.post("/api/trips", json={}, headers=h(ZHANG)).json()["trip"]["id"]
        if slots:
            response = self.client.patch(f"/api/trips/{trip_id}/slots", headers=h(ZHANG),
                                         json={"expected_revision": 1, "slots": slots})
            assert response.status_code == 200, response.text
        return trip_id

    def submit(self, trip_id: str, key: str = "submit-1") -> list[dict[str, Any]]:
        response = self.client.post(f"/api/trips/{trip_id}/submit", json={"expected_revision": 1},
                                    headers=h(ZHANG, key))
        return sse(response.text)

    def decide(self, trip_id: str, user: str, key: str, decision: str = "approve", revision: int = 1,
               comment: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"revision": revision, "decision": decision}
        if comment is not None:
            body["comment"] = comment
        response = self.client.post(f"/api/trips/{trip_id}/decision", json=body, headers=h(user, key))
        data = response.json()
        result: dict[str, Any] = {"request": {"user": user, "key": key, **body},
                                  "status_code": response.status_code}
        if response.status_code == 200:
            result.update(trip_status=data["trip"]["status"], revision=data["trip"]["revision"],
                          approval_decision=data["approval"]["decision"])
        else:
            result.update(code=data["error"]["code"], details=data["error"]["details"])
        return result


def ac002(run: Run) -> None:
    trip_id = run.new_trip()
    turns = [("下周去杭州", "turn-1"),
             ("从北京出发，10月28日到10月30日，参加供应商大会，预算四千元", "turn-2"),
             ("预算改成五千", "turn-3")]
    parsed = []
    for text, name in turns:
        response = run.client.post(f"/api/trips/{trip_id}/messages",
                                   json={"text": text, "expected_revision": 1}, headers=h(ZHANG))
        run.write(f"AC-002/{name}.sse.txt", f"POST /api/trips/{{id}}/messages  text={text}\n"
                                            f"HTTP {response.status_code}  X-Trace-Id: "
                                            f"{response.headers.get('X-Trace-Id')}\n\n{response.text}")
        events = sse(response.text)
        slots = next(e["payload"] for e in events if e["type"] == "slots")
        ask = "".join(e["payload"]["text"] for e in events if e["type"] == "token")
        parsed.append({"text": text, "event_types": [e["type"] for e in events], "slots": slots["slots"],
                       "missing_fields": slots["missing_fields"], "invalid_fields": slots["invalid_fields"],
                       "changes": slots["changes"], "generator": slots["generator"], "ask": ask})
    audit = run.audit(trip_id)
    run.write("AC-002/turns.json", parsed)
    run.write("AC-002/audit.json", audit)
    run.write("AC-002/trip-after.json", run.trip(trip_id))

    first, second, third = parsed
    run.check("AC-002", "S-05: destination=杭州 recognised", first["slots"]["destination"] == "杭州",
              first["slots"]["destination"])
    run.check("AC-002", "S-05: '下周' not guessed into dates",
              first["slots"]["start_date"] is None and first["slots"]["end_date"] is None,
              [first["slots"]["start_date"], first["slots"]["end_date"]])
    run.check("AC-002", "S-05: one reply asks exactly the 5 missing fields",
              first["missing_fields"] == S05_MISSING and first["event_types"].count("token") == 1,
              first["missing_fields"])
    asked = first["ask"].split("还需要")[-1]
    run.check("AC-002", "S-05: 杭州/目的城市 not asked again",
              "目的城市" not in asked and "杭州" not in asked,
              first["ask"])
    run.check("AC-002", "completed turn asks nothing", second["missing_fields"] == []
              and "还需要" not in second["ask"], second["ask"])
    run.check("AC-002", "budget change recorded before/after", third["changes"] ==
              [{"field": "declared_budget_fen", "before": 400000, "after": 500000}], third["changes"])
    run.check("AC-002", "no re-ask after change", "还需要" not in third["ask"], third["ask"])
    changes = [e for e in audit if e["type"] == "slots_changed"]
    run.check("AC-002", "audit slots_changed has before/after for the budget edit",
              changes[-1]["payload"]["changes"] == third["changes"]
              and all(c["payload"]["origin"] == "message" for c in changes),
              changes[-1]["payload"])
    asked_audit = [e["payload"]["missing_fields"] for e in audit if e["type"] == "message_processed"]
    run.check("AC-002", "audit message_processed asks: 5 fields, then none, then none",
              asked_audit == [S05_MISSING, [], []], asked_audit)


def ac003(run: Run, max_bytes: int) -> None:
    trip_id = run.new_trip()
    before = run.trip(trip_id)
    data = INVITATION.read_bytes()
    uploaded = run.client.post("/api/uploads", headers=h(ZHANG),
                               files={"file": (INVITATION.name, data, "image/png")})
    extraction = run.client.post(f"/api/trips/{trip_id}/image-extractions", headers=h(ZHANG),
                                 json={"upload_id": uploaded.json()["id"]})
    proposal = extraction.json()
    pending = run.trip(trip_id)
    audit_pending = run.audit(trip_id)
    confirmed = run.client.post(f"/api/trips/{trip_id}/image-extractions/{proposal['id']}/confirm",
                                json={"expected_revision": 1}, headers=h(ZHANG))
    after = run.trip(trip_id)
    audit = run.audit(trip_id)
    wrong_type = run.client.post("/api/uploads", headers=h(ZHANG),
                                 files={"file": ("invitation.png", b"%PDF-1.7 not an image", "image/png")})
    too_big = run.client.post("/api/uploads", headers=h(ZHANG),
                              files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + b"\0" * max_bytes,
                                              "image/png")})
    run.write("AC-003/trip-before.json", before)
    run.write("AC-003/upload.json", {"status_code": uploaded.status_code, **uploaded.json()})
    run.write("AC-003/extraction.json", {"status_code": extraction.status_code, **proposal})
    run.write("AC-003/trip-after-extraction.json", pending)
    run.write("AC-003/trip-after-confirm.json", after)
    run.write("AC-003/audit.json", audit)
    run.write("AC-003/upload-errors.json", {
        "wrong_type": {"status_code": wrong_type.status_code, **wrong_type.json()},
        "too_big": {"status_code": too_big.status_code, "bytes_sent": max_bytes + 8, **too_big.json()}})

    run.check("AC-003", "extraction comes from recorded output (source=model)",
              proposal.get("generator") == "recorded:model", proposal.get("generator"))
    run.check("AC-003", "Trip unchanged before confirm",
              pending["slots"] == before["slots"] and pending["revision"] == before["revision"]
              and [e["type"] for e in audit_pending] == ["trip_created", "image_extracted"],
              [e["type"] for e in audit_pending])
    slots = after["slots"]
    address = (slots.get("meeting_address") or "").replace(" ", "").replace("\u3000", "")
    s11 = {"destination": slots["destination"] == "上海", "start_date": slots["start_date"] == "2026-10-28",
           "end_date": slots["end_date"] == "2026-10-30", "purpose": "供应商大会" in (slots["purpose"] or ""),
           "meeting_address": "滨江大道2727号" in address and "星衡国际会议中心" in address}
    run.check("AC-003", "after confirm fields match S-11", confirmed.status_code == 200 and all(s11.values()),
              {"slots": slots, "criteria": s11})
    types = [e["type"] for e in audit]
    change = next(e for e in audit if e["type"] == "slots_changed")
    run.check("AC-003", "audited: image_extracted→slots_changed(origin=image)→image_confirmed",
              {"image_extracted", "slots_changed", "image_confirmed"} <= set(types)
              and change["payload"]["origin"] == "image", types)
    run.check("AC-003", "wrong type → 415", wrong_type.status_code == 415, wrong_type.json()["error"]["code"])
    run.check("AC-003", "over 10 MiB → 413", too_big.status_code == 413, too_big.json()["error"]["code"])


def ac009(run: Run) -> None:
    trip_id = run.new_trip(S01)
    run.submit(trip_id)
    labels = ["employee_self", "non_direct_manager", "old_revision", "first", "same_key_replay",
              "different_key"]
    attempts = [run.decide(trip_id, ZHANG, "a-1"), run.decide(trip_id, ZHAO, "a-2"),
                run.decide(trip_id, LISI, "a-3", revision=2)]
    before = run.counts(trip_id)
    attempts += [run.decide(trip_id, LISI, "a-4"), run.decide(trip_id, LISI, "a-4"),
                 run.decide(trip_id, LISI, "a-5")]
    after = run.counts(trip_id)
    by = dict(zip(labels, attempts, strict=True))
    run.write("AC-009/decisions-http.json", {"attempts": by, "db_counts_before_valid_decision": before,
                                            "db_counts_after": after})
    run.check("AC-009", "张三 approve → 403", by["employee_self"]["status_code"] == 403,
              by["employee_self"].get("code"))
    run.check("AC-009", "non-direct manager → 403", by["non_direct_manager"]["status_code"] == 403,
              by["non_direct_manager"].get("code"))
    run.check("AC-009", "old revision → 409", by["old_revision"]["status_code"] == 409,
              by["old_revision"].get("code"))
    run.check("AC-009", "rejected attempts had no side effects",
              before["decided_approvals"] == 0 and before["outbox_trip_delivery"] == 0, before)
    first, replay = by["first"], by["same_key_replay"]
    run.check("AC-009", "same key returns the same result",
              first["status_code"] == replay["status_code"] == 200
              and {k: v for k, v in first.items() if k != "request"}
              == {k: v for k, v in replay.items() if k != "request"}, replay)
    run.check("AC-009", "second different key → 409", by["different_key"]["status_code"] == 409,
              by["different_key"].get("code"))
    run.check("AC-009", "DB: one decision, one delivery, one itinerary",
              after["decided_approvals"] == 1 and after["decision_recorded_events"] == 1
              and after["outbox_trip_delivery"] == 1 and after["itineraries"] == 1, after)


def ac011(run: Run) -> None:
    scenarios = {}
    for name, decision, comment, expected in (
            ("S-01", "approve", None, {"trip_delivery": 1, "workflow_notification": 0}),
            ("S-07", "reject", "本次会议改为线上参加", {"trip_delivery": 0, "workflow_notification": 1}),
            ("S-08", "request_changes", "请把预算调整为 5000 元后重提",
             {"trip_delivery": 0, "workflow_notification": 1})):
        trip_id = run.new_trip(S01)
        run.submit(trip_id)
        result = run.decide(trip_id, LISI, f"{name}-d", decision=decision, comment=comment)
        emails = run.outbox(trip_id)
        kinds = {kind: sum(e["kind"] == kind for e in emails) for kind in expected}
        scenarios[name] = {"decision": result, "outbox": emails, "status_timeline": run.timeline(trip_id)}
        run.check("AC-011", f"{name}: outbox kinds/counts", kinds == expected, kinds)

    trip_id = run.new_trip(S01)
    run.submit(trip_id)
    workflow = run.client.app.state.workflow

    def broken(trip_id: str, revision: int) -> None:
        raise OSError("simulated outbox write failure")

    workflow.delivery_fault_hook = broken  # test-only hook (§4.7): there is no public fault switch for it
    failed = run.decide(trip_id, LISI, "fail-d")
    workflow.delivery_fault_hook = None
    after_failure = run.trip(trip_id)
    retry = run.client.post(f"/api/trips/{trip_id}/delivery/retry", headers=h(WANG))
    retry_again = run.client.post(f"/api/trips/{trip_id}/delivery/retry", headers=h(WANG))
    counts = run.counts(trip_id)
    scenarios["delivery-failure-retry"] = {
        "decision": failed, "after_failure": {"status": after_failure["status"],
                                              "delivery": after_failure.get("delivery")},
        "retry": {"status_code": retry.status_code, "status": retry.json().get("status")},
        "retry_again": {"status_code": retry_again.status_code, **retry_again.json()},
        "outbox": run.outbox(trip_id), "db_counts": counts, "status_timeline": run.timeline(trip_id)}
    run.write("AC-011/scenarios-http.json", scenarios)
    run.check("AC-011", "write failure stops at DELIVERY_PENDING",
              after_failure["status"] == "DELIVERY_PENDING" and counts["outbox_trip_delivery"] <= 1,
              after_failure["status"])
    run.check("AC-011", "admin retry over HTTP → DELIVERED",
              retry.status_code == 200 and retry.json().get("status") == "DELIVERED", retry.status_code)
    run.check("AC-011", "second retry does not duplicate (409, one trip_delivery)",
              retry_again.status_code == 409 and counts["outbox_trip_delivery"] == 1, counts)


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="api-evidence-")) / "var"
    settings = replace(get_settings(), llm_mode="fake", data_dir=work, db_path=work / "app.sqlite",
                       checkpoint_db_path=work / "checkpoints.sqlite", upload_dir=work / "uploads")
    with TestClient(create_app(settings)) as client:
        run = Run(client, settings.db_path, out)
        ac002(run)
        ac003(run, settings.max_upload_bytes)
        ac009(run)
        ac011(run)
    failed = [c for c in run.checks if c["result"] != "PASS"]
    summary = {"mode": "fake (deterministic parser) + recorded image extraction; no network",
               "checks": run.checks, "passed": len(run.checks) - len(failed), "failed": len(failed)}
    run.write("api-evidence-summary.json", summary)
    for c in run.checks:
        print(f"{c['result']}  {c['ac']}  {c['check']}")
    print(f"{summary['passed']} passed, {summary['failed']} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/acceptance/TASK-006")))
