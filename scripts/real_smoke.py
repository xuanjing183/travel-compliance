"""Real-model smoke test of the HTTP paths that call DashScope (AUTH-01, metered; TASK-006).

Runs the real app in-process (`TestClient`) with `llm_mode=real` on a throwaway
data directory and exercises each model-backed path once:

1. `POST /api/trips {text}`        → slot extraction (text model)
2. `POST /messages` (SSE)          → slot extraction for a follow-up turn
3. upload + `image-extractions`    → invitation extraction (vision model)
4. `POST /submit` (SSE) for S-01   → itinerary summary (text model)

Every call is guarded and metered by `UsageMeter` into `artifacts/llm-usage.jsonl`
(run `scripts/ledger_summary.py --write` afterwards). Only the *name*
`DASHSCOPE_API_KEY` is checked; its value is never read here. Usage:

    uv run --offline python scripts/real_smoke.py artifacts/acceptance/TASK-006/real-smoke.json
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from travel_desk.config import REPO_ROOT, get_settings
from travel_desk.main import create_app

INVITATION = REPO_ROOT / "tests" / "fixtures" / "invitations" / "shanghai_supplier_conference.png"
ZHANG = {"X-Demo-User": "u-zhangsan"}


def sse(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = [line[5:].lstrip() for line in block.split("\n") if line.startswith("data:")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


def main(out: Path) -> int:
    if "DASHSCOPE_API_KEY" not in os.environ:
        sys.exit("DASHSCOPE_API_KEY is not set (only the name is checked)")
    work = Path(tempfile.mkdtemp(prefix="real-smoke-")) / "var"
    settings = replace(get_settings(), llm_mode="real", data_dir=work, db_path=work / "app.sqlite",
                       checkpoint_db_path=work / "checkpoints.sqlite", upload_dir=work / "uploads")
    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                              "llm_mode": "real", "text_model": settings.text_model,
                              "vision_model": settings.vision_model, "steps": {}}
    steps = report["steps"]
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/trips", json={"text": "下周去杭州出差，预算四千元"}, headers=ZHANG)
        body = created.json()
        assistant = body.get("assistant") or {}
        steps["create_with_text"] = {"status_code": created.status_code,
                                     "generator": assistant.get("generator"),
                                     "error": assistant.get("error"), "slots": body["trip"]["slots"],
                                     "missing_fields": assistant.get("missing_fields"),
                                     "ask": assistant.get("text")}
        trip_id = body["trip"]["id"]

        reply = client.post(f"/api/trips/{trip_id}/messages",
                            json={"text": "从北京出发，10月28日到10月30日，参加供应商大会",
                                  "expected_revision": 1},
                            headers=ZHANG)
        events = sse(reply.text)
        slots = next((e["payload"] for e in events if e["type"] == "slots"), {})
        steps["message_turn"] = {"event_types": [e["type"] for e in events],
                                 "terminal": events[-1]["payload"] if events else None,
                                 "generator": slots.get("generator"), "changes": slots.get("changes"),
                                 "missing_fields": slots.get("missing_fields")}

        other = client.post("/api/trips", json={}, headers=ZHANG).json()["trip"]["id"]
        uploaded = client.post("/api/uploads", headers=ZHANG,
                               files={"file": ("invite.png", INVITATION.read_bytes(), "image/png")}).json()
        extracted = client.post(f"/api/trips/{other}/image-extractions", json={"upload_id": uploaded["id"]},
                                headers=ZHANG)
        result = extracted.json()
        if extracted.status_code == 201:
            result = {k: result.get(k) for k in ("generator", "fields", "status")}
        steps["image_extraction"] = {"status_code": extracted.status_code, **result}

        filled = client.patch(f"/api/trips/{trip_id}/slots", headers=ZHANG,
                              json={"expected_revision": 1,
                                    "slots": {"destination": "上海", "declared_budget_fen": 400000}})
        submit = client.post(f"/api/trips/{trip_id}/submit", json={"expected_revision": 1},
                             headers={**ZHANG, "Idempotency-Key": "real-smoke-submit"})
        events = sse(submit.text)
        plan = next((e["payload"] for e in events if e["type"] == "plan"), {})
        steps["submit_summary"] = {"patch_status": filled.status_code,
                                   "event_types": [e["type"] for e in events],
                                   "terminal": events[-1]["payload"] if events else None,
                                   "summary": plan.get("summary")}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: real_smoke.py <out.json>")
    sys.exit(main(Path(sys.argv[1])))
