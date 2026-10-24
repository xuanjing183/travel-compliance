"""AC-006 rule-core tests: every rule, value == limit, limit + 1 fen, date boundaries.

Candidates come from the shipped provider fixtures (validated through the shared
models), and rules come from the seeded database, so these tests exercise the
same data the Demo uses. Expected numbers are the contract's (§7.2), not values
re-derived from the implementation.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from travel_desk.config import REPO_ROOT, Settings
from travel_desk.db.database import initialize_database
from travel_desk.db.repositories import list_policy_rules
from travel_desk.domain.models import HotelCandidate, TransportCandidate, TripSlots
from travel_desk.domain.policy import PolicyRuleError, RuleSet, normalize_city, validate_rule_shape
from travel_desk.domain.rules import (
    PlanChoice,
    PlanMismatchError,
    check_blocks,
    compute_cost,
    evaluate_trip,
)

TODAY = date(2026, 10, 20)
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "providers"


def _load_transport() -> dict[str, TransportCandidate]:
    data = json.loads((FIXTURES / "transport.json").read_text(encoding="utf-8"))
    return {c["id"]: TransportCandidate.model_validate(c) for c in data["candidates"]}


def _load_hotels() -> dict[str, HotelCandidate]:
    data = json.loads((FIXTURES / "hotels.json").read_text(encoding="utf-8"))
    return {h["id"]: HotelCandidate.model_validate(h) for h in data["hotels"]}


T = _load_transport()
H = _load_hotels()


@pytest.fixture
def seeded(settings: Settings) -> Settings:
    initialize_database(settings)
    return settings


@pytest.fixture
def rows_v12(seeded: Settings) -> list[dict[str, Any]]:
    return list_policy_rules(seeded.db_path, "v1.2")


@pytest.fixture
def v12(rows_v12) -> RuleSet:
    return RuleSet.from_rules("v1.2", rows_v12)


@pytest.fixture
def v10(seeded: Settings) -> RuleSet:
    return RuleSet.from_rules("v1.0", list_policy_rules(seeded.db_path, "v1.0"))


@pytest.fixture
def v13(rows_v12) -> RuleSet:
    """v1.3 = v1.2 with the employee A-tier hotel cap lowered to 45000 (§4.1)."""
    rows = []
    for row in rows_v12:
        row = {**row, "version": "v1.3"}
        if row["id"] == "RULE-HOTEL-001":
            row["value"] = {"caps_fen": {"A": 45000, "B": 40000, "C": 30000}}
            row["quote"] = "员工住宿上限：A 类城市每晚上限 450 元；B 类城市每晚上限 400 元；C 类城市每晚上限 300 元。"
        rows.append(row)
    return RuleSet.from_rules("v1.3", rows)


def slots(origin="北京", destination="上海", start="2026-10-28", end="2026-10-30",
          purpose="供应商大会", budget=400000) -> TripSlots:
    return TripSlots(origin=origin, destination=destination, start_date=start, end_date=end,
                     purpose=purpose, declared_budget_fen=budget)


def with_price(candidate, price: int):
    return candidate.model_copy(update={"price_fen": price})


def codes(items) -> list[str]:
    return [item.code for item in items]


# ------------------------------------------------------------------ rule set


def test_rule_set_from_seed_matches_contract_4_2(v12: RuleSet, v10: RuleSet) -> None:
    assert v12.tier_of("上海") == "A" and v12.tier_of("成都") == "B" and v12.tier_of("拉萨") == "C"
    assert v12.tier_of(" 上海市 ") == "A"
    assert v12.hotel_cap("employee", "A")[0] == 50000
    assert v12.hotel_cap("employee", "B")[0] == 40000
    assert v12.hotel_cap("employee", "C")[0] == 30000
    assert v12.hotel_cap("manager", "A")[0] == 65000
    assert v12.hotel_cap("manager", "B")[0] == 50000
    assert v12.hotel_cap("manager", "C")[0] == 40000
    assert [v12.meal_per_day(t)[0] for t in "ABC"] == [10000, 8000, 6000]
    assert v12.allowed_cabins == {"economy", "second_class"}
    assert v12.advance_requirement("air")[0] == 3
    assert v12.advance_requirement("rail") is None
    assert v12.auto_limit_fen == 300000 and v12.max_trip_days == 15
    assert v10.advance_requirement("air")[0] == 5 and v10.auto_limit_fen == 200000
    ref = v12.hotel_cap("employee", "A")[1]
    assert (ref.rule_id, ref.policy_version, ref.clause) == ("RULE-HOTEL-001", "v1.2", "POL-2")
    assert ref.locator.startswith("line:") and "500 元" in ref.quote


def test_rule_set_refuses_missing_or_ambiguous_rules(rows_v12) -> None:
    without_auto = [r for r in rows_v12 if r["type"] != "auto_approve_limit"]
    with pytest.raises(PolicyRuleError) as exc:
        RuleSet.from_rules("v1.2", without_auto)
    assert {p["code"] for p in exc.value.problems} == {"MISSING_RULE_TYPE"}

    without_manager_cap = [r for r in rows_v12 if r["id"] != "RULE-HOTEL-002"]
    with pytest.raises(PolicyRuleError) as exc:
        RuleSet.from_rules("v1.2", without_manager_cap)
    assert exc.value.problems[0]["level"] == "manager"

    duplicate = rows_v12 + [{**next(r for r in rows_v12 if r["id"] == "RULE-AUTO-001"), "id": "RULE-AUTO-002"}]
    with pytest.raises(PolicyRuleError) as exc:
        RuleSet.from_rules("v1.2", duplicate)
    assert exc.value.problems[0]["code"] == "CONFLICTING_RULES"

    float_money = [{**r, "value": {"value_fen": 3000.5}} if r["id"] == "RULE-AUTO-001" else r for r in rows_v12]
    with pytest.raises(PolicyRuleError) as exc:
        RuleSet.from_rules("v1.2", float_money)
    assert exc.value.problems[0]["code"] == "INVALID_RULE_SHAPE"

    bad_quote = [{**r, "quote_status": "invalid_quote"} if r["id"] == "RULE-MEAL-001" else r for r in rows_v12]
    with pytest.raises(PolicyRuleError):
        RuleSet.from_rules("v1.2", bad_quote)

    no_c_tier = [{**r, "value": {"caps_fen": {"A": 10000, "B": 8000}}} if r["id"] == "RULE-MEAL-001" else r
                 for r in rows_v12]
    with pytest.raises(PolicyRuleError) as exc:
        RuleSet.from_rules("v1.2", no_c_tier)
    assert exc.value.problems[0]["code"] == "TIER_NOT_COVERED"


def test_only_confirmed_rules_drive_decisions(rows_v12) -> None:
    candidate = {**next(r for r in rows_v12 if r["id"] == "RULE-AUTO-001"),
                 "id": "RULE-AUTO-009", "status": "candidate", "value": {"value_fen": 1}}
    rejected = {**candidate, "id": "RULE-AUTO-010", "status": "rejected"}
    rules = RuleSet.from_rules("v1.2", rows_v12 + [candidate, rejected])
    assert rules.auto_limit_fen == 300000


def test_unsupported_rule_type_is_reported_not_dropped() -> None:
    problems = validate_rule_shape("taxi_cap", {"scope": "all"}, {"value_fen": 100})
    assert problems[0]["code"] == "UNSUPPORTED_RULE_TYPE"
    assert validate_rule_shape("auto_approve_limit", {"scope": "all"}, {"value_fen": 300000}) == []


def test_city_normalisation() -> None:
    assert normalize_city("上海市") == "上海"
    assert normalize_city(" 北 京 ") == "北京"
    assert normalize_city("市") == "市"


# ------------------------------------------------------- contract scenarios


def test_s01_golden_path_cost_and_reasons(v12: RuleSet) -> None:
    choice = PlanChoice(T["T-BJS-SHA-1028-A"], T["T-SHA-BJS-1030-A"], H["H-SHA-CONTRACT-01"])
    result = evaluate_trip(slots(), v12, "employee", TODAY, choice)
    cost = result.cost_breakdown
    assert (cost.nights, cost.days) == (2, 3)
    assert (cost.transport_fen, cost.hotel_fen, cost.meal_fen, cost.total_fen) == (350000, 96000, 30000, 476000)
    assert cost.budget_delta_fen == 76000 and cost.missing_components == []
    assert result.violations == []
    assert codes(result.approval_reasons) == ["BUDGET_EXCEEDED", "OVER_AUTO_APPROVAL_LIMIT"]
    over = result.approval_reasons[1]
    assert over.basis.model_dump() == {"rule_id": "RULE-AUTO-001", "policy_version": "v1.2", "clause_ref": "POL-6"}
    assert result.approval_reasons[0].basis is None
    assert result.outcome == "PENDING_APPROVAL"
    assert {ref.id for ref in result.refs} >= {"RULE-CITY-001", "RULE-HOTEL-001", "RULE-MEAL-001", "RULE-AUTO-001"}


def test_s02_auto_approved(v12: RuleSet) -> None:
    choice = PlanChoice(T["T-BJS-SHA-1025-B"], T["T-SHA-BJS-1026-B"], H["H-SHA-CONTRACT-02"])
    result = evaluate_trip(slots(start="2026-10-25", end="2026-10-26", purpose="客户拜访", budget=250000),
                           v12, "employee", TODAY, choice)
    assert result.cost_breakdown.total_fen == 190000
    assert result.violations == [] and result.approval_reasons == []
    assert result.outcome == "AUTO_APPROVED"


def test_s03_backup_hotel_over_cap(v12: RuleSet) -> None:
    choice = PlanChoice(T["T-BJS-CTU-1027-A"], T["T-CTU-BJS-1029-A"], H["H-CTU-BACKUP-01"])
    result = evaluate_trip(slots(destination="成都", start="2026-10-27", end="2026-10-29", purpose="培训"),
                           v12, "employee", TODAY, choice)
    assert result.cost_breakdown.total_fen == 274000
    assert result.approval_reasons == []
    (violation,) = result.violations
    assert violation.code == "HOTEL_OVER_CAP" and violation.rule_id == "RULE-HOTEL-001"
    assert (violation.actual_value, violation.limit_value) == (45000, 40000)
    assert violation.actual_value - violation.limit_value == 5000
    assert violation.clause == "POL-2" and violation.source == "policy" and violation.quote
    assert violation.subject == "H-CTU-BACKUP-01" and violation.policy_version == "v1.2"
    assert result.outcome == "PENDING_APPROVAL"


def test_s04_short_notice_is_a_violation_not_a_block(v12: RuleSet) -> None:
    choice = PlanChoice(T["T-BJS-SHA-1022-E"], T["T-SHA-BJS-1023-E"], H["H-SHA-CONTRACT-02"])
    trip = slots(start="2026-10-22", end="2026-10-23", purpose="紧急会议", budget=300000)
    result = evaluate_trip(trip, v12, "employee", TODAY, choice)
    assert result.blocks == []
    assert result.cost_breakdown.total_fen == 180000
    (violation,) = result.violations  # return leg on 10-23 is exactly 3 days ahead: compliant
    assert violation.code == "ADVANCE_BOOKING_VIOLATION" and violation.subject == "T-BJS-SHA-1022-E"
    assert (violation.actual_value, violation.limit_value) == (2, 3)
    assert result.approval_reasons == [] and result.outcome == "PENDING_APPROVAL"


def test_s06_past_start_is_blocked_before_pricing(v12: RuleSet) -> None:
    trip = slots(start="2026-10-19", end="2026-10-20")
    result = evaluate_trip(trip, v12, "employee", TODAY, choice=None)
    assert result.outcome == "POLICY_BLOCKED" and codes(result.blocks) == ["START_DATE_IN_PAST"]
    assert result.cost_breakdown is None and result.violations == [] and result.approval_reasons == []
    assert result.blocks[0].rule_id is None


def test_s09_missing_transport_is_never_zero(v12: RuleSet) -> None:
    choice = PlanChoice(None, None, H["H-SHA-CONTRACT-02"])
    trip = slots(start="2026-10-25", end="2026-10-26", purpose="客户拜访", budget=250000)
    result = evaluate_trip(trip, v12, "employee", TODAY, choice)
    cost = result.cost_breakdown
    assert cost.transport_fen is None and cost.total_fen is None and cost.budget_delta_fen is None
    assert cost.known_subtotal_fen == 60000 and cost.missing_components == ["transport"]
    assert codes(result.approval_reasons) == ["PLAN_INCOMPLETE"]
    assert result.approval_reasons[0].missing == ["transport"]
    assert result.outcome == "PENDING_APPROVAL"  # S-02 with the same input is AUTO_APPROVED


def test_ac013_missing_hotel_uses_the_same_rule(v12: RuleSet) -> None:
    choice = PlanChoice(T["T-BJS-SHA-1025-B"], T["T-SHA-BJS-1026-B"], None)
    trip = slots(start="2026-10-25", end="2026-10-26", purpose="客户拜访", budget=250000)
    result = evaluate_trip(trip, v12, "employee", TODAY, choice)
    cost = result.cost_breakdown
    assert cost.total_fen is None and cost.hotel_fen is None
    assert cost.known_subtotal_fen == 150000 and cost.missing_components == ["hotel"]
    assert codes(result.approval_reasons) == ["PLAN_INCOMPLETE"]
    assert result.violations == [] and result.outcome == "PENDING_APPROVAL"


def test_ac023_inputs_auto_approve_with_either_outbound(v12: RuleSet) -> None:
    trip = slots(start="2026-11-05", end="2026-11-06", purpose="客户拜访", budget=250000)
    for outbound in ("T-BJS-SHA-1105-EARLY", "T-BJS-SHA-1105-LATE"):
        choice = PlanChoice(T[outbound], T["T-SHA-BJS-1106-P"], H["H-SHA-CONTRACT-02"])
        result = evaluate_trip(trip, v12, "employee", TODAY, choice)
        assert result.cost_breakdown.total_fen == 190000 and result.outcome == "AUTO_APPROVED"


def test_s12_same_candidates_under_v13_add_a_hotel_violation(v12: RuleSet, v13: RuleSet) -> None:
    choice = PlanChoice(T["T-BJS-SHA-1028-A"], T["T-SHA-BJS-1030-A"], H["H-SHA-CONTRACT-01"])
    before = evaluate_trip(slots(), v12, "employee", TODAY, choice)
    after = evaluate_trip(slots(), v13, "employee", TODAY, choice)
    assert before.violations == []
    (violation,) = after.violations
    assert violation.code == "HOTEL_OVER_CAP" and violation.policy_version == "v1.3"
    assert (violation.actual_value, violation.limit_value) == (48000, 45000)
    assert "450 元" in violation.quote
    assert after.cost_breakdown == before.cost_breakdown  # D-12: only the judgement changes
    assert codes(after.approval_reasons) == ["BUDGET_EXCEEDED", "OVER_AUTO_APPROVAL_LIMIT"]


def test_s08_raised_budget_still_needs_approval(v12: RuleSet) -> None:
    choice = PlanChoice(T["T-BJS-SHA-1028-A"], T["T-SHA-BJS-1030-A"], H["H-SHA-CONTRACT-01"])
    result = evaluate_trip(slots(budget=500000), v12, "employee", TODAY, choice)
    assert result.cost_breakdown.total_fen == 476000
    assert codes(result.approval_reasons) == ["OVER_AUTO_APPROVAL_LIMIT"]
    assert result.cost_breakdown.budget_delta_fen == -24000
    assert result.outcome == "PENDING_APPROVAL"


# ---------------------------------------------------------- boundary values

S02_TRIP = dict(start="2026-10-25", end="2026-10-26", purpose="客户拜访")


def _s02_choice(hotel_price=40000, out=None, ret=None):
    return PlanChoice(out or T["T-BJS-SHA-1025-B"], ret or T["T-SHA-BJS-1026-B"],
                      with_price(H["H-SHA-CONTRACT-02"], hotel_price))


@pytest.mark.parametrize(("role", "price", "violated"), [
    ("employee", 50000, False), ("employee", 50001, True),
    ("manager", 65000, False), ("manager", 65001, True),
])
def test_hotel_cap_boundary_by_level(v12: RuleSet, role, price, violated) -> None:
    result = evaluate_trip(slots(**S02_TRIP, budget=900000), v12, role, TODAY, _s02_choice(price))
    assert codes(result.violations) == (["HOTEL_OVER_CAP"] if violated else [])
    if violated:
        expected = "RULE-HOTEL-001" if role == "employee" else "RULE-HOTEL-002"
        assert result.violations[0].rule_id == expected


@pytest.mark.parametrize(("tier_city", "cap"), [("杭州", 40000), ("拉萨", 30000)])
def test_hotel_cap_boundary_by_tier(v12: RuleSet, tier_city, cap) -> None:
    hotel = H["H-SHA-CONTRACT-02"].model_copy(update={"city": tier_city})
    out = T["T-BJS-SHA-1025-B"].model_copy(update={"destination": tier_city})
    ret = T["T-SHA-BJS-1026-B"].model_copy(update={"origin": tier_city})
    trip = slots(destination=tier_city, **S02_TRIP, budget=900000)
    at_cap = evaluate_trip(trip, v12, "employee", TODAY, PlanChoice(out, ret, with_price(hotel, cap)))
    over = evaluate_trip(trip, v12, "employee", TODAY, PlanChoice(out, ret, with_price(hotel, cap + 1)))
    assert at_cap.violations == [] and codes(over.violations) == ["HOTEL_OVER_CAP"]


def test_meal_allowance_by_tier(v12: RuleSet) -> None:
    for city, per_day in (("上海", 10000), ("成都", 8000), ("拉萨", 6000)):
        hotel = H["H-SHA-CONTRACT-02"].model_copy(update={"city": city})
        out = T["T-BJS-SHA-1025-B"].model_copy(update={"destination": city})
        ret = T["T-SHA-BJS-1026-B"].model_copy(update={"origin": city})
        cost = compute_cost(slots(destination=city, **S02_TRIP), v12, PlanChoice(out, ret, hotel))
        assert (cost.meal_per_day_fen, cost.meal_fen) == (per_day, per_day * 2)


def _leg_on(candidate: TransportCandidate, day: date) -> TransportCandidate:
    """Move a fixture leg to another local departure date, keeping its clock time."""
    from travel_desk.domain.dates import local_date

    shift = timedelta(days=(day - local_date(candidate.depart_at)).days)
    return candidate.model_copy(update={"depart_at": candidate.depart_at + shift,
                                        "arrive_at": candidate.arrive_at + shift})


@pytest.mark.parametrize(("rules_name", "days_ahead", "violating"), [
    ("v12", 3, []),
    ("v12", 2, ["T-BJS-SHA-1025-B"]),                       # return leg is 3 days ahead: compliant
    ("v12", 1, ["T-BJS-SHA-1025-B", "T-SHA-BJS-1026-B"]),
    ("v12", 0, ["T-BJS-SHA-1025-B", "T-SHA-BJS-1026-B"]),
    ("v10", 5, []),
    ("v10", 4, ["T-BJS-SHA-1025-B"]),
])
def test_advance_booking_boundary(request, rules_name, days_ahead, violating) -> None:
    rules = request.getfixturevalue(rules_name)
    start = TODAY + timedelta(days=days_ahead)
    end = start + timedelta(days=1)
    choice = PlanChoice(_leg_on(T["T-BJS-SHA-1025-B"], start), _leg_on(T["T-SHA-BJS-1026-B"], end),
                        H["H-SHA-CONTRACT-01"])
    trip = slots(start=start.isoformat(), end=end.isoformat(), budget=10**8)
    result = evaluate_trip(trip, rules, "employee", TODAY, choice)
    assert [v.subject for v in result.violations] == violating
    assert all(v.code == "ADVANCE_BOOKING_VIOLATION" and v.rule_id == "RULE-ADVANCE-001"
               and v.clause == "POL-5" for v in result.violations)


def test_advance_uses_local_departure_date(v12: RuleSet) -> None:
    # 2026-11-04T23:30Z is 07:30 on 11-05 in Beijing: the local date counts.
    early = T["T-BJS-SHA-1105-EARLY"]
    shifted = early.model_copy(update={"depart_at": early.depart_at - timedelta(days=13),
                                       "arrive_at": early.arrive_at - timedelta(days=13)})
    ret = _leg_on(T["T-SHA-BJS-1026-B"], date(2026, 10, 24))
    trip = slots(start="2026-10-23", end="2026-10-24", budget=10**8)
    result = evaluate_trip(trip, v12, "employee", TODAY, PlanChoice(shifted, ret, H["H-SHA-CONTRACT-01"]))
    assert codes(result.violations) == []  # local 10-23 is 3 days ahead; the UTC date 10-22 would not be


def test_rail_legs_are_not_subject_to_advance_booking(v12: RuleSet) -> None:
    out = T["T-BJS-SHA-1022-E"].model_copy(update={"mode": "rail", "cabin": "second_class"})
    choice = PlanChoice(out, T["T-SHA-BJS-1023-E"], H["H-SHA-CONTRACT-02"])
    result = evaluate_trip(slots(start="2026-10-22", end="2026-10-23"), v12, "employee", TODAY, choice)
    assert result.violations == []


@pytest.mark.parametrize("cabin", ["business", "first", "first_class"])
def test_cabin_class_violation(v12: RuleSet, cabin) -> None:
    out = T["T-BJS-SHA-1025-B"].model_copy(update={"cabin": cabin})
    result = evaluate_trip(slots(**S02_TRIP, budget=250000), v12, "employee", TODAY, _s02_choice(out=out))
    (violation,) = result.violations
    assert violation.code == "CABIN_CLASS_VIOLATION" and violation.rule_id == "RULE-CLASS-001"
    assert violation.actual_value == cabin and violation.clause == "POL-4"
    assert result.outcome == "PENDING_APPROVAL"  # 190000 total: the violation alone triggers approval


def test_auto_limit_boundary(v12: RuleSet) -> None:
    # S-02 legs and hotel give 190000; raise the hotel to land exactly on 300000 and one fen above.
    at_limit = _s02_choice(out=with_price(T["T-BJS-SHA-1025-B"], 65000 + 110000), hotel_price=40000)
    result = evaluate_trip(slots(**S02_TRIP, budget=10**8), v12, "employee", TODAY, at_limit)
    assert result.cost_breakdown.total_fen == 300000 and result.outcome == "AUTO_APPROVED"
    over = _s02_choice(out=with_price(T["T-BJS-SHA-1025-B"], 65000 + 110001), hotel_price=40000)
    result = evaluate_trip(slots(**S02_TRIP, budget=10**8), v12, "employee", TODAY, over)
    assert result.cost_breakdown.total_fen == 300001
    assert codes(result.approval_reasons) == ["OVER_AUTO_APPROVAL_LIMIT"]


def test_v10_auto_limit_is_lower(v10: RuleSet) -> None:
    choice = _s02_choice(out=with_price(T["T-BJS-SHA-1025-B"], 65000 + 10001))
    far = evaluate_trip(slots(**S02_TRIP, budget=10**8), v10, "employee", TODAY, choice)
    assert far.cost_breakdown.total_fen == 200001
    assert "OVER_AUTO_APPROVAL_LIMIT" in codes(far.approval_reasons)


def test_budget_boundary(v12: RuleSet) -> None:
    equal = evaluate_trip(slots(**S02_TRIP, budget=190000), v12, "employee", TODAY, _s02_choice())
    assert equal.approval_reasons == [] and equal.outcome == "AUTO_APPROVED"
    short = evaluate_trip(slots(**S02_TRIP, budget=189999), v12, "employee", TODAY, _s02_choice())
    (reason,) = short.approval_reasons
    assert reason.code == "BUDGET_EXCEEDED" and (reason.actual_value, reason.limit_value) == (190000, 189999)
    assert reason.basis is None and not reason.lower_bound


def test_known_subtotal_over_budget_is_a_lower_bound(v12: RuleSet) -> None:
    choice = PlanChoice(None, None, H["H-SHA-CONTRACT-02"])
    result = evaluate_trip(slots(**S02_TRIP, budget=59999), v12, "employee", TODAY, choice)
    assert codes(result.approval_reasons) == ["BUDGET_EXCEEDED", "PLAN_INCOMPLETE"]
    assert result.approval_reasons[0].lower_bound and result.approval_reasons[0].actual_value == 60000
    equal = evaluate_trip(slots(**S02_TRIP, budget=60000), v12, "employee", TODAY, choice)
    assert codes(equal.approval_reasons) == ["PLAN_INCOMPLETE"]


def test_one_missing_leg_makes_transport_unknown(v12: RuleSet) -> None:
    choice = PlanChoice(T["T-BJS-SHA-1025-B"], None, H["H-SHA-CONTRACT-02"])
    cost = compute_cost(slots(**S02_TRIP), v12, choice)
    assert cost.transport_fen is None and cost.outbound_fen == 65000 and cost.total_fen is None
    assert cost.missing_components == ["transport"]
    assert cost.known_subtotal_fen == 65000 + 40000 + 20000  # every priced part counts


def test_same_day_trip_needs_no_hotel(v12: RuleSet) -> None:
    out = T["T-BJS-SHA-1025-B"]
    ret = out.model_copy(update={"id": "T-SAME-DAY", "origin": "上海", "destination": "北京"})
    cost = compute_cost(slots(start="2026-10-25", end="2026-10-25"), v12, PlanChoice(out, ret, None))
    assert (cost.nights, cost.days, cost.hotel_fen, cost.total_fen) == (0, 1, 0, 140000)
    assert cost.missing_components == []


@pytest.mark.parametrize(("start", "end", "expected"), [
    ("2026-10-20", "2026-10-21", []),                       # start == today is allowed
    ("2026-10-19", "2026-10-21", ["START_DATE_IN_PAST"]),
    ("2026-10-25", "2026-10-25", []),                       # end == start: 1 day
    ("2026-10-25", "2026-10-24", ["END_BEFORE_START"]),
    ("2026-10-21", "2026-11-04", []),                       # 15 days
    ("2026-10-21", "2026-11-05", ["DURATION_EXCEEDED"]),    # 16 days
    ("2026-10-19", "2026-10-18", ["START_DATE_IN_PAST", "END_BEFORE_START"]),
])
def test_hard_block_boundaries(v12: RuleSet, start, end, expected) -> None:
    blocks = check_blocks(slots(start=start, end=end), v12, TODAY)
    assert codes(blocks) == expected
    for block in blocks:
        if block.code == "DURATION_EXCEEDED":
            assert (block.rule_id, block.clause, block.actual_value, block.limit_value) == \
                ("RULE-DURATION-001", "POL-7", 16, 15)


# ------------------------------------------------------- integrity checks


def test_candidates_must_belong_to_the_trip(v12: RuleSet) -> None:
    wrong_route = PlanChoice(T["T-BJS-CTU-1027-A"], T["T-SHA-BJS-1030-A"], H["H-SHA-CONTRACT-01"])
    with pytest.raises(PlanMismatchError):
        compute_cost(slots(), v12, wrong_route)
    wrong_day = PlanChoice(T["T-BJS-SHA-1025-B"], T["T-SHA-BJS-1030-A"], H["H-SHA-CONTRACT-01"])
    with pytest.raises(PlanMismatchError):
        compute_cost(slots(), v12, wrong_day)
    wrong_city = PlanChoice(T["T-BJS-SHA-1028-A"], T["T-SHA-BJS-1030-A"], H["H-CTU-BACKUP-01"])
    with pytest.raises(PlanMismatchError):
        compute_cost(slots(), v12, wrong_city)


def test_admin_cannot_be_an_applicant(v12: RuleSet) -> None:
    with pytest.raises(ValueError):
        evaluate_trip(slots(), v12, "admin", TODAY, None)


def test_provider_values_cannot_be_smuggled_in() -> None:
    raw = json.loads((FIXTURES / "transport.json").read_text(encoding="utf-8"))["candidates"][0]
    for bad in ({"price_fen": 1800.0}, {"price_fen": True}, {"price_fen": "180000"}, {"price_fen": -1},
                {"compliant": True}, {"source": "llm"}, {"mode": "bus"},
                {"depart_at": "2026-10-28T09:30:00"}, {"arrive_at": raw["depart_at"]}):
        with pytest.raises(ValidationError):
            TransportCandidate.model_validate({**raw, **bad})


def test_evaluation_is_deterministic_and_json_safe(v12: RuleSet) -> None:
    choice = PlanChoice(T["T-BJS-SHA-1028-A"], T["T-SHA-BJS-1030-A"], H["H-SHA-CONTRACT-01"])
    first = evaluate_trip(slots(), v12, "employee", TODAY, choice).model_dump(mode="json")
    second = evaluate_trip(slots(), v12, "employee", TODAY, choice).model_dump(mode="json")
    assert first == second
    text = json.dumps(first)
    assert "476000" in text and "." not in json.dumps(first["cost_breakdown"])  # integers only
