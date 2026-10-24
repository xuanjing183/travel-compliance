"""Slot collection: which required fields are missing or format-invalid (§4.4, D-14).

Format problems keep the Trip in COLLECTING and are asked about again. Semantic
problems (dates in the past, end before start, too long) are *not* checked here;
they become hard blocks at submit time (`rules.check_blocks`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from travel_desk.domain.dates import parse_iso_date
from travel_desk.domain.models import Preferences, TripSlots
from travel_desk.domain.policy import normalize_city

REQUIRED_FIELDS = ("origin", "destination", "start_date", "end_date", "purpose", "declared_budget_fen")
OPTIONAL_FIELDS = ("meeting_address", "preferences")
SLOT_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
TEXT_FIELDS = ("origin", "destination", "purpose")


@dataclass(frozen=True)
class SlotState:
    values: dict[str, Any]
    missing_fields: list[str]
    invalid_fields: list[dict[str, str]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing_fields and not self.invalid_fields

    def to_slots(self) -> TripSlots:
        if not self.complete:
            raise ValueError("slots are incomplete or invalid")
        return TripSlots.model_validate(self.values)


def field_problem(name: str, value: Any) -> dict[str, str] | None:
    """Format check for one present (non-null) slot value."""
    if name in TEXT_FIELDS:
        if not isinstance(value, str) or not value.strip():
            return {"field": name, "code": "BLANK", "message": "不能为空"}
    elif name in ("start_date", "end_date"):
        try:
            parse_iso_date(value)
        except (ValueError, TypeError):
            return {"field": name, "code": "INVALID_DATE", "message": "需要明确日期（YYYY-MM-DD）"}
    elif name == "declared_budget_fen":
        if isinstance(value, bool) or not isinstance(value, int):
            return {"field": name, "code": "NOT_INTEGER", "message": "预算必须是整数（分）"}
        if value <= 0:
            return {"field": name, "code": "NOT_POSITIVE", "message": "预算必须大于 0"}
    elif name == "meeting_address":
        if not isinstance(value, str):
            return {"field": name, "code": "NOT_TEXT", "message": "会议地址必须是文本"}
    elif name == "preferences":
        try:
            Preferences.model_validate(value)
        except ValidationError:
            return {"field": name, "code": "INVALID_PREFERENCES",
                    "message": "偏好只支持 avoid_early_departure（布尔）与 notes（≤200 字）"}
    else:
        return {"field": name, "code": "UNKNOWN_FIELD", "message": "未知字段"}
    return None


def normalize_value(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in ("origin", "destination"):
        return normalize_city(value)
    if name in TEXT_FIELDS or name == "meeting_address":
        text = " ".join(value.split())
        if name == "meeting_address":
            return text or None
        return text
    if name == "preferences":
        return Preferences.model_validate(value).model_dump(exclude_none=True)
    return value


def evaluate_slots(raw: dict[str, Any]) -> SlotState:
    """Normalise stored slot values and report missing/invalid fields in contract order."""
    values: dict[str, Any] = {name: None for name in SLOT_FIELDS}
    invalid: list[dict[str, str]] = []
    for name in SLOT_FIELDS:
        value = raw.get(name)
        if value is None:
            continue
        problem = field_problem(name, value)
        if problem:
            invalid.append(problem)
            values[name] = value
        else:
            values[name] = normalize_value(name, value)
    for name in raw:
        if name not in SLOT_FIELDS:
            invalid.append({"field": name, "code": "UNKNOWN_FIELD", "message": "未知字段"})
    missing = [name for name in REQUIRED_FIELDS if values[name] is None]
    return SlotState(values=values, missing_fields=missing, invalid_fields=invalid)
