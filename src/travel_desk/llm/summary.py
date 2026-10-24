"""Itinerary summary paragraph (§4.7: the model may write only this paragraph).

The summary is built from a context the code has already decided (selected
candidates, dates, weather); it never feeds back into amounts, candidates,
rule results or approval outcomes. Its text is checked before use: blank,
over-long or money-quoting output is rejected and the plan carries
`summary.status = "unavailable"` instead of an invented paragraph.

Implementations:
- `FakeSummarizer` — deterministic template, zero network; labelled `generator="fake"`.
- `RecordedSummarizer` — frozen outputs from `tests/fixtures/recorded/summaries.json` (§7.1.1).
- `RealSummarizer` — DashScope text model via `llm/dashscope.py` (metered, budget-guarded).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from travel_desk.config import Settings
from travel_desk.domain.dates import to_local
from travel_desk.domain.models import TripSlots
from travel_desk.llm.dashscope import ModelUnavailable, UsageMeter, text_call
from travel_desk.planning.plan import Plan, PlanSummary
from travel_desk.services.idempotency import fingerprint

MAX_SUMMARY_CHARS = 400
_MONEY = re.compile(r"[¥￥]|\d\s*(元|块钱|RMB|人民币)|RMB|人民币")
RECORDED_FILE = "summaries.json"


class SummaryUnavailable(Exception):
    """The summary could not be produced; the plan continues without it."""


@dataclass(frozen=True)
class SummaryText:
    text: str
    generator: str


class Summarizer(Protocol):
    name: str

    async def summarize(self, context: dict[str, Any]) -> SummaryText: ...


def _leg(plan_leg) -> dict[str, Any] | None:
    if plan_leg is None:
        return None
    return {"service_no": plan_leg.service_no or plan_leg.id, "origin": plan_leg.origin,
            "destination": plan_leg.destination,
            "depart_local": to_local(plan_leg.depart_at).strftime("%H:%M"),
            "arrive_local": to_local(plan_leg.arrive_at).strftime("%H:%M")}


def summary_context(slots: TripSlots, plan: Plan) -> dict[str, Any]:
    hotel = plan.hotel.candidate
    return {
        "origin": slots.origin, "destination": slots.destination,
        "start_date": slots.start_date.isoformat(), "end_date": slots.end_date.isoformat(),
        "purpose": slots.purpose, "meeting_address": slots.meeting_address,
        "outbound": _leg(plan.transport.outbound), "return": _leg(plan.transport.return_leg),
        "hotel_status": plan.hotel.status,
        "hotel": {"name": hotel.name, "nights": plan.hotel.nights} if hotel is not None else None,
        "weather": [{"date": d.date.isoformat(), "summary": d.summary} for d in plan.weather.days],
    }


def context_key(context: dict[str, Any]) -> str:
    return fingerprint(context)[:16]


def check_summary(text: Any) -> str:
    if not isinstance(text, str) or not text.strip():
        raise SummaryUnavailable("模型返回空摘要")
    cleaned = " ".join(text.split())
    if len(cleaned) > MAX_SUMMARY_CHARS:
        raise SummaryUnavailable(f"模型摘要超过 {MAX_SUMMARY_CHARS} 字")
    if _MONEY.search(cleaned):
        # Amounts belong to the code-computed cost lines only (§4.7).
        raise SummaryUnavailable("模型摘要包含金额，已丢弃")
    return cleaned


class FakeSummarizer:
    name = "fake"

    async def summarize(self, context: dict[str, Any]) -> SummaryText:
        parts = [f"{context['start_date']}至{context['end_date']}赴{context['destination']}，"
                 f"事由：{context['purpose']}。"]
        out, back = context["outbound"], context["return"]
        parts.append(f"去程乘 {out['service_no']} 于 {out['depart_local']} 出发、{out['arrive_local']} 抵达。"
                     if out else "去程交通待人工补录。")
        if context["hotel"]:
            parts.append(f"入住{context['hotel']['name']}，共 {context['hotel']['nights']} 晚。")
        elif context["hotel_status"] == "manual_required":
            parts.append("住宿待人工补录。")
        if any("雨" in day["summary"] for day in context["weather"]):
            parts.append("行程期间有雨，请携带雨具。")
        parts.append(f"返程乘 {back['service_no']} 于 {back['depart_local']} 出发。"
                     if back else "返程交通待人工补录。")
        return SummaryText(text="".join(parts), generator="fake")


class RecordedSummarizer:
    name = "recorded"

    def __init__(self, recorded_dir: Path):
        self.path = recorded_dir / RECORDED_FILE

    async def summarize(self, context: dict[str, Any]) -> SummaryText:
        if not self.path.is_file():
            raise SummaryUnavailable(f"缺少录制输出文件 {RECORDED_FILE}")
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8"))["entries"]
            entry = entries[context_key(context)]
        except KeyError:
            raise SummaryUnavailable("录制输出中没有该行程的摘要") from None
        except (ValueError, TypeError):
            raise SummaryUnavailable("录制输出文件格式无效") from None
        source = entry.get("source") if isinstance(entry, dict) else None
        if source not in ("model", "hand_written"):
            raise SummaryUnavailable("录制输出缺少 source 标注")
        return SummaryText(text=entry.get("text"), generator=f"recorded:{source}")


class RealSummarizer:
    """DashScope text model (AUTH-01, metered). Its output passes `check_summary` like any other."""

    name = "real"

    def __init__(self, settings: Settings, meter: UsageMeter):
        self.settings = settings
        self.meter = meter
        self.model = settings.text_model

    async def summarize(self, context: dict[str, Any]) -> SummaryText:
        prompt = ("请根据下列已确定的差旅安排写一段不超过 150 字的中文行程摘要，供员工阅读。"
                  "只能使用给出的信息，不得编造班次、酒店或时间，不得出现任何金额或价格。\n"
                  + json.dumps(context, ensure_ascii=False))
        try:
            text = await text_call(self.settings, self.meter, purpose="itinerary_summary", model=self.model,
                                   messages=[("system", "你是差旅助理，只做文字概括。"), ("human", prompt)])
        except ModelUnavailable as exc:
            raise SummaryUnavailable(exc.reason) from None
        return SummaryText(text=text, generator=self.model)


def build_summarizer(settings: Settings) -> Summarizer:
    if settings.llm_mode == "fake":
        return FakeSummarizer()
    if settings.llm_mode == "recorded":
        return RecordedSummarizer(settings.recorded_dir)
    return RealSummarizer(settings, UsageMeter.from_settings(settings))


async def make_summary(summarizer: Summarizer, slots: TripSlots,
                       plan: Plan) -> tuple[PlanSummary, str | None]:
    """(summary, error message or None). Failure degrades to `status="unavailable"`."""
    generator = getattr(summarizer, "model", None) or summarizer.name
    try:
        result = await summarizer.summarize(summary_context(slots, plan))
        text = check_summary(result.text)
    except SummaryUnavailable as exc:
        return PlanSummary(status="unavailable", text=None, generator=generator,
                           note=f"行程摘要不可用：{exc}"), str(exc)
    return PlanSummary(status="ok", text=text, generator=result.generator, note=None), None
