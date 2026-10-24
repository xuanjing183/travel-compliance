"""Real-model smoke test of policy Q&A #18 (AUTH-01, metered; TASK-008).

Not an evaluation: three hand-picked questions check that the real path works end
to end — DashScope embeddings (`settings.embedding_model`, 1024 dims, retrieval
`settings.retrieval_mode`) and the text model's structured draft — and that the
code-side citation checks hold on real output. AC-004/017/025 metrics come only
from the frozen 25-question set (TASK-011, D-17), never from this script.

Runs the app in-process (`TestClient`, `llm_mode=real`) on a throwaway data
directory; every call is metered into `artifacts/llm-usage.jsonl`. Only the name
`DASHSCOPE_API_KEY` is checked, never its value. Usage:

    uv run --offline python scripts/policy_qa_smoke.py artifacts/acceptance/TASK-008/qa-real-smoke.json
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

from travel_desk.config import get_settings
from travel_desk.main import create_app

ZHANG = {"X-Demo-User": "u-zhangsan"}
QUESTIONS = [
    {"question": "员工去上海出差，住宿每晚最多能报多少？", "expect": "answerable", "clauses": ["POL-2"]},
    {"question": "经理去成都出差，酒店每晚上限是多少？", "expect": "answerable",
     "clauses": ["POL-1", "POL-2"]},
    {"question": "公司每年有几天年假？", "expect": "refusal", "clauses": []},
]


def usage_lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(out: Path) -> int:
    if "DASHSCOPE_API_KEY" not in os.environ:
        sys.exit("DASHSCOPE_API_KEY is not set (only the name is checked)")
    work = Path(tempfile.mkdtemp(prefix="qa-smoke-")) / "var"
    settings = replace(get_settings(), llm_mode="real", data_dir=work, db_path=work / "app.sqlite",
                       checkpoint_db_path=work / "checkpoints.sqlite", upload_dir=work / "uploads")
    before = len(usage_lines(settings.usage_log_path))
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "llm_mode": "real",
        "kind": "smoke (not an evaluation; AC-004/017/025 use the frozen set in TASK-011)",
        "text_model": settings.text_model, "embedding_model": settings.embedding_model,
        "retrieval_mode": settings.retrieval_mode, "questions": []}
    with TestClient(create_app(settings)) as client:
        for item in QUESTIONS:
            response = client.post("/api/policy/ask", json={"question": item["question"]}, headers=ZHANG)
            body = response.json()
            row: dict[str, Any] = {**item, "status_code": response.status_code}
            if response.status_code == 200:
                cited = sorted({c["clause"] for c in body["citations"]})
                row.update({
                    "answerable": body["answerable"], "answer": body["answer"],
                    "generator": body["generator"],
                    "citations": body["citations"], "retrieval": body["retrieval"],
                    "check": {"expectation_met": (body["answerable"] == (item["expect"] == "answerable")
                                                  and set(item["clauses"]) <= set(cited)),
                              "cited_clauses": cited},
                })
            else:
                row["error"] = body.get("error")
            report["questions"].append(row)
    calls = usage_lines(settings.usage_log_path)[before:]
    report["usage"] = {"calls": [{k: c[k] for k in ("purpose", "model", "total_tokens", "est_cost_cny",
                                                    "latency_ms")} for c in calls],
                       "total_tokens": sum(c["total_tokens"] for c in calls),
                       "est_cost_cny": round(sum(c["est_cost_cny"] for c in calls), 6)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: policy_qa_smoke.py <out.json>")
    sys.exit(main(Path(sys.argv[1])))
