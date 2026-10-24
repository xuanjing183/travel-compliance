"""AC-016 evidence: one-click Demo reset (contract §7.2, REQ-025, HC-11 exception).

Boots the service with the documented start command (`python -m travel_desk`,
the same entry point as `uv run travel-desk`) on a throwaway data directory in
Fake mode, then *creates* every kind of dynamic data the contract names —
a delivered Trip (approval + itinerary + Outbox email), a second Trip left in
PENDING_APPROVAL (a live checkpoint thread), a personal preference, and an
injected fault — snapshots both databases, and resets.

It checks the authorisation and confirmation gates first (non-admin 403, wrong
confirmation 422), then that a correct admin reset clears all dynamic data,
restores the seed (4 employees, policy v1.0 archived + v1.2 published, seeded
rules, faults off), deletes the checkpoint threads, and leaves exactly one new
append-only `demo_reset` audit event behind (HC-11's only sanctioned exception).

No network beyond 127.0.0.1, no model calls: `DASHSCOPE_API_KEY` is removed
from the child environment. Usage:

    uv run --offline python scripts/ac016_reset.py artifacts/acceptance/TASK-010/ac016-demo-reset

Exit code 0 only when every check passed; the evidence JSON is written either way.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

S01 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
       "purpose": "供应商大会", "declared_budget_fen": 400000}
ZHANG, LISI, WANG = "u-zhangsan", "u-lisi", "u-wangwu"
# Tables that hold dynamic (per-run) data and must be empty after a reset.
DYNAMIC_TABLES = ["trips", "approvals", "outbox_emails", "idempotency_keys", "itineraries",
                  "uploads", "image_extractions"]


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def parse_sse(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = [line[5:].lstrip() for line in block.split("\n") if line.startswith("data:")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


class Check:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def __call__(self, name: str, ok: bool, observed: Any = None) -> bool:
        self.items.append({"check": name, "passed": bool(ok), "observed": observed})
        print(("PASS " if ok else "FAIL ") + name, flush=True)
        return bool(ok)

    @property
    def passed(self) -> bool:
        return bool(self.items) and all(item["passed"] for item in self.items)


class Server:
    def __init__(self, env: dict[str, str], port: int, log_dir: Path):
        self.env, self.port, self.log_dir = env, port, log_dir
        self.base = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.boots = 0

    def start(self) -> dict[str, Any]:
        self.boots += 1
        log = open(self.log_dir / f"boot-{self.boots}.log", "wb")  # noqa: SIM115 - owned by the child
        started = now()
        self.proc = subprocess.Popen([sys.executable, "-m", "travel_desk"], env=self.env, stdout=log,
                                     stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited during boot {self.boots} (see boot-{self.boots}.log)")
            try:
                if httpx.get(self.base + "/api/health", timeout=1).status_code == 200:
                    return {"boot": self.boots, "pid": self.proc.pid, "started_at": started,
                            "ready_at": now()}
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise RuntimeError(f"server not ready after 60s (boot {self.boots})")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=15)
        self.proc = None


def h(user: str, key: str | None = None) -> dict[str, str]:
    headers = {"X-Demo-User": user}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def db_snapshot(data_dir: Path) -> dict[str, Any]:
    """Direct read-only snapshot of both databases (no service involved)."""
    app = _connect_ro(data_dir / "app.sqlite")
    try:
        counts = {t: app.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in DYNAMIC_TABLES + ["audit_events", "faults", "employees",
                                             "policy_versions", "policy_rules"]}
        employees = [dict(r) for r in app.execute(
            "SELECT id, name, role, department, manager_id, preferences_json FROM employees ORDER BY id")]
        versions = [dict(r) for r in app.execute(
            "SELECT version, status, effective_date FROM policy_versions ORDER BY version")]
        rules_by_version = {r["version"]: r["n"] for r in app.execute(
            "SELECT version, COUNT(*) AS n FROM policy_rules GROUP BY version ORDER BY version")}
        faults = {r["key"]: bool(r["enabled"]) for r in app.execute(
            "SELECT key, enabled FROM faults ORDER BY key")}
        audit_types = [r["type"] for r in app.execute(
            "SELECT type FROM audit_events ORDER BY seq")]
        preferences_set = [e["id"] for e in employees if e["preferences_json"] not in ("{}", "")]
    finally:
        app.close()

    cp_path = data_dir / "checkpoints.sqlite"
    checkpoint_threads: list[str] = []
    checkpoint_rows = 0
    if cp_path.exists():
        cp = _connect_ro(cp_path)
        try:
            tables = {r["name"] for r in cp.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "checkpoints" in tables:
                checkpoint_rows = cp.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
                checkpoint_threads = sorted(
                    r[0] for r in cp.execute("SELECT DISTINCT thread_id FROM checkpoints"))
        finally:
            cp.close()

    return {"counts": counts, "employees": employees, "policy_versions": versions,
            "rules_by_version": rules_by_version, "faults": faults, "audit_event_types": audit_types,
            "employees_with_preferences": preferences_set,
            "checkpoint_rows": checkpoint_rows, "checkpoint_threads": checkpoint_threads}


def run(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ac016-"))
    data_dir = work / "var"
    port = free_port()
    env = {key: value for key, value in os.environ.items() if key != "DASHSCOPE_API_KEY"}
    env.update({"TRAVEL_DESK_DATA_DIR": str(data_dir), "TRAVEL_DESK_LLM_MODE": "fake",
                "TRAVEL_DESK_PORT": str(port), "TRAVEL_DESK_USAGE_LOG": str(work / "llm-usage.jsonl"),
                "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    server = Server(env, port, out)
    check = Check()
    evidence: dict[str, Any] = {"scenario": "AC-016 / REQ-025 Demo reset", "generated_at": now(),
                                "mode": "fake",
                                "start_command": "python -m travel_desk (= uv run travel-desk)",
                                "data_dir": "<tempdir>/var", "steps": []}
    step = evidence["steps"].append
    try:
        with httpx.Client(base_url=server.base, timeout=30) as client:
            step({"boot": server.start()})

            # ---- create dynamic data of every kind the contract names -------------
            # Trip A: S-01 → PENDING_APPROVAL → approve → DELIVERED (approval, itinerary, Outbox).
            trip_a = client.post("/api/trips", json={}, headers=h(ZHANG)).json()["trip"]["id"]
            client.patch(f"/api/trips/{trip_a}/slots", json={"expected_revision": 1, "slots": S01},
                         headers=h(ZHANG))
            submit_a = parse_sse(client.post(f"/api/trips/{trip_a}/submit", json={"expected_revision": 1},
                                             headers=h(ZHANG, "ac016-a")).text)
            check("Trip A 提交后 PENDING_APPROVAL", submit_a[-1]["payload"]["status"] == "PENDING_APPROVAL",
                  submit_a[-1]["payload"]["status"])
            decided = client.post(f"/api/trips/{trip_a}/decision",
                                  json={"revision": 1, "decision": "approve"},
                                  headers=h(LISI, "ac016-a-d"))
            check("Trip A 批准后 DELIVERED（生成交付邮件与行程单）",
                  decided.status_code == 200 and decided.json()["trip"]["status"] == "DELIVERED",
                  decided.json().get("trip", {}).get("status"))

            # Trip B: another S-01, left in PENDING_APPROVAL → a live checkpoint thread + pending card.
            trip_b = client.post("/api/trips", json={}, headers=h(ZHANG)).json()["trip"]["id"]
            client.patch(f"/api/trips/{trip_b}/slots", json={"expected_revision": 1, "slots": S01},
                         headers=h(ZHANG))
            submit_b = parse_sse(client.post(f"/api/trips/{trip_b}/submit", json={"expected_revision": 1},
                                             headers=h(ZHANG, "ac016-b")).text)
            check("Trip B 停在 PENDING_APPROVAL（活动 checkpoint 线程）",
                  submit_b[-1]["payload"]["status"] == "PENDING_APPROVAL", submit_b[-1]["payload"]["status"])

            # Preference (dynamic, per-employee).
            pref = client.put(f"/api/users/{ZHANG}/preferences",
                              json={"avoid_early_departure": True, "notes": "靠窗"}, headers=h(ZHANG))
            check("写入员工偏好", pref.status_code == 200
                  and pref.json()["preferences"]["avoid_early_departure"] is True, pref.json())

            # Fault injection (dynamic).
            fault = client.post("/api/demo/faults", json={"fault": "transport_timeout", "enabled": True},
                                headers=h(WANG))
            health_before = client.get("/api/health/providers", headers=h(WANG)).json()
            check("注入交通超时故障", fault.status_code == 200
                  and {p["name"]: p["status"] for p in health_before["providers"]}["transport"]
                  == "fault_injected",
                  {p["name"]: p["status"] for p in health_before["providers"]})
            step({"provider_health_before": health_before["providers"]})

            # ---- BEFORE snapshot --------------------------------------------------
            before = db_snapshot(data_dir)
            evidence["db_before"] = before
            step({"db_before_counts": before["counts"], "checkpoint_threads": before["checkpoint_threads"]})
            check("重置前存在动态数据（Trip/审批/Outbox/幂等键/行程单）",
                  before["counts"]["trips"] == 2 and before["counts"]["approvals"] >= 2
                  and before["counts"]["outbox_emails"] >= 1 and before["counts"]["idempotency_keys"] >= 1
                  and before["counts"]["itineraries"] >= 1, before["counts"])
            check("重置前审计已累积多条事件", before["counts"]["audit_events"] > 1,
                  before["counts"]["audit_events"])
            check("重置前偏好已写入张三", before["employees_with_preferences"] == [ZHANG],
                  before["employees_with_preferences"])
            check("重置前故障开关为 transport_timeout=True",
                  before["faults"].get("transport_timeout") is True, before["faults"])
            check("重置前存在活动 checkpoint 线程", len(before["checkpoint_threads"]) >= 2,
                  before["checkpoint_threads"])
            check("重置前种子完整（4 员工、v1.0+v1.2）",
                  before["counts"]["employees"] == 4
                  and {v["version"]: v["status"] for v in before["policy_versions"]}
                  == {"v1.0": "archived", "v1.2": "published"},
                  before["policy_versions"])

            # ---- authorisation & confirmation gates -------------------------------
            as_employee = client.post("/api/demo/reset", json={"confirmation": "RESET_DEMO_DATA"},
                                      headers=h(ZHANG))
            as_manager = client.post("/api/demo/reset", json={"confirmation": "RESET_DEMO_DATA"},
                                     headers=h(LISI))
            check("员工重置被拒 403", as_employee.status_code == 403, as_employee.status_code)
            check("经理重置被拒 403（仅 admin）", as_manager.status_code == 403, as_manager.status_code)
            step({"reset_403_employee": as_employee.json(), "reset_403_manager": as_manager.json()})

            missing = client.post("/api/demo/reset", json={}, headers=h(WANG))
            wrong = client.post("/api/demo/reset", json={"confirmation": "RESET"}, headers=h(WANG))
            check("缺确认串 422 CONFIRMATION_REQUIRED",
                  missing.status_code == 422 and missing.json()["error"]["code"] == "CONFIRMATION_REQUIRED",
                  missing.json()["error"]["code"])
            check("错误确认串 422", wrong.status_code == 422
                  and wrong.json()["error"]["code"] == "CONFIRMATION_REQUIRED",
                  wrong.json()["error"]["code"])
            # The failed attempts must not have reset anything.
            still = db_snapshot(data_dir)
            check("被拒/无效重置没有副作用（Trip 仍在）", still["counts"]["trips"] == 2,
                  still["counts"]["trips"])

            # ---- the real reset ---------------------------------------------------
            done = client.post("/api/demo/reset", json={"confirmation": "RESET_DEMO_DATA"}, headers=h(WANG))
            check("管理员正确确认 → 200 {status: reset}",
                  done.status_code == 200 and done.json() == {"status": "reset"}, done.json())

            after = db_snapshot(data_dir)
            evidence["db_after"] = after
            step({"db_after_counts": after["counts"], "checkpoint_threads": after["checkpoint_threads"]})

            # dynamic data cleared
            cleared = {t: after["counts"][t] for t in DYNAMIC_TABLES}
            check("动态数据全部清空（Trip/审批/Outbox/幂等键/行程单/上传/图片抽取）",
                  all(v == 0 for v in cleared.values()), cleared)
            check("故障开关清空（transport_timeout 关闭）",
                  after["faults"].get("transport_timeout") is False, after["faults"])
            check("偏好清空（无员工保留偏好）", after["employees_with_preferences"] == [],
                  after["employees_with_preferences"])
            check("checkpoint 线程全部删除", after["checkpoint_threads"] == []
                  and after["checkpoint_rows"] == 0,
                  {"threads": after["checkpoint_threads"], "rows": after["checkpoint_rows"]})

            # seed restored
            check("种子员工恢复（4 人，角色/部门/经理关系一致）",
                  [(e["id"], e["role"]) for e in after["employees"]]
                  == [(e["id"], e["role"]) for e in before["employees"]]
                  and after["counts"]["employees"] == 4,
                  [(e["id"], e["role"]) for e in after["employees"]])
            check("政策版本恢复（v1.0 archived + v1.2 published）",
                  {v["version"]: v["status"] for v in after["policy_versions"]}
                  == {"v1.0": "archived", "v1.2": "published"}, after["policy_versions"])
            check("种子规则按版本恢复（与重置前同版本同数量）",
                  after["rules_by_version"] == before["rules_by_version"], after["rules_by_version"])

            # audit: append-only, only the sanctioned demo_reset event survives (HC-11 exception)
            check("重置后审计只剩 1 条 demo_reset（既有事件按 REQ-025 清除）",
                  after["counts"]["audit_events"] == 1
                  and after["audit_event_types"] == ["demo_reset"], after["audit_event_types"])

            # fixtures restored: providers back to ok (fault cleared), fixtures are read-only sources
            health_after = client.get("/api/health/providers", headers=h(WANG)).json()
            status_after = {p["name"]: p["status"] for p in health_after["providers"]}
            step({"provider_health_after": health_after["providers"]})
            check("Provider 恢复 ok（故障清除，fixture 为只读源，重置不改素材）",
                  status_after.get("transport") == "ok" and all(s == "ok" for s in status_after.values()),
                  status_after)

            # the reset really took effect through the API surface too
            trips_after = client.get("/api/trips", headers=h(WANG)).json()["trips"]
            gone = client.get(f"/api/trips/{trip_a}", headers=h(ZHANG))
            check("API 层确认 Trip 已消失（列表空、详情 404）",
                  trips_after == [] and gone.status_code == 404,
                  {"list": trips_after, "detail": gone.status_code})
            todo_after = client.get("/api/approvals?status=pending", headers=h(LISI)).json()["approvals"]
            check("API 层确认审批待办已清空", todo_after == [], todo_after)
            outbox_after = client.get("/api/outbox", headers=h(WANG)).json()["emails"]
            check("API 层确认 Outbox 已清空", outbox_after == [], outbox_after)

            # admin read-only endpoints above must not have appended any audit event
            audit_final = db_snapshot(data_dir)
            check("管理员只读接口未新增审计（仍为 1 条 demo_reset）",
                  audit_final["counts"]["audit_events"] == 1
                  and audit_final["audit_event_types"] == ["demo_reset"], audit_final["audit_event_types"])
    except Exception as exc:  # noqa: BLE001 - record and fail, never pass silently
        check("script completed without exception", False, f"{type(exc).__name__}: {exc}")
    finally:
        server.stop()
        evidence["checks"] = check.items
        evidence["passed"] = check.passed
        (out / "AC-016-reset.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
    print("AC-016", "PASSED" if check.passed else "FAILED", flush=True)
    return 0 if check.passed else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: ac016_reset.py <out_dir>")
    sys.exit(run(Path(sys.argv[1])))
