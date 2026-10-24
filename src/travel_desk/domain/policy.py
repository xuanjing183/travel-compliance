"""Confirmed policy rules of one version, as typed and validated values.

Only the closed rule types of §4.2 can drive runtime decisions. A `RuleSet` is
built from the confirmed rules bound to a Trip revision; it refuses to exist
if a closed type is missing, malformed or ambiguous, so the evaluator never
has to guess (publish uses the same check, §6.5 "封闭类型缺少已确认规则返回 422").
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

CLOSED_RULE_TYPES = (
    "city_tier",
    "hotel_cap",
    "meal_allowance",
    "cabin_class",
    "advance_booking_days",
    "auto_approve_limit",
    "trip_duration_limit",
)
HOTEL_LEVELS = ("employee", "manager")
TransportModeScope = Literal["air", "rail", "any"]


class PolicyRuleError(ValueError):
    """The rule set cannot be used for evaluation. `problems` lists each issue."""

    def __init__(self, problems: list[dict[str, Any]]):
        self.problems = problems
        super().__init__("; ".join(p["message"] for p in problems))


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CityTierValue(_Value):
    tiers: dict[str, list[str]]
    fallback_tier: str = Field(min_length=1)

    @model_validator(mode="after")
    def _no_city_in_two_tiers(self) -> CityTierValue:
        seen: dict[str, str] = {}
        for tier, cities in self.tiers.items():
            for city in cities:
                key = normalize_city(city)
                if key in seen and seen[key] != tier:
                    raise ValueError(f"city {city} appears in tiers {seen[key]} and {tier}")
                seen[key] = tier
        return self


class TierAmountsValue(_Value):
    caps_fen: dict[str, StrictInt]

    @model_validator(mode="after")
    def _positive(self) -> TierAmountsValue:
        if not self.caps_fen or any(v <= 0 for v in self.caps_fen.values()):
            raise ValueError("caps_fen must be positive integers in fen")
        return self


class CabinValue(_Value):
    allowed: list[str] = Field(min_length=1)


class DaysValue(_Value):
    days: StrictInt = Field(ge=0)


class LimitFenValue(_Value):
    value_fen: StrictInt = Field(gt=0)


class LevelCondition(_Value):
    level: Literal["employee", "manager"]


class ModeCondition(_Value):
    mode: TransportModeScope


class ScopeAll(_Value):
    scope: Literal["all"]


_SCHEMAS: dict[str, tuple[type[_Value], type[_Value]]] = {
    "city_tier": (ScopeAll, CityTierValue),
    "hotel_cap": (LevelCondition, TierAmountsValue),
    "meal_allowance": (ScopeAll, TierAmountsValue),
    "cabin_class": (ScopeAll, CabinValue),
    "advance_booking_days": (ModeCondition, DaysValue),
    "auto_approve_limit": (ScopeAll, LimitFenValue),
    "trip_duration_limit": (ScopeAll, DaysValue),
}


def normalize_city(name: str) -> str:
    """Canonical city key: trimmed, without a trailing 市 (上海市 → 上海)."""
    text = "".join(name.split())
    return text[:-1] if len(text) > 1 and text.endswith("市") else text


def validate_rule_shape(rule_type: str, condition: Any, value: Any) -> list[dict[str, Any]]:
    """Schema problems for one rule; [] means the rule is well-formed.

    Unknown types are reported as `unsupported` so callers can mark them instead
    of silently dropping them (§4.2).
    """
    if rule_type not in _SCHEMAS:
        return [{"code": "UNSUPPORTED_RULE_TYPE", "type": rule_type,
                 "message": f"unsupported rule type {rule_type}"}]
    condition_model, value_model = _SCHEMAS[rule_type]
    problems = []
    for label, model, payload in (("condition", condition_model, condition),
                                  ("value", value_model, value)):
        try:
            model.model_validate(payload)
        except ValidationError as exc:
            problems.append({"code": "INVALID_RULE_SHAPE", "type": rule_type, "field": label,
                             "message": f"{rule_type} {label} invalid: {exc.errors()[0]['msg']}"})
    return problems


@dataclass(frozen=True)
class RuleRef:
    """The traceable part of a rule (HC-02)."""

    rule_id: str
    policy_version: str
    clause: str
    quote: str
    locator: str
    unit: str


@dataclass(frozen=True)
class RuleSet:
    version: str
    city_tiers: dict[str, str]
    fallback_tier: str
    hotel_caps: dict[str, dict[str, int]]
    meal_allowance: dict[str, int]
    allowed_cabins: frozenset[str]
    advance_days: dict[str, int]
    auto_limit_fen: int
    max_trip_days: int
    refs: dict[str, RuleRef]  # keyed by rule_id
    by_type: dict[str, tuple[str, ...]]  # rule type -> rule ids
    hotel_rule_by_level: dict[str, str]
    advance_rule_by_mode: dict[str, str]

    def tier_of(self, city: str) -> str:
        return self.city_tiers.get(normalize_city(city), self.fallback_tier)

    def hotel_cap(self, level: str, tier: str) -> tuple[int, RuleRef]:
        rule_id = self.hotel_rule_by_level[level]
        return self.hotel_caps[level][tier], self.refs[rule_id]

    def meal_per_day(self, tier: str) -> tuple[int, RuleRef]:
        return self.meal_allowance[tier], self.ref("meal_allowance")

    def advance_requirement(self, mode: str) -> tuple[int, RuleRef] | None:
        rule_id = self.advance_rule_by_mode.get(mode) or self.advance_rule_by_mode.get("any")
        if rule_id is None:
            return None
        scope = mode if mode in self.advance_days else "any"
        return self.advance_days[scope], self.refs[rule_id]

    def ref(self, rule_type: str) -> RuleRef:
        return self.refs[self.by_type[rule_type][0]]

    @classmethod
    def from_rules(cls, version: str, rules: Iterable[Mapping[str, Any]]) -> RuleSet:
        """Build from rule rows (decoded JSON). Only `confirmed` rules are used."""
        problems: list[dict[str, Any]] = []
        confirmed = [r for r in rules if r.get("status", "confirmed") == "confirmed"]
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for rule in confirmed:
            if rule.get("version", version) != version:
                problems.append({"code": "VERSION_MISMATCH", "rule_id": rule["id"],
                                 "message": f"rule {rule['id']} belongs to {rule.get('version')}"})
                continue
            if rule.get("quote_status", "valid") != "valid":
                problems.append({"code": "INVALID_QUOTE", "rule_id": rule["id"],
                                 "message": f"rule {rule['id']} has an invalid quote"})
                continue
            shape = validate_rule_shape(rule["type"], rule["condition"], rule["value"])
            if shape:
                problems.extend({**p, "rule_id": rule["id"]} for p in shape)
                continue
            grouped.setdefault(rule["type"], []).append(rule)

        for rule_type in CLOSED_RULE_TYPES:
            if rule_type not in grouped:
                problems.append({"code": "MISSING_RULE_TYPE", "type": rule_type,
                                 "message": f"no confirmed {rule_type} rule"})
        for rule_type in ("city_tier", "meal_allowance", "cabin_class", "auto_approve_limit",
                          "trip_duration_limit"):
            if len(grouped.get(rule_type, [])) > 1:
                ids = [r["id"] for r in grouped[rule_type]]
                problems.append({"code": "CONFLICTING_RULES", "type": rule_type, "rule_ids": ids,
                                 "message": f"more than one confirmed {rule_type} rule: {ids}"})

        hotel_rule_by_level: dict[str, str] = {}
        hotel_caps: dict[str, dict[str, int]] = {}
        for rule in grouped.get("hotel_cap", []):
            level = rule["condition"]["level"]
            if level in hotel_rule_by_level:
                problems.append({"code": "CONFLICTING_RULES", "type": "hotel_cap",
                                 "rule_ids": [hotel_rule_by_level[level], rule["id"]],
                                 "message": f"two confirmed hotel caps for level {level}"})
                continue
            hotel_rule_by_level[level] = rule["id"]
            hotel_caps[level] = dict(rule["value"]["caps_fen"])
        if "hotel_cap" in grouped:
            for level in HOTEL_LEVELS:
                if level not in hotel_rule_by_level:
                    problems.append({"code": "MISSING_RULE_TYPE", "type": "hotel_cap", "level": level,
                                     "message": f"no confirmed hotel cap for level {level}"})

        advance_rule_by_mode: dict[str, str] = {}
        advance_days: dict[str, int] = {}
        for rule in grouped.get("advance_booking_days", []):
            mode = rule["condition"]["mode"]
            if mode in advance_rule_by_mode:
                problems.append({"code": "CONFLICTING_RULES", "type": "advance_booking_days",
                                 "rule_ids": [advance_rule_by_mode[mode], rule["id"]],
                                 "message": f"two confirmed advance-booking rules for mode {mode}"})
                continue
            advance_rule_by_mode[mode] = rule["id"]
            advance_days[mode] = rule["value"]["days"]

        if problems:
            raise PolicyRuleError(problems)

        city_value = grouped["city_tier"][0]["value"]
        city_tiers = {normalize_city(city): tier
                      for tier, cities in city_value["tiers"].items() for city in cities}
        fallback = city_value["fallback_tier"]
        tiers = set(city_tiers.values()) | {fallback}
        meal = dict(grouped["meal_allowance"][0]["value"]["caps_fen"])
        for label, amounts in [("meal_allowance", meal)] + [
            (f"hotel_cap[{level}]", caps) for level, caps in hotel_caps.items()
        ]:
            uncovered = sorted(tiers - set(amounts))
            if uncovered:
                problems.append({"code": "TIER_NOT_COVERED", "type": label, "tiers": uncovered,
                                 "message": f"{label} has no amount for tiers {uncovered}"})
        if problems:
            raise PolicyRuleError(problems)

        refs = {
            rule["id"]: RuleRef(rule_id=rule["id"], policy_version=version, clause=rule["clause"],
                                quote=rule["quote"], locator=rule["locator"], unit=rule["unit"])
            for rules_of_type in grouped.values() for rule in rules_of_type
        }
        return cls(
            version=version,
            city_tiers=city_tiers,
            fallback_tier=fallback,
            hotel_caps=hotel_caps,
            meal_allowance=meal,
            allowed_cabins=frozenset(grouped["cabin_class"][0]["value"]["allowed"]),
            advance_days=advance_days,
            auto_limit_fen=grouped["auto_approve_limit"][0]["value"]["value_fen"],
            max_trip_days=grouped["trip_duration_limit"][0]["value"]["days"],
            refs=refs,
            by_type={t: tuple(r["id"] for r in rs) for t, rs in grouped.items()},
            hotel_rule_by_level=hotel_rule_by_level,
            advance_rule_by_mode=advance_rule_by_mode,
        )
