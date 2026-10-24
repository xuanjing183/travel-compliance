"""Gated policy-Q&A evaluation harness (AC-025 retrieval comparison; AC-004/AC-017 metrics).

Loads the frozen `evals/qa_set.json` (D-17) and runs it against the delivered
policy-Q&A path. Two stages:

- ``retrieval`` : Recall@3 / Recall@5 only, via the delivered ``Retriever`` (no
  text-model answer). AC-025 compares ``TRAVEL_DESK_RETRIEVAL=vector`` vs
  ``hybrid`` and keeps hybrid only if Recall@3 is *strictly* better (REQ-029, D-17).
- ``full``      : retrieval + real structured answer via ``POST /api/policy/ask``.
  Computes citation hit rate (>=90%), numeric correctness (>=85%) and refusal
  accuracy (>=4/5). AC-004/AC-017, run with the config AC-025 retained.

Real mode (``llm_mode=real``) is metered under AUTH-01 into
``artifacts/llm-usage.jsonl``; only the NAME ``DASHSCOPE_API_KEY`` is checked,
never its value (F-03/HC-07). The frozen dataset's SHA-256 is embedded in the
report so every run is tied to the frozen bytes. Metric definitions are frozen in
``evals/METRICS.md``; this script only applies them.

Usage::

    uv run --offline python scripts/eval_qa.py <out.json> --stage retrieval --retrieval vector
    uv run --offline python scripts/eval_qa.py <out.json> --stage retrieval --retrieval hybrid
    uv run --offline python scripts/eval_qa.py <out.json> --stage full --retrieval vector [--mode real]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from travel_desk.config import Settings, get_settings
from travel_desk.db.database import read_session
from travel_desk.main import create_app
from travel_desk.policy.rag import build_policy_qa, clause_links, with_supplements
from travel_desk.services.policies import _document_of, _rules

REPO_ROOT = Path(__file__).resolve().parents[1]
QA_SET = REPO_ROOT / "evals" / "qa_set.json"
ZHANG = {"X-Demo-User": "u-zhangsan"}

# Numeric normalisation (METRICS.md §2.2): monetary -> fen, durations -> integer count.
_NUM = re.compile(r"(\d+(?:\.\d+)?|[零一二两三四五六七八九十]+)\s*(个自然日|自然日|元|块|分|个|天|日|晚)?")
_CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_FEN_UNITS = {"元", "块"}
_COUNT_UNITS = {"天", "日", "晚", "个", "个自然日", "自然日"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _to_number(token: str) -> float | None:
    if token.isdigit() or re.fullmatch(r"\d+\.\d+", token):
        return float(token)
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN.get(head, 1) if head else 1
        ones = _CN.get(tail, 0) if tail else 0
        return float(tens * 10 + ones)
    if token and all(ch in _CN for ch in token):
        return float(int("".join(str(_CN[ch]) for ch in token)))
    return None


def extract_values(answer: str) -> tuple[set[int], set[int]]:
    """(fen candidates, count candidates) parsed from a free-text answer.

    A bare number is read both as yuan (x100 -> fen) and as a possible count, since
    the policy states money in yuan and durations in days; the gold value is specific
    enough that this does not create cross-question false positives. The raw answer is
    always kept in the report for manual audit.
    """
    fen: set[int] = set()
    count: set[int] = set()
    for match in _NUM.finditer(answer or ""):
        num = _to_number(match.group(1))
        if num is None:
            continue
        unit = match.group(2) or ""
        if unit in _FEN_UNITS:
            fen.add(round(num * 100))
        elif unit == "分":
            fen.add(round(num))
        elif unit in _COUNT_UNITS:
            count.add(int(num))
        else:
            fen.add(round(num * 100))
            count.add(int(num))
    return fen, count


def build_settings(mode: str, retrieval: str) -> Settings:
    work = Path(tempfile.mkdtemp(prefix="qa-eval-")) / "var"
    base = get_settings()
    return replace(base, llm_mode=mode, retrieval_mode=retrieval, data_dir=work,
                   db_path=work / "app.sqlite", checkpoint_db_path=work / "checkpoints.sqlite",
                   upload_dir=work / "uploads")


def load_questions() -> list[dict[str, Any]]:
    return json.loads(QA_SET.read_text(encoding="utf-8"))["questions"]


def _published(settings: Settings) -> tuple[str, str, Any, dict[str, list[str]]]:
    with read_session(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM policy_versions WHERE status = 'published'").fetchone()
        if row is None:
            raise SystemExit("no published policy version (seed failed)")
        return row["version"], row["document_hash"], _document_of(settings, row), clause_links(_rules(conn, row["version"]))


async def _retrieval_rows(settings: Settings, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    version, document_hash, document, links = _published(settings)
    engine = build_policy_qa(settings)
    index = engine.retriever.index(version, document_hash, document)
    rows = []
    for q in questions:
        retrieval = with_supplements(await engine.retriever.search(index, q["question"]), index.chunks, links)
        ranked = [h.chunk.clause for h in retrieval.hits]
        rows.append({"id": q["id"], "question": q["question"], "primary_clause": q["primary_clause"],
                     "also_expected": q["also_expected"], "retrieval_mode": retrieval.mode,
                     "ranked_clauses": ranked,
                     "top3": ranked[:3], "top5": ranked[:5],
                     "supplement_clauses": [c.clause for c in retrieval.supplements]})
    return rows


def _full_rows(client: TestClient, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for q in questions:
        response = client.post("/api/policy/ask", json={"question": q["question"]}, headers=ZHANG)
        row: dict[str, Any] = {"id": q["id"], "question": q["question"], "primary_clause": q["primary_clause"],
                               "also_expected": q["also_expected"], "status_code": response.status_code}
        if response.status_code != 200:
            row["error"] = response.json().get("error")
            rows.append(row)
            continue
        body = response.json()
        cited = [c["clause"] for c in body["citations"]]
        ranked = [c["clause"] for c in sorted(body["retrieval"]["chunks"], key=lambda c: c["rank"])]
        fen, count = extract_values(body["answer"])
        row.update({
            "answerable": body["answerable"], "answer": body["answer"], "generator": body["generator"],
            "retrieval_mode": body["retrieval"]["mode"], "cited_clauses": cited, "ranked_clauses": ranked,
            "top3": ranked[:3], "top5": ranked[:5],
            "parsed_fen": sorted(fen), "parsed_count": sorted(count),
        })
        rows.append(row)
    return rows


def _score(questions: list[dict[str, Any]], rows: list[dict[str, Any]], stage: str) -> dict[str, Any]:
    by_id = {r["id"]: r for r in rows}
    answerable = [q for q in questions if q["answerable"]]
    refusals = [q for q in questions if q["refusal"]]
    numeric = [q for q in questions if q.get("numeric")]
    detail = []

    def recall_hit(q: dict[str, Any], r: dict[str, Any], k: int) -> bool | None:
        if not q["primary_clause"]:
            return None
        top = r.get(f"top{k}") or []
        return q["primary_clause"] in top

    citation_hits = numeric_hits = refusal_ok = r3 = r5 = 0
    r3_n = r5_n = 0
    for q in questions:
        r = by_id.get(q["id"], {})
        item: dict[str, Any] = {"id": q["id"], "answerable_gold": q["answerable"], "refusal": q["refusal"]}
        h3, h5 = recall_hit(q, r, 3), recall_hit(q, r, 5)
        if h3 is not None:
            r3_n += 1
            r3 += int(h3)
            item["recall3"] = h3
        if h5 is not None:
            r5_n += 1
            r5 += int(h5)
            item["recall5"] = h5
        if stage == "full":
            cited = set(r.get("cited_clauses") or [])
            if q["answerable"]:
                hit = bool(q["primary_clause"]) and q["primary_clause"] in cited
                citation_hits += int(hit)
                item["citation_hit"] = hit
                item["cited_clauses"] = sorted(cited)
            if q.get("numeric"):
                gold = q["numeric"]
                ok = bool(r.get("answerable")) and (
                    (gold["kind"] == "fen" and gold["value_fen"] in set(r.get("parsed_fen") or []))
                    or (gold["kind"] == "count" and gold["value"] in set(r.get("parsed_count") or [])))
                numeric_hits += int(ok)
                item["numeric_gold"] = gold
                item["numeric_hit"] = ok
                item["parsed_fen"] = r.get("parsed_fen")
                item["parsed_count"] = r.get("parsed_count")
            if q["refusal"]:
                ok = (r.get("answerable") is False) and not (r.get("cited_clauses") or [])
                refusal_ok += int(ok)
                item["refusal_correct"] = ok
            item["answerable_model"] = r.get("answerable")
            item["answer"] = r.get("answer")
        detail.append(item)

    n_ans = len(answerable)
    metrics: dict[str, Any] = {
        "recall@3": {"hit": r3, "total": r3_n, "value": round(r3 / r3_n, 4) if r3_n else None},
        "recall@5": {"hit": r5, "total": r5_n, "value": round(r5 / r5_n, 4) if r5_n else None},
    }
    if stage == "full":
        metrics["citation_hit_rate"] = {"hit": citation_hits, "total": n_ans,
                                        "value": round(citation_hits / n_ans, 4) if n_ans else None,
                                        "threshold": 0.90}
        metrics["numeric_correct_rate"] = {"hit": numeric_hits, "total": len(numeric),
                                           "value": round(numeric_hits / len(numeric), 4) if numeric else None,
                                           "threshold": 0.85}
        metrics["refusal_correct"] = {"hit": refusal_ok, "total": len(refusals),
                                      "threshold_rule": ">=4 of 5"}
    return {"metrics": metrics, "detail": detail}


def _usage_since(settings: Settings, before: int) -> dict[str, Any]:
    path = settings.usage_log_path
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.is_file() else []
    calls = lines[before:]
    return {"calls": [{k: c.get(k) for k in ("purpose", "model", "total_tokens", "est_cost_cny", "latency_ms")}
                      for c in calls],
            "n": len(calls), "total_tokens": sum(c.get("total_tokens") or 0 for c in calls),
            "est_cost_cny": round(sum(c.get("est_cost_cny") or 0 for c in calls), 6)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--stage", choices=("retrieval", "full"), default="retrieval")
    parser.add_argument("--retrieval", choices=("vector", "hybrid"), default="vector")
    parser.add_argument("--mode", choices=("fake", "recorded", "real"), default="real")
    args = parser.parse_args()

    if args.mode == "real" and not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        sys.exit("DASHSCOPE_API_KEY is not set (only the name is checked, never the value)")
    if args.stage == "retrieval" and args.mode == "fake":
        print("note: fake mode forces bm25 retrieval; vector/hybrid comparison needs --mode real", file=sys.stderr)

    questions = load_questions()
    settings = build_settings(args.mode, args.retrieval)
    before = len(settings.usage_log_path.read_text(encoding="utf-8").splitlines()) if settings.usage_log_path.is_file() else 0

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "stage": args.stage,
        "llm_mode": args.mode, "retrieval_mode": args.retrieval,
        "text_model": settings.text_model, "embedding_model": settings.embedding_model,
        "qa_set_sha256": sha256_file(QA_SET), "question_count": len(questions),
        "metric_spec": "evals/METRICS.md",
    }
    with TestClient(create_app(settings)) as client:
        rows = (_full_rows(client, questions) if args.stage == "full"
                else asyncio.run(_retrieval_rows(settings, questions)))
    scored = _score(questions, rows, args.stage)
    report["rows"] = rows
    report.update(scored)
    if args.mode == "real":
        report["usage"] = _usage_since(settings, before)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    m = scored["metrics"]
    print(f"stage={args.stage} mode={args.mode} retrieval={args.retrieval} "
          f"Recall@3={m['recall@3']['hit']}/{m['recall@3']['total']}={m['recall@3']['value']} "
          f"Recall@5={m['recall@5']['hit']}/{m['recall@5']['total']}={m['recall@5']['value']}")
    if args.stage == "full":
        print(f"  citation_hit={m['citation_hit_rate']['hit']}/{m['citation_hit_rate']['total']}="
              f"{m['citation_hit_rate']['value']} (>=0.90)")
        print(f"  numeric_correct={m['numeric_correct_rate']['hit']}/{m['numeric_correct_rate']['total']}="
              f"{m['numeric_correct_rate']['value']} (>=0.85)")
        print(f"  refusal_correct={m['refusal_correct']['hit']}/{m['refusal_correct']['total']} (>=4/5)")
    if "usage" in report:
        print(f"  usage: {report['usage']['n']} calls, {report['usage']['total_tokens']} tokens, "
              f"{report['usage']['est_cost_cny']} CNY")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
