"""Gated rule-extraction evaluation harness (AC-018).

Runs the delivered ``RealRuleExtractor`` over the frozen v1.2 policy text,
compiles candidates with ``compile_candidates`` (all judgement is code-side), and
scores them against the frozen gold rule set (``evals/gold_rules_v1.2.json``) and
the frozen injection samples (``evals/injections.json``). Metric definitions are
frozen in ``evals/METRICS.md`` §3; this script only applies them.

- recall                     = matched gold / 8                 (threshold >= 80%, i.e. >= 7)
- precision                  = candidates matching any gold / all candidates  (reported, no threshold)
- invalid-quote interception = injected invalid_quote marked / injected        (must be 100%)
- unsupported marking        = injected unsupported marked / injected          (must be 100%)
- model-spontaneous invalid_quote count reported separately (not gated).

The injection checks are deterministic (``compile_candidates`` + ``document.locate``)
and do NOT depend on what the model spontaneously produces (D-17). The recall /
precision part uses the real model and is metered under AUTH-01 into
``artifacts/llm-usage.jsonl``; only the NAME ``DASHSCOPE_API_KEY`` is checked, never
its value (F-03/HC-07). The frozen gold/injection SHA-256 and the v1.2 document hash
are embedded in the report so every run is tied to the frozen bytes.

Usage::

    uv run --offline python scripts/eval_rules.py <out.json> [--mode real]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from travel_desk.config import Settings, get_settings
from travel_desk.domain.policy import CLOSED_RULE_TYPES
from travel_desk.policy import documents
from travel_desk.policy.extraction import _raw_candidate, build_rule_extractor, compile_candidates

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD = REPO_ROOT / "evals" / "gold_rules_v1.2.json"
INJECTIONS = REPO_ROOT / "evals" / "injections.json"
CORPUS_NAME = "v1.2.md"

MONEY_TYPES = {"hotel_cap", "meal_allowance", "auto_approve_limit"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fen_mult(unit: str | None) -> int:
    """1 if the unit already denotes fen, 100 if it denotes yuan (money -> fen)."""
    u = unit or ""
    low = u.lower()
    if "fen" in low or "分" in u:
        return 1
    if "yuan" in low or "元" in u:
        return 100
    return 1  # default: the extraction prompt instructs integer fen


def _as_int(x: Any) -> int | None:
    try:
        return int(round(float(x)))
    except (TypeError, ValueError):
        return None


def _canon_value(type_: str, value: Any, unit: str | None) -> Any:
    """Canonical, comparable form of a rule value (METRICS.md §3 normalisation)."""
    value = value if isinstance(value, dict) else {}
    mult = _fen_mult(unit) if type_ in MONEY_TYPES else 1
    if type_ == "city_tier":
        tiers = value.get("tiers") if isinstance(value.get("tiers"), dict) else {}
        fallback = value.get("fallback_tier")
        if fallback is None and tiers.get("C"):
            fallback = "C"  # an explicit C-tier list expresses "other" == fallback C
        return {"A": frozenset(tiers.get("A") or []), "B": frozenset(tiers.get("B") or []),
                "fallback": fallback}
    if type_ in ("hotel_cap", "meal_allowance"):
        caps = value.get("caps_fen")
        if not isinstance(caps, dict):
            caps = value.get("caps") if isinstance(value.get("caps"), dict) else {}
        norm = {}
        for k, v in caps.items():
            iv = _as_int(v)
            norm[k] = iv * mult if iv is not None else None
        return {"caps": norm}
    if type_ == "auto_approve_limit":
        v = value.get("value_fen", value.get("value"))
        iv = _as_int(v)
        return {"value_fen": iv * mult if iv is not None else None}
    if type_ in ("advance_booking_days", "trip_duration_limit"):
        return {"days": _as_int(value.get("days"))}
    if type_ == "cabin_class":
        return {"allowed": frozenset(value.get("allowed") or [])}
    return {"raw": json.dumps(value, sort_keys=True, ensure_ascii=False)}


def _unit_ok(type_: str, cand_unit: str | None, gold_unit: str) -> bool:
    cu = (cand_unit or "").strip().lower()
    gu = (gold_unit or "").strip().lower()
    if type_ in MONEY_TYPES:
        # exact fen unit, or a yuan-denominated money unit (its value is x100 normalised)
        return cu == gu or _fen_mult(cand_unit) == 100
    return cu == gu


def _matches(cand: dict[str, Any], gold: dict[str, Any]) -> bool:
    """A candidate matches a gold rule when type/condition/value/unit/clause all agree."""
    if cand.get("type") != gold["type"]:
        return False
    if (cand.get("clause") or "").strip() != gold["clause"]:
        return False
    cond = cand.get("condition") if isinstance(cand.get("condition"), dict) else {}
    if cond != gold["condition"]:
        return False
    if not _unit_ok(gold["type"], cand.get("unit"), gold["unit"]):
        return False
    return _canon_value(gold["type"], cand.get("value"), cand.get("unit")) == \
        _canon_value(gold["type"], gold["value"], gold["unit"])


def _score(compiled: list[dict[str, Any]], gold_rules: list[dict[str, Any]]) -> dict[str, Any]:
    per_gold = []
    matched_ids: set[str] = set()
    for g in gold_rules:
        matches = [c for c in compiled if _matches(c, g)]
        for c in matches:
            matched_ids.add(c["id"])
        per_gold.append({
            "gold_id": g["gold_id"], "type": g["type"], "clause": g["clause"],
            "matched": bool(matches), "matched_candidate_ids": [c["id"] for c in matches],
            "matched_quote_status": sorted({c["quote_status"] for c in matches}),
        })
    recall_hit = sum(1 for x in per_gold if x["matched"])
    spontaneous_invalid = sum(1 for c in compiled if c["quote_status"] == "invalid_quote")
    unsupported_types = [c for c in compiled if c["type"] not in CLOSED_RULE_TYPES]
    unsupported_mislabelled = [c["id"] for c in unsupported_types if c["status"] != "unsupported"]
    return {
        "per_gold": per_gold,
        "recall": {"hit": recall_hit, "total": len(gold_rules),
                   "value": round(recall_hit / len(gold_rules), 4) if gold_rules else None,
                   "threshold": 0.80},
        "precision": {"hit": len(matched_ids), "total": len(compiled),
                      "value": round(len(matched_ids) / len(compiled), 4) if compiled else None,
                      "threshold_rule": "reported, no gate"},
        "model_spontaneous_invalid_quote": spontaneous_invalid,
        "real_non_closed_type_candidates": len(unsupported_types),
        "real_non_closed_type_mislabelled": unsupported_mislabelled,
    }


def _injections(document: Any, injections: dict[str, Any]) -> dict[str, Any]:
    invalid = injections["invalid_quote"]
    unsupported = injections["unsupported"]
    iq = compile_candidates(document, [_raw_candidate(x["candidate"]) for x in invalid])
    iq_rows = [{"id": invalid[i]["id"], "expect_quote_status": invalid[i]["expect_quote_status"],
                "quote_status": c["quote_status"], "status": c["status"],
                "intercepted": c["quote_status"] == "invalid_quote"}
               for i, c in enumerate(iq)]
    us = compile_candidates(document, [_raw_candidate(x["candidate"]) for x in unsupported])
    us_rows = [{"id": unsupported[i]["id"], "expect_status": unsupported[i]["expect_status"],
                "expect_quote_status": unsupported[i].get("expect_quote_status"),
                "status": c["status"], "quote_status": c["quote_status"],
                "marked": c["status"] == "unsupported"}
               for i, c in enumerate(us)]
    iq_hit = sum(1 for r in iq_rows if r["intercepted"])
    us_hit = sum(1 for r in us_rows if r["marked"])
    return {
        "invalid_quote": {"rows": iq_rows, "hit": iq_hit, "total": len(iq_rows),
                          "value": round(iq_hit / len(iq_rows), 4) if iq_rows else None,
                          "threshold": 1.0},
        "unsupported": {"rows": us_rows, "hit": us_hit, "total": len(us_rows),
                        "value": round(us_hit / len(us_rows), 4) if us_rows else None,
                        "threshold": 1.0},
    }


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
    parser.add_argument("--mode", choices=("fake", "recorded", "real"), default="real")
    args = parser.parse_args()

    if args.mode == "real" and not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        sys.exit("DASHSCOPE_API_KEY is not set (only the name is checked, never the value)")

    settings = replace(get_settings(), llm_mode=args.mode)
    document = documents.load(settings.policies_dir / CORPUS_NAME, "markdown")
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    injections = json.loads(INJECTIONS.read_text(encoding="utf-8"))
    before = len(settings.usage_log_path.read_text(encoding="utf-8").splitlines()) if settings.usage_log_path.is_file() else 0

    extractor = build_rule_extractor(settings)
    extraction = asyncio.run(extractor.extract(document))
    compiled = compile_candidates(document, extraction.candidates)

    scored = _score(compiled, gold["rules"])
    inj = _injections(document, injections)

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "llm_mode": args.mode, "extractor": extractor.name, "generator": extraction.generator,
        "text_model": settings.text_model,
        "corpus": str(settings.policies_dir / CORPUS_NAME),
        "document_normalized_sha256": document.normalized_sha256,
        "gold_sha256": sha256_file(GOLD), "injections_sha256": sha256_file(INJECTIONS),
        "metric_spec": "evals/METRICS.md#3",
        "candidate_count": len(compiled),
        "candidates": [{k: c.get(k) for k in ("id", "type", "condition", "value", "unit", "clause",
                                              "quote_status", "status", "locator")} for c in compiled],
        "recall": scored["recall"], "precision": scored["precision"], "per_gold": scored["per_gold"],
        "model_spontaneous_invalid_quote": scored["model_spontaneous_invalid_quote"],
        "real_non_closed_type_candidates": scored["real_non_closed_type_candidates"],
        "real_non_closed_type_mislabelled": scored["real_non_closed_type_mislabelled"],
        "invalid_quote_interception": inj["invalid_quote"],
        "unsupported_marking": inj["unsupported"],
    }
    if args.mode == "real":
        report["usage"] = _usage_since(settings, before)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    r, p = report["recall"], report["precision"]
    iq, us = report["invalid_quote_interception"], report["unsupported_marking"]
    print(f"mode={args.mode} extractor={extraction.generator} candidates={len(compiled)}")
    print(f"  recall={r['hit']}/{r['total']}={r['value']} (>=0.80)")
    print(f"  precision={p['hit']}/{p['total']}={p['value']} (reported)")
    print(f"  invalid_quote_interception={iq['hit']}/{iq['total']}={iq['value']} (==1.0)")
    print(f"  unsupported_marking={us['hit']}/{us['total']}={us['value']} (==1.0)")
    print(f"  model_spontaneous_invalid_quote={report['model_spontaneous_invalid_quote']}")
    print(f"  real_non_closed_type_mislabelled={report['real_non_closed_type_mislabelled']}")
    if "usage" in report:
        print(f"  usage: {report['usage']['n']} calls, {report['usage']['total_tokens']} tokens, "
              f"{report['usage']['est_cost_cny']} CNY")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
