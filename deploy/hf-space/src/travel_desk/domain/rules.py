"""Deterministic rule core (§4.3–§4.5, D-11, D-14, D-18).

Pure functions, no I/O: given slots, the selected candidates, the confirmed
rules of the bound version, the applicant's role and the Demo date, compute
blocks, the cost breakdown, policy violations, approval reasons and the
outcome. A model can never change any of these values (HC-01).

Boundary convention: a limit is "不超过" — a value equal to the limit complies,
one fen above it does not.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from travel_desk.domain.dates import local_date, trip_days, trip_nights
from travel_desk.domain.models import HotelCandidate, TransportCandidate, TripSlots
from travel_desk.domain.money import format_yuan
from travel_desk.domain.policy import RuleRef, RuleSet, normalize_city

Outcome = Literal["POLICY_BLOCKED", "PENDING_APPROVAL", "AUTO_APPROVED"]
ViolationCode = Literal["HOTEL_OVER_CAP", "ADVANCE_BOOKING_VIOLATION", "CABIN_CLASS_VIOLATION"]
ReasonCode = Literal["BUDGET_EXCEEDED", "OVER_AUTO_APPROVAL_LIMIT", "PLAN_INCOMPLETE"]
BlockCode = Literal["START_DATE_IN_PAST", "END_BEFORE_START", "DURATION_EXCEEDED"]
ROLE_TO_HOTEL_LEVEL = {"employee": "employee", "manager": "manager"}


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Block(_Out):
    code: BlockCode
    actual_value: Any
    limit_value: Any
    rule_id: str | None
    policy_version: str | None
    clause: str | None
    message: str


class CostBreakdown(_Out):
    nights: int
    days: int
    destination_tier: str
    transport_fen: int | None
    outbound_fen: int | None
    return_fen: int | None
    hotel_per_night_fen: int | None
    hotel_fen: int | None
    meal_per_day_fen: int
    meal_fen: int
    total_fen: int | None
    known_subtotal_fen: int
    declared_budget_fen: int
    budget_delta_fen: int | None
    missing_components: list[Literal["transport", "hotel"]]


class Violation(_Out):
    code: ViolationCode
    rule_id: str
    policy_version: str
    clause: str
    quote: str
    locator: str
    actual_value: Any
    limit_value: Any
    unit: str
    subject: str
    source: Literal["policy"] = "policy"
    message: str


class ReasonBasis(_Out):
    rule_id: str
    policy_version: str
    clause_ref: str


class ApprovalReason(_Out):
    code: ReasonCode
    actual_value: int | None
    limit_value: int | None
    unit: str
    lower_bound: bool = False
    missing: list[str] = []
    basis: ReasonBasis | None = None
    message: str


class RuleCitation(_Out):
    kind: Literal["rule"] = "rule"
    id: str
    version: str
    clause: str


class Evaluation(_Out):
    policy_version: str
    outcome: Outcome
    blocks: list[Block]
    cost_breakdown: CostBreakdown | None
    violations: list[Violation]
    approval_reasons: list[ApprovalReason]
    refs: list[RuleCitation]


@dataclass(frozen=True)
class PlanChoice:
    """Candidates selected for one revision. `None` means "待人工补录" for that part."""

    outbound: TransportCandidate | None
    return_leg: TransportCandidate | None
    hotel: HotelCandidate | None


class PlanMismatchError(ValueError):
    """The selected candidates do not belong to this trip (route, date or city)."""


def hotel_level(role: str) -> str:
    try:
        return ROLE_TO_HOTEL_LEVEL[role]
    except KeyError:
        raise ValueError(f"role {role!r} cannot apply for travel (§6.4)") from None


def _cite(ref: RuleRef) -> RuleCitation:
    return RuleCitation(id=ref.rule_id, version=ref.policy_version, clause=ref.clause)


# ---------------------------------------------------------------- hard blocks


def check_blocks(slots: TripSlots, rules: RuleSet, today: date) -> list[Block]:
    """Semantic input checks that run before any provider call (§4.5, D-14)."""
    blocks: list[Block] = []
    if slots.start_date < today:
        blocks.append(Block(
            code="START_DATE_IN_PAST", actual_value=slots.start_date.isoformat(),
            limit_value=today.isoformat(), rule_id=None, policy_version=None, clause=None,
            message=f"出发日期 {slots.start_date.isoformat()} 早于今天 {today.isoformat()}",
        ))
    if slots.end_date < slots.start_date:
        blocks.append(Block(
            code="END_BEFORE_START", actual_value=slots.end_date.isoformat(),
            limit_value=slots.start_date.isoformat(), rule_id=None, policy_version=None, clause=None,
            message=f"结束日期 {slots.end_date.isoformat()} 早于出发日期 {slots.start_date.isoformat()}",
        ))
    else:
        days = trip_days(slots.start_date, slots.end_date)
        if days > rules.max_trip_days:
            ref = rules.ref("trip_duration_limit")
            blocks.append(Block(
                code="DURATION_EXCEEDED", actual_value=days, limit_value=rules.max_trip_days,
                rule_id=ref.rule_id, policy_version=ref.policy_version, clause=ref.clause,
                message=f"出差 {days} 个自然日，超过制度上限 {rules.max_trip_days} 天",
            ))
    return blocks


# ----------------------------------------------------------------------- cost


def _check_choice(slots: TripSlots, choice: PlanChoice, nights: int) -> None:
    origin, destination = normalize_city(slots.origin), normalize_city(slots.destination)
    legs = (("outbound", choice.outbound, origin, destination, slots.start_date),
            ("return", choice.return_leg, destination, origin, slots.end_date))
    for label, leg, src, dst, day in legs:
        if leg is None:
            continue
        if (normalize_city(leg.origin), normalize_city(leg.destination)) != (src, dst):
            raise PlanMismatchError(f"{label} {leg.id} is not {src}→{dst}")
        if local_date(leg.depart_at) != day:
            raise PlanMismatchError(f"{label} {leg.id} does not depart on {day.isoformat()}")
    if choice.hotel is not None:
        if nights == 0:
            raise PlanMismatchError("a same-day trip has no hotel nights")
        if normalize_city(choice.hotel.city) != destination:
            raise PlanMismatchError(f"hotel {choice.hotel.id} is not in {destination}")


def compute_cost(slots: TripSlots, rules: RuleSet, choice: PlanChoice) -> CostBreakdown:
    nights = trip_nights(slots.start_date, slots.end_date)
    if nights < 0:
        raise ValueError("cost is undefined for a trip that ends before it starts")
    _check_choice(slots, choice, nights)
    days = nights + 1
    tier = rules.tier_of(slots.destination)
    meal_per_day, _ = rules.meal_per_day(tier)
    meal = meal_per_day * days
    missing: list[Literal["transport", "hotel"]] = []

    outbound = choice.outbound.price_fen if choice.outbound else None
    ret = choice.return_leg.price_fen if choice.return_leg else None
    transport = outbound + ret if outbound is not None and ret is not None else None
    if transport is None:
        missing.append("transport")

    if nights == 0:
        per_night, hotel = None, 0
    elif choice.hotel is None:
        per_night, hotel = None, None
        missing.append("hotel")
    else:
        per_night = choice.hotel.price_fen
        hotel = per_night * nights

    # D-18: a missing part is unknown, never zero. The known subtotal is the sum
    # of every priced part (a single priced leg included), so it is a true lower bound.
    known = meal + (outbound or 0) + (ret or 0) + (hotel or 0)
    total = None if missing else known
    return CostBreakdown(
        nights=nights, days=days, destination_tier=tier,
        transport_fen=transport, outbound_fen=outbound, return_fen=ret,
        hotel_per_night_fen=per_night, hotel_fen=hotel,
        meal_per_day_fen=meal_per_day, meal_fen=meal,
        total_fen=total, known_subtotal_fen=known,
        declared_budget_fen=slots.declared_budget_fen,
        budget_delta_fen=None if total is None else total - slots.declared_budget_fen,
        missing_components=missing,
    )


# ----------------------------------------------------------------- violations


def _violation(code: ViolationCode, ref: RuleRef, actual: Any, limit: Any, unit: str,
               subject: str, message: str) -> Violation:
    return Violation(code=code, rule_id=ref.rule_id, policy_version=ref.policy_version,
                     clause=ref.clause, quote=ref.quote, locator=ref.locator, actual_value=actual,
                     limit_value=limit, unit=unit, subject=subject, message=message)


def find_violations(slots: TripSlots, rules: RuleSet, role: str, today: date,
                    choice: PlanChoice) -> list[Violation]:
    """Only the three traceable kinds of D-11. Missing candidates produce none."""
    violations: list[Violation] = []
    if choice.hotel is not None:
        tier = rules.tier_of(slots.destination)
        cap, ref = rules.hotel_cap(hotel_level(role), tier)
        price = choice.hotel.price_fen
        if price > cap:
            violations.append(_violation(
                "HOTEL_OVER_CAP", ref, price, cap, "fen_per_night", choice.hotel.id,
                f"{choice.hotel.name} 每晚 {format_yuan(price)}，超过 {tier} 类城市上限 "
                f"{format_yuan(cap)}（超 {format_yuan(price - cap)}/晚）",
            ))
    for label, leg in (("去程", choice.outbound), ("返程", choice.return_leg)):
        if leg is None:
            continue
        if leg.cabin not in rules.allowed_cabins:
            ref = rules.ref("cabin_class")
            violations.append(_violation(
                "CABIN_CLASS_VIOLATION", ref, leg.cabin, sorted(rules.allowed_cabins), "cabin",
                leg.id, f"{label} {leg.id} 舱位 {leg.cabin} 不在允许范围内",
            ))
        requirement = rules.advance_requirement(leg.mode)
        if requirement is not None:
            required_days, ref = requirement
            advance = (local_date(leg.depart_at) - today).days
            if advance < required_days:
                violations.append(_violation(
                    "ADVANCE_BOOKING_VIOLATION", ref, advance, required_days, "days", leg.id,
                    f"{label} {leg.id} 提前 {advance} 天预订，少于制度要求的 {required_days} 天",
                ))
    return violations


# ------------------------------------------------------------ approval reasons


def find_approval_reasons(cost: CostBreakdown, rules: RuleSet) -> list[ApprovalReason]:
    reasons: list[ApprovalReason] = []
    budget = cost.declared_budget_fen
    amount = cost.total_fen if cost.total_fen is not None else cost.known_subtotal_fen
    if amount > budget:
        lower = cost.total_fen is None
        reasons.append(ApprovalReason(
            code="BUDGET_EXCEEDED", actual_value=amount, limit_value=budget, unit="fen",
            lower_bound=lower,
            message=(f"{'已知部分' if lower else '总额'} {format_yuan(amount)} 超过申报预算 "
                     f"{format_yuan(budget)}（超 {format_yuan(amount - budget)}）"),
        ))
    if cost.total_fen is not None and cost.total_fen > rules.auto_limit_fen:
        ref = rules.ref("auto_approve_limit")
        reasons.append(ApprovalReason(
            code="OVER_AUTO_APPROVAL_LIMIT", actual_value=cost.total_fen,
            limit_value=rules.auto_limit_fen, unit="fen",
            basis=ReasonBasis(rule_id=ref.rule_id, policy_version=ref.policy_version,
                              clause_ref=ref.clause),
            message=(f"总额 {format_yuan(cost.total_fen)} 超过自动通过阈值 "
                     f"{format_yuan(rules.auto_limit_fen)}"),
        ))
    if cost.total_fen is None:
        labels = {"transport": "交通", "hotel": "酒店"}
        reasons.append(ApprovalReason(
            code="PLAN_INCOMPLETE", actual_value=None, limit_value=None, unit="fen",
            missing=list(cost.missing_components),
            message="方案不完整：" + "、".join(labels[m] for m in cost.missing_components)
                    + "待人工补录，不能自动通过",
        ))
    return reasons


# ------------------------------------------------------------------- decision


def decide_outcome(blocks: list[Block], violations: list[Violation],
                   reasons: list[ApprovalReason]) -> Outcome:
    if blocks:
        return "POLICY_BLOCKED"
    if violations or reasons:
        return "PENDING_APPROVAL"
    return "AUTO_APPROVED"


def evaluate_trip(slots: TripSlots, rules: RuleSet, role: str, today: date,
                  choice: PlanChoice | None) -> Evaluation:
    """Full evaluation. With blocks, the plan is never priced (`choice` may be None)."""
    level = hotel_level(role)
    blocks = check_blocks(slots, rules, today)
    duration_ref = rules.ref("trip_duration_limit")
    if blocks:
        return Evaluation(policy_version=rules.version, outcome="POLICY_BLOCKED", blocks=blocks,
                          cost_breakdown=None, violations=[], approval_reasons=[],
                          refs=[_cite(duration_ref)])
    if choice is None:
        raise ValueError("a trip without blocks needs a plan choice")
    cost = compute_cost(slots, rules, choice)
    violations = find_violations(slots, rules, role, today, choice)
    reasons = find_approval_reasons(cost, rules)
    consulted = [rules.ref("city_tier"), rules.ref("meal_allowance"),
                 rules.refs[rules.hotel_rule_by_level[level]], rules.ref("cabin_class"),
                 *[rules.refs[rid] for rid in rules.by_type["advance_booking_days"]],
                 rules.ref("auto_approve_limit"), duration_ref]
    return Evaluation(
        policy_version=rules.version, outcome=decide_outcome(blocks, violations, reasons),
        blocks=[], cost_breakdown=cost, violations=violations, approval_reasons=reasons,
        refs=[_cite(ref) for ref in consulted],
    )
