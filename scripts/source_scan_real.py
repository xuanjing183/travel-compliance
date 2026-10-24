"""AC-015 real-mode part: scan the API and audit JSON of a real-model run for `source` (TASK-007).

Same scan as `tests/test_sources.py`, but with `llm_mode=real`, so the itinerary summaries
and the policy answer come from DashScope (AUTH-01, metered into `artifacts/llm-usage.jsonl`;
run `scripts/ledger_summary.py --write` afterwards). Slots are set with PATCH, so no
slot-extraction call is made: model calls are 2 summaries plus 1 question (embeddings + answer).

Runs in-process (`TestClient`) on a throwaway data directory. Only the *name*
`DASHSCOPE_API_KEY` is checked; its value is never read here. Usage:

    uv run --offline python scripts/source_scan_real.py artifacts/acceptance/TASK-007/AC-015-real
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

sys.path.insert(0, str(REPO_ROOT / "tests"))
from test_api import LISI, S01, ZHANG, h, stream, trip_with  # noqa: E402
from test_sources import S03, scan  # noqa: E402

QUESTION = "上海住宿标准是多少"


def main(out: Path) -> int:
    if "DASHSCOPE_API_KEY" not in os.environ:
        sys.exit("DASHSCOPE_API_KEY is not set (only the name is checked)")
    work = Path(tempfile.mkdtemp(prefix="source-scan-real-")) / "var"
    settings = replace(get_settings(), llm_mode="real", data_dir=work, db_path=work / "app.sqlite",
                       checkpoint_db_path=work / "checkpoints.sqlite", upload_dir=work / "uploads")
    docs: dict[str, Any] = {}
    with TestClient(create_app(settings)) as client:
        def get(url: str, user: str = ZHANG) -> Any:
            res = client.get(url, headers=h(user))
            assert res.status_code == 200, res.text
            return res.json()

        s01 = trip_with(client, {**S01, "meeting_address": "上海市浦东新区滨江大道2727号"})
        stream(client, f"/api/trips/{s01}/submit", {"expected_revision": 1}, user=ZHANG, key="real-src-s01")
        s03 = trip_with(client, {**S03, "meeting_address": "成都市高新区天府大道1000号"})
        stream(client, f"/api/trips/{s03}/submit", {"expected_revision": 1}, user=ZHANG, key="real-src-s03")
        docs["approvals-pending"] = get("/api/approvals?status=pending", LISI)
        decided = client.post(f"/api/trips/{s01}/decision", json={"revision": 1, "decision": "approve"},
                              headers=h(LISI, "real-src-d1"))
        assert decided.status_code == 200, decided.text
        docs["S01-trip"] = get(f"/api/trips/{s01}")
        docs["S01-itinerary"] = get(f"/api/trips/{s01}/itinerary")
        docs["S01-audit"] = get(f"/api/trips/{s01}/audit")
        docs["S03-trip"] = get(f"/api/trips/{s03}")
        docs["S03-audit"] = get(f"/api/trips/{s03}/audit")
        answer = client.post("/api/policy/ask", json={"question": QUESTION}, headers=h(ZHANG))
        assert answer.status_code == 200, answer.text
        docs["policy-answer"] = answer.json()

    out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                               "llm_mode": "real", "text_model": settings.text_model,
                               "embedding_model": settings.embedding_model, "question": QUESTION,
                               "documents": {}}
    failed = False
    for name, doc in docs.items():
        text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        (out / f"{name}.json").write_text(text, encoding="utf-8")
        seen, problems = scan(doc)
        failed = failed or bool(problems)
        summary["documents"][name] = {"sources": sorted(seen), "problems": problems}
    trip01, answer_body = docs["S01-trip"], docs["policy-answer"]
    summary["S01"] = {"status": trip01["status"], "summary_generator": trip01["plan"]["summary"]["generator"],
                      "summary_source": trip01["plan"]["summary"]["source"]}
    summary["S03"] = {"status": docs["S03-trip"]["status"],
                      "hotel_source": docs["S03-trip"]["plan"]["hotel"]["candidate"]["source"]}
    summary["policy_answer"] = {k: answer_body.get(k) for k in ("answerable", "source", "generator")}
    summary["policy_answer"]["citation_sources"] = sorted({c["source"] for c in answer_body["citations"]})
    summary["result"] = "fail" if failed else "pass"
    summary_text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    (out / "summary.json").write_text(summary_text, encoding="utf-8")
    print(summary_text)
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: source_scan_real.py <out-dir>")
    sys.exit(main(Path(sys.argv[1])))
