"""Shared helpers for the end-to-end evidence scripts (AC-012, AC-019, AC-021).

The scripts drive a **real server subprocess** started with the documented
command (`python -m travel_desk`, the same entry point as `uv run travel-desk`)
over HTTP/SSE, so what is measured is the shipped process — including the egress
guard installed by its lifespan — and not an in-process test client.

Fake LLM mode, fixed `DEMO_TODAY`, throwaway data directory, `DASHSCOPE_API_KEY`
removed from the child environment: no model cost and no external network
(HC-06). Usage of the scripts is documented in each of them.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
INVITATION = REPO_ROOT / "tests" / "fixtures" / "invitations" / "shanghai_supplier_conference.png"

ZHANG, LISI, ZHAO, WANG = "u-zhangsan", "u-lisi", "u-zhaoliu", "u-wangwu"
RESET_CONFIRMATION = "RESET_DEMO_DATA"

S01 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
       "purpose": "供应商大会", "declared_budget_fen": 400000}
S02 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-25", "end_date": "2026-10-26",
       "purpose": "客户拜访", "declared_budget_fen": 250000}
S03 = {"origin": "北京", "destination": "成都", "start_date": "2026-10-27", "end_date": "2026-10-29",
       "purpose": "培训", "declared_budget_fen": 400000}
S04 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-22", "end_date": "2026-10-23",
       "purpose": "紧急会议", "declared_budget_fen": 300000}
S06 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-19", "end_date": "2026-10-20",
       "purpose": "客户拜访", "declared_budget_fen": 250000}
S06_FIXED = {**S06, "start_date": "2026-10-25", "end_date": "2026-10-26"}


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse frames strictly: id/event/data must be present and agree (§6.7)."""
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            fields[name] = value[1:] if value.startswith(" ") else value
        if not fields:
            continue  # trailing blank block after the last frame
        if set(fields) != {"id", "event", "data"}:
            raise AssertionError(f"malformed SSE frame: {fields}")
        event = json.loads(fields["data"])
        if event["type"] != fields["event"] or event["event_id"] != fields["id"]:
            raise AssertionError(f"SSE frame disagrees with its envelope: {fields} {event['type']}")
        events.append(event)
    return events


class Check:
    """Collects named assertions; a scenario passes only if every one of them did."""

    def __init__(self, echo: bool = True):
        self.items: list[dict[str, Any]] = []
        self.echo = echo

    def __call__(self, name: str, ok: bool, observed: Any = None) -> bool:
        self.items.append({"check": name, "passed": bool(ok), "observed": observed})
        if self.echo:
            print(("  PASS " if ok else "  FAIL ") + name, flush=True)
        return bool(ok)

    @property
    def passed(self) -> bool:
        return bool(self.items) and all(item["passed"] for item in self.items)

    def take(self) -> list[dict[str, Any]]:
        items, self.items = self.items, []
        return items


class Server:
    """The service under test, as a real process on 127.0.0.1."""

    def __init__(self, work: Path, out: Path, *, llm_mode: str = "fake", port: int | None = None,
                 extra_env: dict[str, str] | None = None):
        self.work = work
        self.data_dir = work / "var"
        self.out = out
        self.port = port or free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self.boots = 0
        env = {key: value for key, value in os.environ.items() if key != "DASHSCOPE_API_KEY"}
        env.update({"TRAVEL_DESK_DATA_DIR": str(self.data_dir), "TRAVEL_DESK_LLM_MODE": llm_mode,
                    "TRAVEL_DESK_PORT": str(self.port), "TRAVEL_DESK_HOST": "127.0.0.1",
                    "TRAVEL_DESK_USAGE_LOG": str(work / "llm-usage.jsonl"),
                    "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        self.env = {**env, **(extra_env or {})}

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> dict[str, Any]:
        self.boots += 1
        log = open(self.out / f"boot-{self.boots}.log", "wb")  # noqa: SIM115 - owned by the child
        started = now()
        self.proc = subprocess.Popen([sys.executable, "-m", "travel_desk"], env=self.env, cwd=REPO_ROOT,
                                     stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 90
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
        raise RuntimeError(f"server not ready after 90s (boot {self.boots})")

    def hard_kill(self) -> dict[str, Any]:
        assert self.proc is not None
        pid, at = self.proc.pid, now()
        if os.name == "nt":
            # /T: a venv python.exe on Windows may be a launcher whose child is the real interpreter.
            done = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            method, rc = "taskkill /F /T /PID", done.returncode
        else:
            self.proc.send_signal(signal.SIGKILL)
            method, rc = "SIGKILL", 0
        exit_code = self.proc.wait(timeout=20)
        try:
            httpx.get(self.base + "/api/health", timeout=1)
            closed = False
        except httpx.HTTPError:
            closed = True
        self.proc = None
        return {"pid": pid, "method": method, "killed_at": at, "tool_returncode": rc,
                "process_exit_code": exit_code, "port_closed_after_kill": closed}

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    @property
    def egress_log(self) -> Path:
        return self.data_dir / "network-guard.jsonl"

    def boot_log(self, boot: int) -> str:
        return (self.out / f"boot-{boot}.log").read_text(encoding="utf-8", errors="replace")


class Desk:
    """HTTP/SSE client for one scenario run, with the contract's expectations as helpers."""

    def __init__(self, client: httpx.Client, server: Server):
        self.client = client
        self.server = server
        self._keys = 0

    # ------------------------------------------------------------------ primitives

    def key(self, prefix: str) -> str:
        self._keys += 1
        return f"{prefix}-{self._keys}"

    @staticmethod
    def headers(user: str | None = None, key: str | None = None) -> dict[str, str]:
        headers = {}
        if user:
            headers["X-Demo-User"] = user
        if key:
            headers["Idempotency-Key"] = key
        return headers

    def get(self, path: str, user: str | None = WANG, **params) -> httpx.Response:
        return self.client.get(path, headers=self.headers(user), params=params or None)

    def post(self, path: str, body: Any = None, user: str | None = WANG, key: str | None = None,
             **kwargs) -> httpx.Response:
        return self.client.post(path, json=body, headers=self.headers(user, key), **kwargs)

    def patch(self, path: str, body: Any, user: str | None = WANG) -> httpx.Response:
        return self.client.patch(path, json=body, headers=self.headers(user))

    def stream(self, path: str, body: Any, user: str, key: str | None = None) -> list[dict[str, Any]]:
        response = self.post(path, body, user=user, key=key)
        if response.status_code != 200:
            raise AssertionError(f"{path} -> {response.status_code}: {response.text[:400]}")
        if not response.headers["content-type"].startswith("text/event-stream"):
            raise AssertionError(f"{path} is not an SSE stream: {response.headers['content-type']}")
        events = parse_sse(response.text)
        terminals = [e for e in events if e["type"] in ("done", "error")]
        if len(terminals) != 1 or events[-1] is not terminals[0]:
            raise AssertionError(f"{path} must end with exactly one terminal event: "
                                 f"{[e['type'] for e in events]}")
        return events

    # ------------------------------------------------------------------ use cases

    def reset(self, user: str | None = WANG, confirmation: str = RESET_CONFIRMATION) -> httpx.Response:
        return self.post("/api/demo/reset", {"confirmation": confirmation}, user=user)

    def fault(self, name: str, enabled: bool) -> httpx.Response:
        return self.post("/api/demo/faults", {"fault": name, "enabled": enabled})

    def create_trip(self, user: str = ZHANG, text: str | None = None) -> dict[str, Any]:
        response = self.post("/api/trips", {"text": text} if text else {}, user=user)
        assert response.status_code == 201, response.text
        return response.json()

    def fill(self, trip_id: str, slots: dict[str, Any], revision: int = 1, user: str = ZHANG) -> dict:
        response = self.patch(f"/api/trips/{trip_id}/slots", {"expected_revision": revision, "slots": slots},
                              user=user)
        assert response.status_code == 200, response.text
        return response.json()

    def trip(self, trip_id: str, user: str = ZHANG) -> dict[str, Any]:
        response = self.get(f"/api/trips/{trip_id}", user=user)
        assert response.status_code == 200, response.text
        return response.json()

    def audit(self, trip_id: str, user: str = ZHANG) -> list[dict[str, Any]]:
        response = self.get(f"/api/trips/{trip_id}/audit", user=user)
        assert response.status_code == 200, response.text
        return response.json()["events"]

    def events_of(self, trip_id: str, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.audit(trip_id) if e["type"] == event_type]

    def trajectory(self, trip_id: str) -> list[str]:
        """The state trajectory AC-019 asks for, from the append-only audit log."""
        return [e["payload"]["to"] for e in self.events_of(trip_id, "status_changed")]

    def submit(self, trip_id: str, revision: int = 1, user: str = ZHANG) -> list[dict[str, Any]]:
        return self.stream(f"/api/trips/{trip_id}/submit", {"expected_revision": revision}, user=user,
                           key=self.key("submit"))

    def message(self, trip_id: str, text: str, revision: int = 1, user: str = ZHANG):
        return self.stream(f"/api/trips/{trip_id}/messages", {"text": text, "expected_revision": revision},
                           user=user, key=self.key("msg"))

    def decide(self, trip_id: str, revision: int, decision: str, comment: str | None = None,
               user: str = LISI, key: str | None = None) -> httpx.Response:
        body: dict[str, Any] = {"revision": revision, "decision": decision}
        if comment is not None:
            body["comment"] = comment
        return self.post(f"/api/trips/{trip_id}/decision", body, user=user, key=key or self.key("decision"))

    def revalidate(self, trip_id: str, revision: int, user: str = ZHANG) -> httpx.Response:
        return self.post(f"/api/trips/{trip_id}/revalidate", {"revision": revision}, user=user,
                         key=self.key("revalidate"))

    def outbox(self, trip_id: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if trip_id:
            params["trip_id"] = trip_id
        if kind:
            params["kind"] = kind
        response = self.get("/api/outbox", user=WANG, **params)
        assert response.status_code == 200, response.text
        return response.json()["emails"]

    def pending_approvals(self, user: str = LISI) -> list[dict[str, Any]]:
        response = self.get("/api/approvals", user=user, status="pending")
        assert response.status_code == 200, response.text
        return response.json()["approvals"]

    # ------------------------------------------------------------------ policy compile (S-12)

    def upload_policy(self, version: str, document: str, *,
                      effective_date: str = "2026-11-01") -> httpx.Response:
        content = (REPO_ROOT / "tests" / "fixtures" / "policies" / document).read_bytes()
        return self.client.post("/api/policies", headers=self.headers(WANG),
                                data={"version": version, "effective_date": effective_date},
                                files={"file": (document, content, "application/octet-stream")})

    def extract_policy(self, version: str) -> httpx.Response:
        return self.post(f"/api/policies/{version}/extract", {})

    def review_policy(self, version: str, closed_ids: list[str], reject_ids: list[str]) -> list[int]:
        codes = []
        for rule_id in closed_ids:
            codes.append(self.patch(f"/api/policies/{version}/rules/{rule_id}",
                                    {"status": "confirmed"}).status_code)
        for rule_id in reject_ids:
            codes.append(self.patch(f"/api/policies/{version}/rules/{rule_id}",
                                    {"status": "rejected"}).status_code)
        return codes

    def publish_policy(self, version: str) -> httpx.Response:
        return self.post(f"/api/policies/{version}/publish", None, key=self.key("publish"))

    # ------------------------------------------------------------------ image entry (S-11)

    def upload_image(self, path: Path = INVITATION, user: str = ZHANG) -> dict[str, Any]:
        with path.open("rb") as handle:
            response = self.client.post("/api/uploads", headers=self.headers(user),
                                        files={"file": (path.name, handle.read(), "image/png")})
        assert response.status_code == 201, response.text
        return response.json()

    def extract_image(self, trip_id: str, upload_id: str, user: str = ZHANG) -> httpx.Response:
        return self.post(f"/api/trips/{trip_id}/image-extractions", {"upload_id": upload_id}, user=user)

    def confirm_image(self, trip_id: str, extraction_id: str, fields: dict[str, Any],
                      revision: int = 1, user: str = ZHANG) -> httpx.Response:
        return self.post(f"/api/trips/{trip_id}/image-extractions/{extraction_id}/confirm",
                         {"expected_revision": revision, "fields": fields}, user=user)


def temp_work(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def codes(items: list[dict[str, Any]], key: str = "code") -> list[str]:
    return [item[key] for item in items]
