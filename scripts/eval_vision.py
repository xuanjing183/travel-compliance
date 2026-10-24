"""Gated real image-extraction evaluation harness (AC-029).

Runs the delivered ``RealInvitationExtractor`` (DashScope multimodal, local file as
a base64 data URL — no OSS upload, RISK-002) over the S-11 fixture and judges the
result against the frozen S-11 acceptance口径 (METRICS.md §4):

- city          : destination contains 「上海」
- dates         : start_date == 2026-10-28 AND end_date == 2026-10-30
- purpose       : contains 「供应商大会」
- meeting addr  : whitespace-stripped address contains BOTH 「滨江大道2727号」 AND 「星衡国际会议中心」

AC-029 passes only if all four agree. The extraction result is still only a
proposal — it touches the Trip after employee confirmation (that flow is verified
deterministically by AC-003); this harness judges the raw extraction only.

Evidence records the model name, a DESENSITISED request summary (no image bytes,
no API-key value — only the variable NAME is checked, F-03/HC-07) and the
extraction JSON. Real mode is metered under AUTH-01 into artifacts/llm-usage.jsonl.

Usage::

    uv run --offline python scripts/eval_vision.py <out.json> [--mode real]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from travel_desk.config import Settings, get_settings
from travel_desk.llm.vision import INVITATION_FIELDS, build_invitation_extractor

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "invitations" / "shanghai_supplier_conference.png"

# Frozen S-11 expectation (METRICS.md §4). Judgement is code-side, not model-side.
EXPECT_CITY = "上海"
EXPECT_START = "2026-10-28"
EXPECT_END = "2026-10-30"
EXPECT_PURPOSE_SUBSTRING = "供应商大会"
EXPECT_ADDRESS_SUBSTRINGS = ("滨江大道2727号", "星衡国际会议中心")

_WS = re.compile(r"[\s\u3000]+")


def _strip_ws(text: str | None) -> str:
    return _WS.sub("", text or "")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def judge(fields: dict[str, Any]) -> dict[str, Any]:
    """Apply the frozen S-11口径 to the extracted fields; every check is explicit."""
    destination = fields.get("destination")
    start, end = fields.get("start_date"), fields.get("end_date")
    purpose = fields.get("purpose")
    address = fields.get("meeting_address")
    addr_norm = _strip_ws(address)
    checks = {
        "city": {"pass": EXPECT_CITY in _strip_ws(destination), "expect_contains": EXPECT_CITY,
                 "actual": destination},
        "start_date": {"pass": (start or "").strip() == EXPECT_START, "expect": EXPECT_START, "actual": start},
        "end_date": {"pass": (end or "").strip() == EXPECT_END, "expect": EXPECT_END, "actual": end},
        "purpose": {"pass": EXPECT_PURPOSE_SUBSTRING in (purpose or ""),
                    "expect_contains": EXPECT_PURPOSE_SUBSTRING, "actual": purpose},
        "meeting_address": {
            "pass": all(s in addr_norm for s in EXPECT_ADDRESS_SUBSTRINGS),
            "expect_contains_all": list(EXPECT_ADDRESS_SUBSTRINGS),
            "actual": address, "actual_whitespace_stripped": addr_norm,
            "missing": [s for s in EXPECT_ADDRESS_SUBSTRINGS if s not in addr_norm],
        },
    }
    checks["dates"] = {"pass": checks["start_date"]["pass"] and checks["end_date"]["pass"]}
    all_pass = all(checks[k]["pass"] for k in ("city", "dates", "purpose", "meeting_address"))
    return {"checks": checks, "all_pass": all_pass}


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
    if not FIXTURE.is_file():
        sys.exit(f"S-11 fixture not found: {FIXTURE}")

    settings = replace(get_settings(), llm_mode=args.mode)
    image = FIXTURE.read_bytes()
    sha = sha256_bytes(image)
    before = len(settings.usage_log_path.read_text(encoding="utf-8").splitlines()) if settings.usage_log_path.is_file() else 0

    extractor = build_invitation_extractor(settings)
    result = asyncio.run(extractor.extract(image, "image/png", sha))
    verdict = judge(result.fields)

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "llm_mode": args.mode, "extractor": extractor.name, "generator": result.generator,
        "vision_model": settings.vision_model,
        "fixture": str(FIXTURE.relative_to(REPO_ROOT)), "image_sha256": sha, "image_bytes": len(image),
        "metric_spec": "evals/METRICS.md#4",
        # Desensitised request summary: NO image bytes, NO key value (HC-07).
        "request_summary": {
            "model": getattr(extractor, "model", settings.vision_model),
            "purpose": "image_extraction",
            "endpoint": "DashScope OpenAI-compatible chat/completions (function-calling structured output)",
            "transport": "local file sent as base64 data URL; no OSS upload (RISK-002)",
            "image": {"content_type": "image/png", "sha256": sha, "bytes": len(image),
                      "raw_bytes_included": False},
            "messages": [
                {"role": "system", "text": "从会议邀请函图片中抽取差旅信息。只抽取图片中明确出现的内容，不要猜测。"},
                {"role": "user", "text": "请抽取会议城市、起止日期、事由和详细地址。",
                 "image_url": "<base64 data URL omitted>"},
            ],
            "schema_fields": list(INVITATION_FIELDS),
            "api_key": "DASHSCOPE_API_KEY (name checked present; value never read/logged/committed)",
        },
        "extraction": result.fields,
        "verdict": verdict,
        "passed": verdict["all_pass"],
    }
    if args.mode == "real":
        report["usage"] = _usage_since(settings, before)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    c = verdict["checks"]
    print(f"mode={args.mode} extractor={result.generator} passed={verdict['all_pass']}")
    for k in ("city", "start_date", "end_date", "purpose", "meeting_address"):
        print(f'  {k:16} {"PASS" if c[k]["pass"] else "FAIL"}  actual={c[k].get("actual")!r}')
    if "usage" in report:
        print(f"  usage: {report['usage']['n']} calls, {report['usage']['total_tokens']} tokens, "
              f"{report['usage']['est_cost_cny']} CNY")
    print(f"wrote {args.out}")
    return 0 if verdict["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
