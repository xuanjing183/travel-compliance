"""Slot extraction from a free-text message (REQ-001/002).

An extractor only reports what the user *stated*; it never fills a field from
common sense, the employee profile or a vague phrase ("下周", "月底"). Its output is
raw: the conversation service format-checks every value with the same rules as
`PATCH /slots`, applies the valid ones and asks about the rest.

Implementations:
- `FakeSlotExtractor` — deterministic regular-expression parser, zero network.
  It is test scaffolding labelled `generator="fake"`, not a model.
- `RecordedSlotExtractor` — frozen outputs in `recorded/slot_extractions.json` (§7.1.1).
- `RealSlotExtractor` — DashScope structured output (AUTH-01, metered).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from travel_desk.config import Settings
from travel_desk.db.seed import CITY_TIERS
from travel_desk.llm.dashscope import ModelUnavailable, UsageMeter, structured_call
from travel_desk.services.idempotency import fingerprint

RECORDED_FILE = "slot_extractions.json"
MAX_MESSAGE_CHARS = 2000


@dataclass(frozen=True)
class Extraction:
    fields: dict[str, Any] = field(default_factory=dict)
    generator: str = "fake"


class SlotExtractor(Protocol):
    name: str

    async def extract(self, text: str, today: date) -> Extraction: ...


def recorded_key(text: str, today: date) -> str:
    return fingerprint({"text": " ".join(text.split()), "today": today.isoformat()})[:16]


# ---------------------------------------------------------------------- fake parser

_EXTRA_CITIES = ["厦门", "青岛", "长沙", "郑州", "昆明", "大连", "合肥", "济南", "沈阳", "哈尔滨", "福州",
                 "宁波", "无锡", "三亚", "贵阳", "南宁", "南昌", "太原", "石家庄", "兰州", "乌鲁木齐"]
_CITIES = sorted({*CITY_TIERS["A"], *CITY_TIERS["B"], *_EXTRA_CITIES}, key=len, reverse=True)
_CITY = "(" + "|".join(_CITIES) + ")市?"
_PAIR = re.compile(_CITY + r"\s*(?:到|至|→|->|—|-)\s*" + _CITY)
_ORIGIN = [re.compile(r"从\s*" + _CITY), re.compile(_CITY + r"\s*出发"),
           re.compile(r"出发地\s*[:：是为]?\s*" + _CITY)]
_DESTINATION = [re.compile(r"(?:去|到|赴|前往)\s*" + _CITY), re.compile(r"目的地\s*[:：是为]?\s*" + _CITY)]
_ISO = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})")
_CN_DATE = re.compile(r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_RANGE_TAIL = re.compile(r"\s*(?:到|至|~|～|-|—)\s*(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*[日号]")
_SLASH = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")
_CN_NUM = "零一二两三四五六七八九十百千万"
_BUDGET = re.compile(r"预算[^0-9" + _CN_NUM + r"]{0,6}([0-9]+(?:\.[0-9]{1,2})?|[" + _CN_NUM + r"]+)\s*(万)?")
_PURPOSE = [re.compile(r"事由\s*[:：是为]?\s*([^，,。；;\s]+)"),
            re.compile(r"参加\s*([^，,。；;\s]+?)(?=[，,。；;\s]|预算|$)")]
_PURPOSE_KEYWORDS = ["供应商大会", "客户拜访", "拜访客户", "紧急会议", "项目验收", "技术交流", "培训", "开会"]
_ADDRESS = re.compile(r"(?:会议)?地址\s*[:：是为]?\s*([^，,。；;]+)")
_AVOID_EARLY = re.compile(r"(?:回避|避开|不要|不坐|不乘|别订)\s*早班")
_SAME_DAY = re.compile(r"当天往返|当日往返|当天来回")


def chinese_number(text: str) -> int:
    """Small Chinese numeral parser incl. colloquial forms: 四千 4000, 两千五 2500, 一万二 12000."""
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
              "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = section = number = 0
    last_unit, zero_seen = 1, False
    for char in text:
        if char == "零":
            zero_seen = True
        elif char in digits:
            number = digits[char]
        elif char in units:
            unit = units[char]
            if unit == 10000:
                total += (section + number) * unit
                section = 0
            else:
                section += (number or (1 if unit == 10 else 0)) * unit
            number, last_unit, zero_seen = 0, unit, False
    if number:
        section += number if zero_seen or last_unit < 10 else number * (last_unit // 10)
    return total + section


def _fmt(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def _dates(text: str, today: date) -> list[str]:
    found: list[tuple[int, str]] = []
    for match in _ISO.finditer(text):
        found.append((match.start(), _fmt(int(match[1]), int(match[2]), int(match[3]))))
    for match in _CN_DATE.finditer(text):
        year = int(match[1]) if match[1] else today.year
        month = int(match[2])
        found.append((match.start(), _fmt(year, month, int(match[3]))))
        tail = _RANGE_TAIL.match(text, match.end())
        if tail:
            found.append((tail.start(), _fmt(year, int(tail[1]) if tail[1] else month, int(tail[2]))))
    for match in _SLASH.finditer(text):
        found.append((match.start(), _fmt(today.year, int(match[1]), int(match[2]))))
    ordered: list[str] = []
    for _, value in sorted(found):
        if value not in ordered:
            ordered.append(value)
    return ordered


def parse_message(text: str, today: date) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    pair = _PAIR.search(text)
    if pair:
        fields["origin"], fields["destination"] = pair[1], pair[2]
    for pattern in _ORIGIN:
        if "origin" not in fields and (match := pattern.search(text)):
            fields["origin"] = match[1]
    for pattern in _DESTINATION:
        if "destination" in fields:
            break
        for match in pattern.finditer(text):
            if match[1] != fields.get("origin"):
                fields["destination"] = match[1]
                break
    dates = _dates(text, today)
    if dates:
        fields["start_date"] = dates[0]
        if len(dates) > 1:
            fields["end_date"] = dates[1]
        elif _SAME_DAY.search(text):
            fields["end_date"] = dates[0]
    if match := _BUDGET.search(text):
        raw = match[1]
        yuan = float(raw) if raw[0].isdigit() else chinese_number(raw)
        if match[2]:
            yuan *= 10000
        fields["declared_budget_fen"] = int(round(yuan * 100))
    for pattern in _PURPOSE:
        if match := pattern.search(text):
            fields["purpose"] = match[1]
            break
    else:
        for keyword in _PURPOSE_KEYWORDS:
            if keyword in text:
                fields["purpose"] = keyword
                break
    if match := _ADDRESS.search(text):
        fields["meeting_address"] = match[1].strip()
    if _AVOID_EARLY.search(text):
        fields["preferences"] = {"avoid_early_departure": True}
    return fields


class FakeSlotExtractor:
    name = "fake"

    async def extract(self, text: str, today: date) -> Extraction:
        return Extraction(fields=parse_message(text, today), generator="fake")


# ---------------------------------------------------------------------- recorded


class RecordedSlotExtractor:
    name = "recorded"

    def __init__(self, recorded_dir: Path):
        self.path = recorded_dir / RECORDED_FILE

    async def extract(self, text: str, today: date) -> Extraction:
        if not self.path.is_file():
            raise ModelUnavailable(f"缺少录制输出文件 {RECORDED_FILE}", retryable=False)
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8"))["entries"]
            entry = entries[recorded_key(text, today)]
        except KeyError:
            raise ModelUnavailable("录制输出中没有这句话的抽取结果", retryable=False) from None
        except (ValueError, TypeError):
            raise ModelUnavailable("录制输出文件格式无效", retryable=False) from None
        source = entry.get("source") if isinstance(entry, dict) else None
        if source not in ("model", "hand_written") or not isinstance(entry.get("fields"), dict):
            raise ModelUnavailable("录制输出缺少 source 或 fields", retryable=False)
        return Extraction(fields=dict(entry["fields"]), generator=f"recorded:{source}")


# ---------------------------------------------------------------------- real


class SlotsOut(BaseModel):
    """差旅申请槽位。无法从用户原话确定的字段必须为 null，不得猜测。"""

    origin: str | None = Field(None, description="出发城市，规范城市名，如 北京；用户未明确说明则为 null")
    destination: str | None = Field(None, description="目的城市，规范城市名")
    start_date: str | None = Field(
        None, description="出发日期 YYYY-MM-DD；只有用户给出明确日期时才填写，'下周''月底'等模糊表达为 null")
    end_date: str | None = Field(None, description="返回日期 YYYY-MM-DD；规则同 start_date")
    purpose: str | None = Field(None, description="出差事由，简短名词短语")
    declared_budget_yuan: float | None = Field(
        None, description="申报预算，单位：元；中文数字需转换为阿拉伯数字")
    meeting_address: str | None = Field(None, description="会议详细地址；未提及为 null")
    avoid_early_departure: bool | None = Field(
        None, description="用户明确要求回避早班（08:00 前出发）时为 true；未提及为 null")


def _system_prompt(today: date) -> str:
    return (f"你是差旅申请信息抽取器。今天是 {today.isoformat()}。只抽取用户这句话里明确给出的信息，"
            "未给出或表达模糊的字段一律返回 null，不要根据常识、上下文或员工常驻地补全。"
            "用户说“改成/改为”时只返回被修改的字段。")


def from_model_output(out: SlotsOut) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name in ("origin", "destination", "start_date", "end_date", "purpose", "meeting_address"):
        value = getattr(out, name)
        if isinstance(value, str) and value.strip():
            fields[name] = value.strip()
    if out.declared_budget_yuan is not None:
        fen = round(out.declared_budget_yuan * 100)
        fields["declared_budget_fen"] = int(fen)
    if out.avoid_early_departure is True:
        fields["preferences"] = {"avoid_early_departure": True}
    return fields


class RealSlotExtractor:
    name = "real"

    def __init__(self, settings: Settings, meter: UsageMeter):
        self.settings = settings
        self.meter = meter
        self.model = settings.text_model

    async def extract(self, text: str, today: date) -> Extraction:
        out = await structured_call(self.settings, self.meter, purpose="slot_extraction", model=self.model,
                                    schema=SlotsOut,
                                    messages=[("system", _system_prompt(today)), ("human", text)])
        return Extraction(fields=from_model_output(out), generator=self.model)


def build_slot_extractor(settings: Settings) -> SlotExtractor:
    if settings.llm_mode == "fake":
        return FakeSlotExtractor()
    if settings.llm_mode == "recorded":
        return RecordedSlotExtractor(settings.recorded_dir)
    return RealSlotExtractor(settings, UsageMeter.from_settings(settings))
