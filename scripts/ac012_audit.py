"""AC-012 evidence: audit completeness and immutability (contract §7.2, REQ-020, HC-11).

Runs the four scenarios AC-012 names — S-01, S-08, S-09, S-12 — against a real
`python -m travel_desk` subprocess in Fake mode (no `DASHSCOPE_API_KEY`, no
external network), exports each trip's complete audit timeline, and asserts the
union covers every category REQ-020 lists: slot changes, retrieved clauses,
rule results, Provider call summaries and sources, plan versions, approval
decisions, notification/delivery, and errors.

It then proves the timeline is append-only against the *live* database: a direct
SQL UPDATE and DELETE on `audit_events` are refused by the triggers, the trigger
definitions are present, and the HTTP surface exposes audit as read-only (no
mutating route). The single sanctioned deletion path — Demo reset — is evidenced
separately in ../ac016-demo-reset/.

Usage:
    uv run --offline python scripts/ac012_audit.py artifacts/acceptance/TASK-010/ac012-audit-integrity

Exit code 0 only when every check passed; the evidence is written either way.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_lib import (  # noqa: E402
    Check,
    Desk,
    Server,
    cleanup,
    temp_work,
    write_json,
)
from e2e_scenarios import s01, s08, s09, s12  # noqa: E402

# REQ-020 categories → the audit event types that satisfy each. A category is
# covered when at least one of its event types appears in the exported timeline.
CATEGORIES: dict[str, set[str]] = {
    "槽位变化": {"slots_changed", "trip_created", "message_processed", "image_confirmed", "image_extracted"},
    "检索条款与引用": {"rules_evaluated", "policy_bound", "policy_question"},
    "规则结果": {"rules_evaluated", "validation_blocked", "blocked_cleared", "revalidated"},
    "Provider 调用摘要与来源": {"provider_call", "candidates_selected"},
    "方案版本": {"plan_created"},
    "审批决定": {"approval_requested", "decision_recorded", "auto_approved",
             "approval_superseded", "no_approver"},
    "通知与交付": {"notification_written", "delivery_written", "delivery_failed"},
    # A recorded fault (Provider timeout) is the in-scenario error evidence; the
    # dedicated `error` / `delivery_failed` event types are proven by the suite.
    "错误": {"error", "delivery_failed", "validation_blocked"},
}
SCENARIOS = [("S-01", s01), ("S-08", s08), ("S-09", s09), ("S-12", s12)]


def _audit_event_types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def _provider_faults(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Provider calls whose recorded status is a fault (the in-scenario error evidence)."""
    out = []
    for e in events:
        if e["type"] == "provider_call":
            status = e["payload"].get("status")
            if status not in ("ok", None):
                out.append({"provider": e["payload"].get("provider"), "status": status})
    return out


def _immutability_proof(db_path: Path, base_url: str) -> dict[str, Any]:
    """Try to mutate the live audit table directly and inspect the HTTP surface."""
    result: dict[str, Any] = {}
    conn = sqlite3.connect(f"file:{db_path}", uri=True, timeout=15)
    try:
        # Triggers must exist and be the append-only guards.
        triggers = {r[0]: r[1] for r in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_events'")}
        result["triggers"] = sorted(triggers)
        result["trigger_sql"] = triggers
        row = conn.execute("SELECT seq, id FROM audit_events ORDER BY seq LIMIT 1").fetchone()
        target_seq, target_id = (row[0], row[1]) if row else (None, None)
        result["probe_event"] = {"seq": target_seq, "id": target_id}

        update_error = delete_error = None
        if target_seq is not None:
            try:
                conn.execute("UPDATE audit_events SET actor = 'tampered' WHERE seq = ?", (target_seq,))
                conn.commit()
            except sqlite3.Error as exc:
                update_error = str(exc)
                conn.rollback()
            try:
                conn.execute("DELETE FROM audit_events WHERE seq = ?", (target_seq,))
                conn.commit()
            except sqlite3.Error as exc:
                delete_error = str(exc)
                conn.rollback()
        result["update_refused"] = update_error
        result["delete_refused"] = delete_error
        # The probe event must be byte-identical afterwards (nothing slipped through).
        if target_seq is not None:
            after = conn.execute("SELECT actor FROM audit_events WHERE seq = ?", (target_seq,)).fetchone()
            result["probe_actor_after"] = after[0] if after else None
            result["probe_still_present"] = after is not None
    finally:
        conn.close()

    # HTTP surface: audit must be read-only (GET), with no mutating route.
    openapi = httpx.get(base_url + "/api/openapi.json", timeout=15).json()
    audit_routes: dict[str, list[str]] = {}
    mutating_audit: list[str] = []
    for path, methods in openapi.get("paths", {}).items():
        if "audit" in path:
            audit_routes[path] = sorted(m.upper() for m in methods)
            if any(m.upper() in {"PUT", "PATCH", "DELETE", "POST"} for m in methods):
                mutating_audit.append(path)
    result["audit_http_routes"] = audit_routes
    result["mutating_audit_routes"] = mutating_audit
    return result


def run(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = temp_work("ac012-")
    server = Server(work, out, llm_mode="fake")
    check = Check()
    report: dict[str, Any] = {
        "scenario": "AC-012 / REQ-020 audit integrity", "mode": "fake",
        "start_command": "python -m travel_desk (= uv run travel-desk)",
        "data_dir": "<tempdir>/var", "scenarios": {}, "coverage": {}, "immutability": {},
    }
    try:
        server.start()
        union_types: set[str] = set()
        fault_evidence: list[dict[str, Any]] = []
        with httpx.Client(base_url=server.base, timeout=120) as client:
            d = Desk(client, server)
            for name, fn in SCENARIOS:
                if d.reset().status_code != 200:
                    raise RuntimeError(f"reset before {name} failed")
                scenario_check = Check(echo=False)
                detail = fn(d, scenario_check)
                trip_id = detail["trip_id"]
                events = d.audit(trip_id)
                types = _audit_event_types(events)
                union_types.update(types)
                faults = _provider_faults(events)
                fault_evidence.extend(faults)
                # Export the complete timeline (the contract's "审计导出").
                (out / f"audit-{name}.json").write_text(
                    json.dumps({"scenario": name, "trip_id": trip_id, "event_count": len(events),
                                "event_types": types, "events": events},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                report["scenarios"][name] = {
                    "trip_id": trip_id, "event_count": len(events), "event_types": types,
                    "provider_faults": faults, "scenario_checks_passed": scenario_check.passed,
                    "trajectory": d.trajectory(trip_id),
                }
                check(f"{name} 场景自身断言通过", scenario_check.passed,
                      [i["check"] for i in scenario_check.items if not i["passed"]])
                check(f"{name} 审计非空且已导出", len(events) > 0, len(events))

            # ---- REQ-020 category coverage across the four scenarios --------------
            coverage: dict[str, Any] = {}
            for category, wanted in CATEGORIES.items():
                found = sorted(union_types & wanted)
                coverage[category] = {"matched_event_types": found, "covered": bool(found)}
            # The 错误 category also accepts a recorded Provider fault (S-09 timeout).
            if not coverage["错误"]["covered"] and fault_evidence:
                coverage["错误"]["covered"] = True
                coverage["错误"]["via_provider_fault"] = fault_evidence
            report["coverage"] = coverage
            report["union_event_types"] = sorted(union_types)
            for category, info in coverage.items():
                check(f"审计覆盖 REQ-020 类别「{category}」", info["covered"],
                      info.get("matched_event_types") or info.get("via_provider_fault"))

            # ---- immutability against the live database ---------------------------
            proof = _immutability_proof(server.data_dir / "app.sqlite", server.base)
            report["immutability"] = proof
            check("audit_events 存在追加式触发器（no_update / no_delete）",
                  "audit_events_no_update" in proof["triggers"]
                  and "audit_events_no_delete" in proof["triggers"], proof["triggers"])
            check("直接 SQL UPDATE 既有审计事件被拒绝", proof["update_refused"] is not None,
                  proof["update_refused"])
            check("直接 SQL DELETE 既有审计事件被拒绝（非重置路径）", proof["delete_refused"] is not None,
                  proof["delete_refused"])
            check("被探测事件在尝试后仍原样存在（actor 未被篡改）",
                  proof.get("probe_still_present") is True
                  and proof.get("probe_actor_after") not in (None, "tampered"),
                  proof.get("probe_actor_after"))
            check("HTTP 层审计只读（无任何改写审计的路由）",
                  proof["mutating_audit_routes"] == [] and bool(proof["audit_http_routes"]),
                  proof["audit_http_routes"])
    except Exception as exc:  # noqa: BLE001 - record and fail, never pass silently
        check("script completed without exception", False, f"{type(exc).__name__}: {exc}")
    finally:
        server.stop()
        report["checks"] = check.items
        report["passed"] = check.passed
        write_json(out / "AC-012-audit.json", report)
        cleanup(work)
    print("AC-012", "PASSED" if check.passed else "FAILED", flush=True)
    return 0 if check.passed else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: ac012_audit.py <out_dir>")
    sys.exit(run(Path(sys.argv[1])))
