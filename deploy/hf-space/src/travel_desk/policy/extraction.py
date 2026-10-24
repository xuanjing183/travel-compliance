"""Rule extraction from a policy document (REQ-006, §4.2, §4.8).

The extractor (recorded fixture or real model) only *proposes* raw candidates:
type, condition, value, unit, clause and quote. Everything that decides what a
candidate may become is done by code in `compile_candidates`:

- the quote is looked up in the normalized document (§4.8) → `quote_status`
  `valid` with a locator, or `invalid_quote` (can never be confirmed);
- a type outside the 7 closed types → status `unsupported` (kept, never used at
  runtime, never silently dropped);
- rule IDs are assigned by code from the type and order (`RULE-HOTEL-001`, …),
  so a re-extraction of the same text yields the same IDs as the seed data.

Recorded mode (`fake` and `recorded`): `recorded/extract-*.json` matched by the
SHA-256 of the normalized document text. An unknown document is reported as
unavailable rather than guessed (same stance as image extraction).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from travel_desk.config import Settings
from travel_desk.domain.policy import CLOSED_RULE_TYPES
from travel_desk.llm.dashscope import ModelUnavailable, UsageMeter, structured_call
from travel_desk.policy.documents import PolicyDocument

ID_PREFIX = {
    "city_tier": "CITY", "hotel_cap": "HOTEL", "meal_allowance": "MEAL", "cabin_class": "CLASS",
    "advance_booking_days": "ADVANCE", "auto_approve_limit": "AUTO", "trip_duration_limit": "DURATION",
}
CANONICAL_UNITS = {
    "city_tier": "city_tier", "hotel_cap": "fen_per_night", "meal_allowance": "fen_per_day",
    "cabin_class": "cabin", "advance_booking_days": "days", "auto_approve_limit": "fen",
    "trip_duration_limit": "days",
}
MAX_CANDIDATES = 60


@dataclass(frozen=True)
class Extraction:
    candidates: list[dict[str, Any]]
    generator: str  # "recorded:hand_written" | "recorded:model" | model name


class RuleExtractor(Protocol):
    name: str

    async def extract(self, document: PolicyDocument) -> Extraction: ...


def _raw_candidate(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ModelUnavailable("抽取结果中的候选不是对象", retryable=False)
    raw = {
        "type": str(item.get("type") or "").strip() or "unknown",
        "condition": item.get("condition") if isinstance(item.get("condition"), dict) else {},
        "value": item.get("value") if isinstance(item.get("value"), dict) else {},
        "unit": str(item.get("unit") or "").strip(),
        "clause": str(item.get("clause") or "").strip(),
        "quote": str(item.get("quote") or ""),
    }
    return raw


class RecordedRuleExtractor:
    name = "recorded"

    def __init__(self, recorded_dir: Path):
        self.recorded_dir = recorded_dir

    async def extract(self, document: PolicyDocument) -> Extraction:
        digest = document.normalized_sha256
        for path in sorted(self.recorded_dir.glob("extract-*.json")):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ModelUnavailable(f"录制输出文件 {path.name} 格式无效", retryable=False) from None
            if digest not in body.get("documents", {}):
                continue
            source = body.get("source")
            if source not in ("model", "hand_written") or not isinstance(body.get("candidates"), list):
                raise ModelUnavailable(f"录制输出文件 {path.name} 缺少 source 或 candidates", retryable=False)
            return Extraction([_raw_candidate(c) for c in body["candidates"]], f"recorded:{source}")
        raise ModelUnavailable("当前为确定性模式，录制输出中没有这份制度文本的抽取结果；"
                               "真实抽取需切换到 real 模式", retryable=False)


class RuleCandidateOut(BaseModel):
    type: str = Field(description="规则类型：city_tier / hotel_cap / meal_allowance / cabin_class / "
                                  "advance_booking_days / auto_approve_limit / trip_duration_limit；"
                                  "不属于这 7 类时写一个简短的英文 snake_case 类型名")
    condition: dict[str, Any] = Field(description="适用条件，见说明中的结构")
    value: dict[str, Any] = Field(description="数值，金额一律换算为整数分（1 元 = 100 分）")
    unit: str = Field(description="单位，见说明")
    clause: str = Field(description="条款号，如 POL-2")
    quote: str = Field(description="支持该规则的原文，逐字摘录一个完整句子，不得改写")


class ExtractionOut(BaseModel):
    """从差旅制度中抽取的全部候选规则。"""

    rules: list[RuleCandidateOut] = Field(description="候选规则列表，按原文顺序")


EXTRACTION_PROMPT = """你是差旅制度规则抽取器。逐条阅读制度原文，把每一条可执行的规则抽取为结构化候选。
支持的 7 种类型与结构（金额统一为整数分）：
- city_tier: condition {"scope":"all"}; value {"tiers":{"A":[城市...],"B":[...]},
  "fallback_tier":"C"}; unit "city_tier"
- hotel_cap: condition {"level":"employee"} 或 {"level":"manager"};
  value {"caps_fen":{"A":50000,...}}; unit "fen_per_night"
- meal_allowance: condition {"scope":"all"}; value {"caps_fen":{"A":10000,...}}; unit "fen_per_day"
- cabin_class: condition {"scope":"all"}; value {"allowed":["economy","second_class"]}; unit "cabin"
- advance_booking_days: condition {"mode":"air"|"rail"|"any"}; value {"days":3}; unit "days"
- auto_approve_limit: condition {"scope":"all"}; value {"value_fen":300000}; unit "fen"
- trip_duration_limit: condition {"scope":"all"}; value {"days":15}; unit "days"
不属于这 7 类的条款也要输出（type 写简短英文名），不要丢弃。quote 必须逐字摘自原文。"""


class RealRuleExtractor:
    name = "real"

    def __init__(self, settings: Settings, meter: UsageMeter):
        self.settings = settings
        self.meter = meter
        self.model = settings.text_model

    async def extract(self, document: PolicyDocument) -> Extraction:
        out = await structured_call(
            self.settings, self.meter, purpose="policy_extraction", model=self.model, schema=ExtractionOut,
            timeout=120.0,
            messages=[("system", EXTRACTION_PROMPT),
                      ("user", "制度原文：\n" + "\n".join(s.text for s in document.segments))])
        return Extraction([_raw_candidate(r.model_dump()) for r in out.rules], self.model)


def build_rule_extractor(settings: Settings) -> RuleExtractor:
    if settings.llm_mode == "real":
        return RealRuleExtractor(settings, UsageMeter.from_settings(settings))
    return RecordedRuleExtractor(settings.recorded_dir)


def compile_candidates(document: PolicyDocument, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Code-side judgement of raw candidates: IDs, quote check, supported type."""
    if len(raw) > MAX_CANDIDATES:
        raise ModelUnavailable(f"抽取结果超过 {MAX_CANDIDATES} 条候选，已拒绝", retryable=False)
    counters: dict[str, int] = {}
    compiled = []
    for item in raw:
        supported = item["type"] in CLOSED_RULE_TYPES
        prefix = ID_PREFIX.get(item["type"], "OTHER")
        counters[prefix] = counters.get(prefix, 0) + 1
        locator = document.locate(item["quote"])
        compiled.append({
            "id": f"RULE-{prefix}-{counters[prefix]:03d}",
            "type": item["type"],
            "condition": item["condition"],
            "value": item["value"],
            "unit": item["unit"],
            "clause": item["clause"],
            "quote": item["quote"],
            "locator": locator or "",
            "quote_status": "valid" if locator else "invalid_quote",
            "status": "candidate" if supported else "unsupported",
        })
    return compiled
