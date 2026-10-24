"""AC-010 evidence: S-10 with a real process kill and restart (contract §7.2 S-10, REQ-017).

Starts the service with the documented start command (`python -m travel_desk`,
the same entry point as `uv run travel-desk`) on a throwaway data directory in
Fake mode, takes S-01 to PENDING_APPROVAL over HTTP/SSE, hard-kills the process
tree (`taskkill /F /T` on Windows, SIGKILL elsewhere), restarts it on the same
databases, approves, and checks the trip is delivered exactly once. A second
hard kill after delivery and a third boot prove startup recovery does not
deliver again.

No network beyond 127.0.0.1, no model calls: `DASHSCOPE_API_KEY` is removed
from the child environment. Usage:

    uv run --offline python scripts/ac010_restart.py artifacts/acceptance/TASK-006/AC-010

Exit code 0 only when every check passed; the evidence JSON is written either way.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
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

    def hard_kill(self) -> dict[str, Any]:
        assert self.proc is not None
        pid, at = self.proc.pid, now()
        if os.name == "nt":
            # /T: a venv python.exe on Windows may be a launcher whose child is the real interpreter.
            done = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            method = "taskkill /F /T /PID"
            tool_rc = done.returncode
        else:
            self.proc.send_signal(signal.SIGKILL)
            method, tool_rc = "SIGKILL", 0
        exit_code = self.proc.wait(timeout=15)
        try:
            httpx.get(self.base + "/api/health", timeout=1)
            port_closed = False
        except httpx.HTTPError:
            port_closed = True
        self.proc = None
        return {"pid": pid, "method": method, "killed_at": at, "tool_returncode": tool_rc,
                "process_exit_code": exit_code, "port_closed_after_kill": port_closed}

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


def db_snapshot(data_dir: Path, trip_id: str, thread_id: str | None) -> dict[str, Any]:
    """Direct read-only queries against the persisted databases (no service involved)."""
    app = sqlite3.connect(f"file:{data_dir / 'app.sqlite'}?mode=ro", uri=True)
    app.row_factory = sqlite3.Row
    try:
        trip = app.execute("SELECT status, revision, thread_id FROM trips WHERE id = ?",
                           (trip_id,)).fetchone()
        approvals = [dict(r) for r in app.execute(
            "SELECT revision, status, decision, approver_id FROM approvals WHERE trip_id = ?"
            " ORDER BY revision", (trip_id,))]
        outbox = {r["kind"]: r["n"] for r in app.execute(
            "SELECT kind, COUNT(*) AS n FROM outbox_emails WHERE trip_id = ? GROUP BY kind", (trip_id,))}
        itineraries = app.execute("SELECT COUNT(*) FROM itineraries WHERE trip_id = ?",
                                  (trip_id,)).fetchone()[0]
    finally:
        app.close()
    checkpoints = None
    if thread_id:
        cp = sqlite3.connect(f"file:{data_dir / 'checkpoints.sqlite'}?mode=ro", uri=True)
        try:
            checkpoints = cp.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                                     (thread_id,)).fetchone()[0]
        finally:
            cp.close()
    return {"trip": dict(trip) if trip else None, "approvals": approvals, "outbox_by_kind": outbox,
            "itineraries": itineraries, "checkpoint_rows_for_thread": checkpoints}


def run(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ac010-"))
    data_dir = work / "var"
    port = free_port()
    env = {key: value for key, value in os.environ.items() if key != "DASHSCOPE_API_KEY"}
    env.update({"TRAVEL_DESK_DATA_DIR": str(data_dir), "TRAVEL_DESK_LLM_MODE": "fake",
                "TRAVEL_DESK_PORT": str(port), "TRAVEL_DESK_USAGE_LOG": str(work / "llm-usage.jsonl"),
                "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    server = Server(env, port, out)
    check = Check()
    evidence: dict[str, Any] = {"scenario": "S-10 / AC-010", "generated_at": now(), "mode": "fake",
                                "start_command": "python -m travel_desk (= uv run travel-desk)",
                                "data_dir": "<tempdir>/var", "steps": []}
    step = evidence["steps"].append
    try:
        with httpx.Client(base_url=server.base, timeout=30) as client:
            step({"boot": server.start()})
            trip_id = client.post("/api/trips", json={}, headers=h(ZHANG)).json()["trip"]["id"]
            patched = client.patch(f"/api/trips/{trip_id}/slots", json={"expected_revision": 1, "slots": S01},
                                   headers=h(ZHANG))
            check("slots filled", patched.status_code == 200, patched.status_code)
            submit = client.post(f"/api/trips/{trip_id}/submit", json={"expected_revision": 1},
                                 headers=h(ZHANG, "ac010-submit"))
            events = parse_sse(submit.text)
            step({"submit_events": [e["type"] for e in events], "terminal": events[-1]["payload"]})
            check("submit stream ends PENDING_APPROVAL", events[-1]["type"] == "done"
                  and events[-1]["payload"]["status"] == "PENDING_APPROVAL", events[-1]["payload"])
            thread_id = client.get(f"/api/trips/{trip_id}", headers=h(ZHANG)).json()["thread_id"]
            before = db_snapshot(data_dir, trip_id, thread_id)
            step({"db_before_kill": before})
            check("checkpoint persisted before kill", (before["checkpoint_rows_for_thread"] or 0) > 0,
                  before["checkpoint_rows_for_thread"])

            kill = server.hard_kill()
            step({"kill_1": kill})
            check("process really terminated", kill["port_closed_after_kill"], kill)
            after_kill = db_snapshot(data_dir, trip_id, thread_id)
            step({"db_after_kill": after_kill})
            check("pending approval survives the kill", after_kill["trip"]["status"] == "PENDING_APPROVAL"
                  and [a["status"] for a in after_kill["approvals"]] == ["pending"], after_kill["trip"])

            step({"boot": server.start()})
            boot2_log = (out / "boot-2.log").read_text(encoding="utf-8", errors="replace")
            check("startup recovery ran", "startup recovery" in boot2_log)
            trip = client.get(f"/api/trips/{trip_id}", headers=h(ZHANG)).json()
            check("trip still PENDING_APPROVAL after restart", trip["status"] == "PENDING_APPROVAL",
                  trip["status"])
            todo = client.get("/api/approvals?status=pending", headers=h(LISI)).json()["approvals"]
            check("manager's to-do list not lost", [a["trip_id"] for a in todo] == [trip_id], len(todo))

            body = {"revision": 1, "decision": "approve"}
            decided = client.post(f"/api/trips/{trip_id}/decision", json=body, headers=h(LISI, "ac010-d1"))
            step({"decision": {"status_code": decided.status_code,
                               "trip_status": decided.json().get("trip", {}).get("status")}})
            check("approve after restart delivers", decided.status_code == 200
                  and decided.json()["trip"]["status"] == "DELIVERED", decided.status_code)
            replay = client.post(f"/api/trips/{trip_id}/decision", json=body, headers=h(LISI, "ac010-d1"))
            check("same-key replay returns the stored response", replay.json() == decided.json())
            audit = client.get(f"/api/trips/{trip_id}/audit", headers=h(ZHANG)).json()["events"]
            vias = [e["payload"].get("via") for e in audit if e["type"] == "decision_recorded"]
            check("decision resumed the checkpoint interrupt (via=graph)", vias == ["graph"], vias)
            delivered = db_snapshot(data_dir, trip_id, thread_id)
            step({"db_after_approve": delivered})
            check("exactly one trip_delivery", delivered["outbox_by_kind"].get("trip_delivery") == 1,
                  delivered["outbox_by_kind"])

            step({"kill_2": server.hard_kill()})
            step({"boot": server.start()})
            final = db_snapshot(data_dir, trip_id, thread_id)
            step({"db_after_second_restart": final})
            check("no second delivery after another restart",
                  final["outbox_by_kind"].get("trip_delivery") == 1 and final["itineraries"] == 1
                  and final["trip"]["status"] == "DELIVERED", final["outbox_by_kind"])
            emails = client.get(f"/api/outbox?trip_id={trip_id}", headers=h(WANG)).json()["emails"]
            check("outbox API agrees", [e["kind"] for e in emails] == ["trip_delivery"],
                  [e["kind"] for e in emails])
    except Exception as exc:  # noqa: BLE001 - record and fail, never pass silently
        check("script completed without exception", False, f"{type(exc).__name__}: {exc}")
    finally:
        server.stop()
        evidence["checks"] = check.items
        evidence["passed"] = check.passed
        (out / "AC-010-restart.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
    print("AC-010", "PASSED" if check.passed else "FAILED", flush=True)
    return 0 if check.passed else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: ac010_restart.py <out_dir>")
    sys.exit(run(Path(sys.argv[1])))
