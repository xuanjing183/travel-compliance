"""Draft paraphrase-set evaluation for the enhanced policy-agent chain.

The frozen `evals/qa_set.json` (25 questions) stays untouched (D-17); this script
reads `evals/paraphrase_set.json` (draft, 24 topics × 2 colloquial variants) and
measures the enhanced retrieval path:

- baseline: original question only, delivered retriever config;
- enhanced: original + optimizer rewrite, multi-query RRF, agent retrieval mode.

Real mode only: rewrite/embeddings need DashScope. Every call is metered under
AUTH-01; the key name is checked, never printed. Use `--limit` for a small smoke run.

Usage::

    uv run --offline python scripts/eval_paraphrase.py <out.json> --limit 12
    uv run --offline python scripts/eval_paraphrase.py <out.json> --no-rewrite
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from travel_desk.config import Settings, get_settings
from travel_desk.db.database import read_session
from travel_desk.llm.dashscope import UsageMeter
from travel_desk.main import create_app
from travel_desk.policy.enhanced_retriever import EnhancedRetriever, query_paths
from travel_desk.policy.query_optimizer import QueryOptimizer, QueryPlan
from travel_desk.policy.rag import (DashScopeEmbedder, Hit, Retrieval, Retriever, clause_links,
                                    with_supplements)
from travel_desk.policy.reranker import CrossEncoderReranker
from travel_desk.services.policies import _document_of, _rules

REPO_ROOT = Path(__file__).resolve().parents[1]
SET_PATH = REPO_ROOT / "evals" / "paraphrase_set.json"


def sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_settings() -> Settings:
    work = Path(tempfile.mkdtemp(prefix="paraphrase-eval-")) / "var"
    return replace(get_settings(), llm_mode="real", data_dir=work,
                   db_path=work / "app.sqlite", checkpoint_db_path=work / "checkpoints.sqlite",
                   upload_dir=work / "uploads")


def _published(settings: Settings):
    with read_session(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM policy_versions WHERE status = 'published'").fetchone()
        if row is None:
            raise SystemExit("no published policy version (seed failed)")
        return (row["version"], row["document_hash"], _document_of(settings, row),
                clause_links(_rules(conn, row["version"])))


def _load_cache(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cache(path: Path | None, cache: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


async def _rows(settings: Settings, queries: list[dict[str, Any]], *, rewrite: bool,
                rerank: bool, synonyms: bool = True,
                cache_path: Path | None = None) -> list[dict[str, Any]]:
    version, document_hash, document, links = _published(settings)
    meter = UsageMeter.from_settings(settings)
    base = Retriever(settings.agent_retrieval_mode, DashScopeEmbedder(settings, meter))
    enhanced = EnhancedRetriever(base, use_synonyms=synonyms)
    index = enhanced.index(version, document_hash, document)
    optimizer = QueryOptimizer(settings, meter)
    reranker = CrossEncoderReranker(settings, meter) if rerank else None
    cache = _load_cache(cache_path)

    rows = []
    for q in queries:
        paths = [q["question"]]
        plan = None
        if rewrite:
            cached = cache.get(q["question"])
            if cached:
                plan = QueryPlan(split=cached["split"], queries=cached["queries"])
            else:
                plan = await optimizer.optimize(q["question"])
                cache[q["question"]] = {"split": plan.split, "queries": plan.queries}
                _save_cache(cache_path, cache)
            paths = query_paths(q["question"], plan)
        retrieval = await enhanced.search_multi(index, paths, k_per_query=10, k_final=10)
        reranked = []
        if reranker is not None:
            # Rerank with the original question, not the rewrite: the cross-encoder
            # judges the user's actual wording.
            reranked = await reranker.rerank(q["question"], [hit.chunk for hit in retrieval.hits], top_k=10)
            final_retrieval = Retrieval(
                f"{retrieval.mode}+rerank",
                [Hit(item.chunk, rank, round(item.score, 6)) for rank, item in enumerate(reranked, start=1)],
                retrieval.query_terms)
        else:
            final_retrieval = retrieval
        shown = with_supplements(final_retrieval, index.chunks, links)
        merged = [hit.chunk.clause for hit in retrieval.hits]
        final_clauses = [hit.chunk.clause for hit in final_retrieval.hits]
        rows.append({
            "id": q["id"], "topic": q["topic"], "category": q["category"], "question": q["question"],
            "answerable": q["answerable"], "refusal": q["refusal"],
            "primary_clause": q["primary_clause"], "also_expected": q["also_expected"],
            "numeric": q["numeric"], "paths": paths,
            "rewrite_split": plan.split if plan else None,
            "ranked_clauses": merged, "final_clauses": final_clauses,
            "top3": final_clauses[:3], "top5": final_clauses[:5],
            "rerank_scores": [{"clause": item.chunk.clause, "locator": item.chunk.locator,
                               "score": round(item.score, 6)} for item in reranked],
            "supplement_clauses": [c.clause for c in shown.supplements],
        })
    return rows


def _metric(hit: int, total: int) -> dict[str, Any]:
    return {"hit": hit, "total": total, "value": round(hit / total, 4) if total else None}


def _score(queries: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {r["id"]: r for r in rows}
    answerable = [q for q in queries if q["answerable"]]
    categories: dict[str, dict[str, int]] = {}
    r3 = r5 = r10 = also5 = also10 = 0
    detail = []
    for q in queries:
        r = by_id.get(q["id"], {})
        item = {"id": q["id"], "topic": q["topic"], "category": q["category"]}
        bucket = categories.setdefault(q["category"], {"r3": 0, "r5": 0, "r10": 0, "n": 0,
                                                       "also": 0, "also10": 0, "also_n": 0})
        final = r.get("final_clauses") or []
        if q["primary_clause"]:
            item["recall3"] = q["primary_clause"] in (r.get("top3") or [])
            item["recall5"] = q["primary_clause"] in (r.get("top5") or [])
            item["recall10"] = q["primary_clause"] in final[:10]
            r3 += int(item["recall3"])
            r5 += int(item["recall5"])
            r10 += int(item["recall10"])
            bucket["r3"] += int(item["recall3"])
            bucket["r5"] += int(item["recall5"])
            bucket["r10"] += int(item["recall10"])
            bucket["n"] += 1
        if q["also_expected"]:
            top5 = set(r.get("top5") or []) | set(r.get("supplement_clauses") or [])
            top10 = set(final[:10]) | set(r.get("supplement_clauses") or [])
            item["also_expected_hit"] = set(q["also_expected"]) <= top5
            item["also_expected_hit10"] = set(q["also_expected"]) <= top10
            also5 += int(item["also_expected_hit"])
            also10 += int(item["also_expected_hit10"])
            bucket["also"] += int(item["also_expected_hit"])
            bucket["also10"] += int(item["also_expected_hit10"])
            bucket["also_n"] += 1
        detail.append(item)
    return {
        "metrics": {
            "recall@3": _metric(r3, len(answerable)),
            "recall@5": _metric(r5, len(answerable)),
            "recall@10": _metric(r10, len(answerable)),
            "also_expected@5_including_supplements": _metric(
                also5, sum(1 for q in queries if q["also_expected"])),
            "also_expected@10_including_supplements": _metric(
                also10, sum(1 for q in queries if q["also_expected"])),
            "empty_after_rerank": sum(1 for r in rows if not r.get("final_clauses")),
            "by_category": {
                name: {"recall@3": _metric(b["r3"], b["n"]), "recall@5": _metric(b["r5"], b["n"]),
                       "recall@10": _metric(b["r10"], b["n"]),
                       "also_expected@5": _metric(b["also"], b["also_n"]),
                       "also_expected@10": _metric(b["also10"], b["also_n"])}
                for name, b in categories.items()
            },
        },
        "detail": detail,
    }


def _usage_since(settings: Settings, before: int) -> dict[str, Any]:
    path = settings.usage_log_path
    lines = ([json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
             if path.is_file() else [])
    calls = lines[before:]
    return {"calls": [{k: c.get(k) for k in ("purpose", "model", "total_tokens", "est_cost_cny", "latency_ms")}
                      for c in calls],
            "n": len(calls), "total_tokens": sum(c.get("total_tokens") or 0 for c in calls),
            "est_cost_cny": round(sum(c.get("est_cost_cny") or 0 for c in calls), 6)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--limit", type=int, default=0, help="evaluate the first N queries (smoke run)")
    parser.add_argument("--ids", default="", help="comma-separated query ids (overrides --limit)")
    parser.add_argument("--no-rewrite", action="store_true", help="baseline: original question only")
    parser.add_argument("--rerank", action="store_true", help="rerank merged candidates with gte-rerank-v2")
    parser.add_argument("--no-synonyms", action="store_true",
                        help="ablation: disable the synonym-expanded BM25 path")
    parser.add_argument("--cache", type=Path, default=REPO_ROOT / "artifacts" / "paraphrase"
                        / "rewrite-cache.json", help="reuse/save rewrite results (no model call on a hit)")
    args = parser.parse_args()

    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        sys.exit("DASHSCOPE_API_KEY is not set (only the name is checked, never the value)")

    dataset = json.loads(SET_PATH.read_text(encoding="utf-8"))
    queries = dataset["queries"][:args.limit or None]
    if args.ids:
        wanted = {name.strip() for name in args.ids.split(",") if name.strip()}
        queries = [q for q in dataset["queries"] if q["id"] in wanted]
        if {q["id"] for q in queries} != wanted:
            sys.exit(f"unknown ids: {sorted(wanted - {q['id'] for q in queries})}")
    settings = build_settings()
    before = (len(settings.usage_log_path.read_text(encoding="utf-8").splitlines())
              if settings.usage_log_path.is_file() else 0)

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": "evals/paraphrase_set.json", "dataset_version": dataset["version"],
        "dataset_status": dataset["status"], "dataset_sha256": sha256_file(SET_PATH),
        "llm_mode": "real", "agent_retrieval_mode": settings.agent_retrieval_mode,
        "rewrite": not args.no_rewrite, "rewrite_model": settings.rewrite_model,
        "rerank": args.rerank, "rerank_model": settings.rerank_model,
        "rerank_min_score": settings.rerank_min_score,
        "synonyms": not args.no_synonyms,
        "embedding_model": settings.embedding_model, "query_count": len(queries),
    }
    with TestClient(create_app(settings)):
        rows = asyncio.run(_rows(settings, queries, rewrite=not args.no_rewrite, rerank=args.rerank,
                                 synonyms=not args.no_synonyms,
                                 cache_path=None if args.no_rewrite else args.cache))
    report["rows"] = rows
    report.update(_score(queries, rows))
    report["usage"] = _usage_since(settings, before)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    m = report["metrics"]
    print(f"rewrite={not args.no_rewrite} synonyms={not args.no_synonyms} rerank={args.rerank} "
          f"queries={len(queries)} "
          f"Recall@3={m['recall@3']['hit']}/{m['recall@3']['total']}={m['recall@3']['value']} "
          f"Recall@5={m['recall@5']['hit']}/{m['recall@5']['total']}={m['recall@5']['value']} "
          f"Recall@10={m['recall@10']['hit']}/{m['recall@10']['total']}={m['recall@10']['value']} "
          f"also@5={m['also_expected@5_including_supplements']['hit']}/"
          f"{m['also_expected@5_including_supplements']['total']} "
          f"also@10={m['also_expected@10_including_supplements']['hit']}/"
          f"{m['also_expected@10_including_supplements']['total']} "
          f"empty={m['empty_after_rerank']}")
    print(f"usage: {report['usage']['n']} calls, {report['usage']['total_tokens']} tokens, "
          f"{report['usage']['est_cost_cny']} CNY")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
