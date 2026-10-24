"""Export TASK-005 service-level evidence for AC-008/009/010/011.

Drives the real workflow (seeded rules → Demo providers → selector → rules →
LangGraph with an AsyncSqliteSaver checkpoint) in Fake mode against a throwaway
data directory: no network, no model calls. Safe to re-run. Usage:

    uv run python scripts/export_workflow_evidence.py artifacts/acceptance/TASK-005

The HTTP/SSE layer (TASK-006), browser screenshots (AC-008) and a real process
kill (AC-010) are NOT covered here; the restart below re-opens the workflow in
the same process.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from travel_desk.config import Settings
from travel_desk.db.database import initialize_database, read_session
from travel_desk.services import trips as trip_service
from travel_desk.services.access import load_employee
from travel_desk.services.audit import list_events
from travel_desk.services.errors import ServiceError
from travel_desk.services.views import list_outbox, trip_view
from travel_desk.workflow.engine import Workflow

S01 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
       "purpose": "供应商大会", "declared_budget_fen": 400000}


class Env:
    def __init__(self, settings: Settings, out: Path):
        self.settings, self.out = settings, out

    def user(self, employee_id: str) -> dict[str, Any]:
        with read_session(self.settings.db_path) as conn:
            return load_employee(conn, employee_id)

    def trip(self, trip_id: str) -> dict[str, Any]:
        with read_session(self.settings.db_path) as conn:
            return trip_view(conn, trip_service.load_trip(conn, trip_id))

    def audit(self, trip_id: str) -> list[dict[str, Any]]:
        with read_session(self.settings.db_path) as conn:
            return list_events(conn, trip_id)

    def outbox(self, trip_id: str) -> list[dict[str, Any]]:
        with read_session(self.settings.db_path) as conn:
            return list_outbox(conn, trip_id=trip_id)

    def counts(self, trip_id: str) -> dict[str, Any]:
        with read_session(self.settings.db_path) as conn:
            def one(sql: str) -> int:
                return conn.execute(sql, (trip_id,)).fetchone()[0]
            return {
                "approvals": one("SELECT COUNT(*) FROM approvals WHERE trip_id = ?"),
                "decided_approvals": one("SELECT COUNT(*) FROM approvals WHERE trip_id = ?"
                                         " AND status='decided'"),
                "outbox_trip_delivery": one("SELECT COUNT(*) FROM outbox_emails WHERE trip_id = ?"
                                            " AND kind='trip_delivery'"),
                "outbox_workflow_notification": one("SELECT COUNT(*) FROM outbox_emails WHERE trip_id = ?"
                                                    " AND kind='workflow_notification'"),
                "itineraries": one("SELECT COUNT(*) FROM itineraries WHERE trip_id = ?"),
                "decision_recorded_events": one("SELECT COUNT(*) FROM audit_events WHERE trip_id = ?"
                                                " AND type='decision_recorded'"),
            }

    def timeline(self, trip_id: str) -> list[dict[str, Any]]:
        return [{"seq": e["seq"], "revision": e["revision"], **e["payload"]}
                for e in self.audit(trip_id) if e["type"] == "status_changed"]

    def write(self, name: str, data: Any) -> None:
        path = self.out / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def new_trip(self, slots: dict[str, Any], owner: str = "u-zhangsan") -> str:
        actor = self.user(owner)
        trip = trip_service.create_trip(self.settings, actor, trace_id="ev-create")
        trip_service.update_slots(self.settings, actor, trip["id"], expected_revision=1, changes=slots,
                                  trace_id="ev-slots")
        return trip["id"]


async def submit(wf: Workflow, env: Env, trip_id: str, *, revision: int = 1, key: str = "submit-1") -> list:
    run = await wf.submit(trip_id, env.user("u-zhangsan"), {"expected_revision": revision},
                          idempotency_key=key, trace_id="ev-submit")
    events = await run.collect()
    await run.wait()
    return events


async def decide(wf: Workflow, env: Env, trip_id: str, decision: str, *, approver: str = "u-lisi",
                 revision: int = 1, comment: str | None = None, key: str = "decide-1") -> dict[str, Any]:
    body: dict[str, Any] = {"revision": revision, "decision": decision}
    if comment is not None:
        body["comment"] = comment
    try:
        status, response = await wf.decide(trip_id, env.user(approver), body, idempotency_key=key,
                                           trace_id="ev-decide")
        return {"request": {"approver": approver, "key": key, **body}, "status_code": status,
                "trip_status": response["trip"]["status"], "revision": response["trip"]["revision"]}
    except ServiceError as exc:
        return {"request": {"approver": approver, "key": key, **body}, "status_code": exc.status_code,
                "code": exc.code, "details": exc.details}


def scenario(env: Env, trip_id: str, events: list | None = None) -> dict[str, Any]:
    return {"sse_event_types": [e["type"] for e in events] if events else None,
            "status_timeline": env.timeline(trip_id), "counts": env.counts(trip_id),
            "outbox": env.outbox(trip_id), "audit_event_types": [e["type"] for e in env.audit(trip_id)]}


async def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        settings = replace(Settings.from_env(), llm_mode="fake", data_dir=data, db_path=data / "app.sqlite",
                           checkpoint_db_path=data / "checkpoints.sqlite", upload_dir=data / "uploads")
        initialize_database(settings)
        env = Env(settings, out_dir)
        wf = await Workflow(settings).open()
        try:
            # AC-008 + S-01: approval card, then approve and deliver.
            s01 = env.new_trip(S01)
            events = await submit(wf, env, s01)
            card = env.trip(s01)["current_approval"]
            env.write("AC-008-approval-card.json", card)
            approve = await decide(wf, env, s01, "approve")
            env.write("S-01-approve.json", {"decision": approve, **scenario(env, s01, events)})
            c = card["card"]
            summary["AC-008"] = {"revision": card["revision"], "total_fen": c["cost_breakdown"]["total_fen"],
                                 "budget_delta_fen": c["budget_delta_fen"],
                                 "approval_reasons": [r["code"] for r in c["approval_reasons"]],
                                 "sources": c["sources"]}
            summary["S-01"] = env.counts(s01)

            # AC-009: permissions, stale revision, same/different key.
            t = env.new_trip(S01)
            await submit(wf, env, t)
            attempts = [
                await decide(wf, env, t, "approve", approver="u-zhangsan", key="a-1"),
                await decide(wf, env, t, "approve", approver="u-zhaoliu", key="a-2"),
                await decide(wf, env, t, "approve", revision=2, key="a-3"),
                await decide(wf, env, t, "approve", key="a-4"),
                await decide(wf, env, t, "approve", key="a-4"),
                await decide(wf, env, t, "approve", key="a-5"),
            ]
            labels = ["employee_self", "non_direct_manager", "old_revision", "first", "same_key_replay",
                      "different_key"]
            env.write("AC-009-decisions.json", {"attempts": dict(zip(labels, attempts, strict=True)),
                                                "counts_after": env.counts(t)})
            summary["AC-009"] = {label: (a["status_code"], a.get("code")) for label, a in
                                 zip(labels, attempts, strict=True)} | {"counts_after": env.counts(t)}

            # AC-011: S-07 reject, S-08 request_changes.
            s07 = env.new_trip(S01)
            await submit(wf, env, s07)
            reject = await decide(wf, env, s07, "reject", comment="本次会议改为线上参加")
            env.write("S-07-reject.json", {"decision": reject, **scenario(env, s07)})
            s08 = env.new_trip(S01)
            await submit(wf, env, s08)
            changes = await decide(wf, env, s08, "request_changes", comment="请把预算调整为 5000 元后重提")
            env.write("S-08-request-changes.json", {"decision": changes, **scenario(env, s08)})
            summary["S-07"], summary["S-08"] = env.counts(s07), env.counts(s08)

            # AC-011: delivery write failure, then admin retry (twice: no duplicate).
            fail = env.new_trip(S01)
            await submit(wf, env, fail)

            def broken(trip_id: str, revision: int) -> None:
                raise OSError("simulated outbox write failure")

            wf.delivery_fault_hook = broken
            first = await decide(wf, env, fail, "approve")
            after_failure = env.trip(fail)
            wf.delivery_fault_hook = None
            admin = env.user("u-wangwu")
            retried = await wf.retry_delivery(fail, admin, trace_id="ev-retry")
            try:
                await wf.retry_delivery(fail, admin, trace_id="ev-retry-2")
                second_retry = {"status_code": 200}
            except ServiceError as exc:
                second_retry = {"status_code": exc.status_code, "code": exc.code}
            env.write("AC-011-delivery-failure-retry.json", {
                "decision": first, "after_failure": {"status": after_failure["status"],
                                                     "delivery": after_failure["delivery"]},
                "after_retry": {"status": retried["status"], "delivery": retried["delivery"]},
                "second_retry": second_retry, **scenario(env, fail)})
            summary["AC-011-retry"] = {"after_failure": after_failure["status"],
                                       "error": after_failure["delivery"]["error"],
                                       "after_retry": retried["status"], "second_retry": second_retry,
                                       **env.counts(fail)}

            # AC-010 (in-process): close the workflow while PENDING_APPROVAL, reopen, recover, approve.
            s10 = env.new_trip(S01)
            await submit(wf, env, s10)
        finally:
            await wf.close()
        wf2 = await Workflow(settings).open()
        try:
            recovered = await wf2.recover(trace_id="ev-recover")
            approve10 = await decide(wf2, env, s10, "approve")
            replay10 = await decide(wf2, env, s10, "approve")
            env.write("AC-010-restart-in-process.json", {
                "note": ("同进程关闭并重新打开 Workflow（新的 aiosqlite 连接与图实例）；"
                         "真实进程终止在 TASK-006 补证"),
                "recover_report": recovered, "decision": approve10, "replay": replay10,
                "decision_via": [e["payload"].get("via") for e in env.audit(s10)
                                 if e["type"] == "decision_recorded"],
                **scenario(env, s10)})
            summary["AC-010-in-process"] = {"decision": approve10["trip_status"],
                                            "via": [e["payload"].get("via") for e in env.audit(s10)
                                                    if e["type"] == "decision_recorded"],
                                            **env.counts(s10)}
        finally:
            await wf2.close()
    env.write("summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/acceptance/TASK-005")))
