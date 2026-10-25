"""Real-mode paraphrase smoke for `/api/policy/ask-agent` (Phase 3).

Runs a small, readable set of the draft paraphrase questions (plus chat probes) through
the enhanced agent endpoint and reports contract-level facts: did it call the tool,
is the answer grounded, is the primary clause cited, is the expected number in the
answer, did a no-basis question refuse, and did a chat question stay out of retrieval.

This is a smoke test, not the frozen AC-004/017 evaluation: those keep running against
the delivered `/api/policy/ask` path with `scripts/eval_qa.py`.

Usage::

    uv run --offline python scripts/eval_agent_paraphrase.py <out.json>
    uv run --offline python scripts/eval_agent_paraphrase.py <out.json> --ids P01-a,P21-a
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from travel_desk.config import Settings, get_settings
from travel_desk.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
SET_PATH = REPO_ROOT / "evals" / "paraphrase_set.json"
DEFAULT_IDS = "P01-a,P02-b,P05-a,P07-a,P09-b,P11-a,P14-a,P15-a,P19-a,P21-a,P23-a"
CHAT_PROBES = (
    "你好，你是谁？",
    "今天北京天气怎么样？",
)
ZHANG = {"X-Demo-User": "u-zhangsan"}


def build_settings() -> Settings:
    work = Path(tempfile.mkdtemp(prefix="agent-eval-")) / "var"
    return replace(get_settings(), llm_mode="real", data_dir=work,
                   db_path=work / "app.sqlite", checkpoint_db_path=work / "checkpoints.sqlite",
                   upload_dir=work / "uploads")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def usages_since(settings: Settings, before: int) -> list[dict[str, Any]]:
    path = settings.usage_log_path
    lines = ([json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
             if path.is_file() else [])
    return lines[before:]


def number_ok(numeric: dict[str, Any] | None, answer: str) -> bool | None:
    if not numeric:
        return None
    if numeric["kind"] == "fen":
        expected = f"{numeric['value_fen'] / 100:g}"
    else:
        expected = str(numeric["value"])
    return re.search(rf"(?<!\d){re.escape(expected)}(?!\d)", answer) is not None


def judge(question: dict[str, Any], response) -> dict[str, Any]:
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    citations = body.get("citations") or []
    agent = body.get("agent") or {}
    answer = body.get("answer") or ""
    row: dict[str, Any] = {
        "id": question["id"], "category": question["category"], "question": question["question"],
        "status": response.status_code, "answer": answer,
        "grounded": body.get("grounded"), "answerable": body.get("answerable"),
        "confidence": body.get("confidence"), "needs_clarification": body.get("needs_clarification"),
        "citations": [{"clause": c.get("clause"), "locator": c.get("locator")} for c in citations],
        "agent": agent, "retrieval_mode": (body.get("retrieval") or {}).get("mode"),
        "numeric": question.get("numeric"),
    }
    if response.status_code != 200:
        row["error"] = body.get("error") or {"message": response.text[:200]}
        row.update({"tool_called": None, "primary_cited": None, "numeric_ok": None, "refused": None})
        return row
    clauses = [c.get("clause") for c in citations]
    tool_called = (agent.get("tool_calls") or 0) > 0
    expected = question.get("expected_clauses") or (
        [question["primary_clause"]] if question.get("primary_clause") else [])
    row["tool_called"] = tool_called
    row["primary_cited"] = all(clause in clauses for clause in expected) if expected else None
    row["numeric_ok"] = number_ok(question.get("numeric"), answer)
    row["refused"] = (not body.get("answerable")) and answer.startswith("不知道") if not question["answerable"] else None
    return row


def judge_chat(question: str, response) -> dict[str, Any]:
    body = response.json()
    agent = body.get("agent") or {}
    citations = body.get("citations") or []
    answer = body.get("answer") or ""
    ok = (response.status_code == 200 and body.get("grounded") is False and not citations
          and (agent.get("tool_calls") or 0) == 0 and bool(answer.strip()))
    return {"question": question, "status": response.status_code, "ok": ok, "grounded": body.get("grounded"),
            "tool_calls": agent.get("tool_calls"), "citations": citations, "answer": answer}


def rate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [row[key] for row in rows if row.get(key) is not None]
    return {"hit": sum(1 for value in values if value), "total": len(values),
            "value": round(sum(1 for value in values if value) / len(values), 4) if values else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--set", type=Path, default=SET_PATH,
                        help="question set JSON (default: evals/paraphrase_set.json)")
    parser.add_argument("--ids", default="", help="comma-separated ids (default: the built-in smoke ids)")
    parser.add_argument("--no-chat", action="store_true", help="skip the chat probes")
    args = parser.parse_args()

    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        sys.exit("DASHSCOPE_API_KEY is not set (only the name is checked, never the value)")

    dataset = json.loads(args.set.read_text(encoding="utf-8"))
    if args.ids:
        wanted = {name.strip() for name in args.ids.split(",") if name.strip()}
    elif args.set == SET_PATH:
        wanted = {name.strip() for name in DEFAULT_IDS.split(",") if name.strip()}
    else:
        wanted = {q["id"] for q in dataset["queries"]}
    questions = [q for q in dataset["queries"] if q["id"] in wanted]
    if {q["id"] for q in questions} != wanted:
        sys.exit(f"unknown ids: {sorted(wanted - {q['id'] for q in questions})}")

    settings = build_settings()
    before = (len(settings.usage_log_path.read_text(encoding="utf-8").splitlines())
              if settings.usage_log_path.is_file() else 0)
    rows: list[dict[str, Any]] = []
    chats: list[dict[str, Any]] = []
    started = time.perf_counter()
    with TestClient(create_app(settings)) as client:
        for question in questions:
            response = client.post("/api/policy/ask-agent", json={"question": question["question"]},
                                   headers=ZHANG)
            rows.append(judge(question, response))
            print(f"{question['id']}: {response.status_code} "
                  f"cited={rows[-1]['primary_cited']} numeric={rows[-1]['numeric_ok']} "
                  f"refused={rows[-1]['refused']}", flush=True)
        if not args.no_chat:
            for probe in CHAT_PROBES:
                response = client.post("/api/policy/ask-agent", json={"question": probe}, headers=ZHANG)
                chats.append(judge_chat(probe, response))
                print(f"chat: {chats[-1]['ok']} tool_calls={chats[-1]['tool_calls']}", flush=True)

    usage = usages_since(settings, before)
    grounded_answers = [row["confidence"] for row in rows
                        if row.get("answerable") and row.get("confidence") is not None]
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "endpoint": "/api/policy/ask-agent", "llm_mode": "real",
        "dataset": str(args.set.resolve().relative_to(REPO_ROOT)).replace("\\", "/"),
        "dataset_version": dataset["version"], "dataset_status": dataset["status"],
        "dataset_sha256": sha256_file(args.set),
        "model": settings.text_model, "retrieval_mode": settings.agent_retrieval_mode,
        "elapsed_s": round(time.perf_counter() - started, 2),
        "metrics": {
            "grounded": rate(rows, "grounded"),
            "tool_called": rate(rows, "tool_called"),
            "primary_cited": rate(rows, "primary_cited"),
            "numeric_ok": rate(rows, "numeric_ok"),
            "refused": rate(rows, "refused"),
            "confidence_avg": round(sum(grounded_answers) / len(grounded_answers), 3)
            if grounded_answers else None,
            "clarifications": sum(1 for row in rows if row.get("needs_clarification")),
            "chat_ok": {"hit": sum(1 for c in chats if c["ok"]), "total": len(chats),
                        "value": round(sum(1 for c in chats if c["ok"]) / len(chats), 4) if chats else None},
        },
        "rows": rows, "chat": chats,
        "usage": {"calls": len(usage),
                  "total_tokens": sum(entry.get("total_tokens") or 0 for entry in usage),
                  "est_cost_cny": round(sum(entry.get("est_cost_cny") or 0 for entry in usage), 6),
                  "by_purpose": {purpose: sum(1 for entry in usage if entry["purpose"] == purpose)
                                 for purpose in sorted({entry["purpose"] for entry in usage})}},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False))
    print(f"usage: {report['usage']['calls']} calls, {report['usage']['total_tokens']} tokens, "
          f"{report['usage']['est_cost_cny']} CNY, {report['elapsed_s']}s")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
