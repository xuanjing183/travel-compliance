"""AC-007 / AC-013 / AC-023: Demo providers, code-owned selection and degradation.

Scenario inputs and expected numbers are the contract's (§7.1, §7.2). Each
scenario runs the real path: seeded rules → Demo providers (fixtures validated
on read) → selector → `evaluate_trip`.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from travel_desk.config import REPO_ROOT, Settings
from travel_desk.db.database import initialize_database
from travel_desk.db.repositories import active_faults, list_faults, list_policy_rules, set_fault
from travel_desk.domain.models import HotelCandidate, Preferences, TransportCandidate, TripSlots
from travel_desk.domain.policy import RuleSet
from travel_desk.domain.rules import PlanChoice, evaluate_trip
from travel_desk.planning.planner import PlanningResult, build_plan
from travel_desk.planning.selector import (
    ForgedCandidateError,
    ProviderResponses,
    select_hotel,
    select_leg,
    verify_choice,
)
from travel_desk.providers.base import FixedClock, ProviderInvalidResponse, ProviderTimeout
from travel_desk.providers.demo import build_demo_providers

TODAY = date(2026, 10, 20)
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "providers"


@pytest.fixture
def seeded(settings: Settings) -> Settings:
    initialize_database(settings)
    return settings


@pytest.fixture
def v12(seeded: Settings) -> RuleSet:
    return RuleSet.from_rules("v1.2", list_policy_rules(seeded.db_path, "v1.2"))


def trip(destination: str, start: str, end: str, budget: int, purpose: str = "供应商大会",
         **extra: Any) -> TripSlots:
    return TripSlots(origin="北京", destination=destination, start_date=start, end_date=end,
                     purpose=purpose, declared_budget_fen=budget, **extra)


async def plan_for(slots: TripSlots, rules: RuleSet, *, faults: frozenset[str] = frozenset(),
                   preferences: Preferences | None = None, providers_dir: Path = FIXTURES,
                   role: str = "employee") -> PlanningResult:
    providers = build_demo_providers(providers_dir, faults)
    return await build_plan(slots=slots, rules=rules, role=role, today=TODAY, revision=1,
                            preferences=preferences, providers=providers)


def ids(result: PlanningResult) -> tuple[str | None, str | None, str | None]:
    c = result.choice
    return (c.outbound.id if c.outbound else None, c.return_leg.id if c.return_leg else None,
            c.hotel.id if c.hotel else None)


def assert_selected_ids_in_responses(result: PlanningResult) -> None:
    """AC-007: every selected id appears in this run's recorded provider responses."""
    returned = {cid for call in result.calls for cid in call.candidate_ids}
    for selected in ids(result):
        if selected is not None:
            assert selected in returned


def make_fixture_dir(tmp_path: Path, *, transport: list[dict] | None = None,
                     hotels: list[dict] | None = None) -> Path:
    target = tmp_path / "providers"
    shutil.copytree(FIXTURES, target)
    if transport is not None:
        (target / "transport.json").write_text(json.dumps({"candidates": transport}, ensure_ascii=False),
                                               encoding="utf-8")
    if hotels is not None:
        (target / "hotels.json").write_text(json.dumps({"hotels": hotels}, ensure_ascii=False),
                                            encoding="utf-8")
    return target


# ---------------------------------------------------------------- scenarios


async def test_s01_full_hotel_is_filtered_and_cheapest_bookable_contract_is_chosen(v12):
    slots = trip("上海", "2026-10-28", "2026-10-30", 400000)
    result = await plan_for(slots, v12)
    assert ids(result) == ("T-BJS-SHA-1028-A", "T-SHA-BJS-1030-A", "H-SHA-CONTRACT-01")
    plan = result.plan.to_json()
    assert plan["ranking"]["filtered_out"] == [
        {"id": "H-SHA-CONTRACT-02", "reason": "NOT_AVAILABLE_ON_NIGHTS", "nights": ["2026-10-28", "2026-10-29"]}
    ]
    assert plan["ranking"]["considered"]["hotel"] == ["H-SHA-CONTRACT-01"]
    assert plan["incomplete"] is False and plan["missing"] == []
    assert plan["transport"]["status"] == "selected" and plan["hotel"]["status"] == "selected"
    assert "return" in plan["transport"] and "return" in plan["ranking"]["considered"]
    evaluation = evaluate_trip(slots, v12, "employee", TODAY, result.choice)
    cost = evaluation.cost_breakdown
    assert (cost.transport_fen, cost.hotel_fen, cost.meal_fen, cost.total_fen) == (350000, 96000, 30000, 476000)
    assert evaluation.violations == []
    assert [r.code for r in evaluation.approval_reasons] == ["BUDGET_EXCEEDED", "OVER_AUTO_APPROVAL_LIMIT"]
    assert evaluation.outcome == "PENDING_APPROVAL"
    assert_selected_ids_in_responses(result)
    # A compliant contract hotel exists, so the backup catalogue is not queried.
    assert [c.provider for c in result.calls] == ["transport", "transport", "hotel_contract", "weather"]


async def test_s02_lowest_compliant_contract_hotel_auto_approves(v12):
    slots = trip("上海", "2026-10-25", "2026-10-26", 250000, purpose="客户拜访")
    result = await plan_for(slots, v12)
    assert ids(result) == ("T-BJS-SHA-1025-B", "T-SHA-BJS-1026-B", "H-SHA-CONTRACT-02")
    # Both contract hotels are bookable on 10-25; 40000 ranks before 48000.
    assert result.plan.ranking.considered.hotel == ["H-SHA-CONTRACT-02", "H-SHA-CONTRACT-01"]
    evaluation = evaluate_trip(slots, v12, "employee", TODAY, result.choice)
    assert evaluation.cost_breakdown.total_fen == 190000
    assert evaluation.outcome == "AUTO_APPROVED"
    assert_selected_ids_in_responses(result)


async def test_s03_backup_hotel_over_cap_is_the_marked_exception(v12):
    slots = trip("成都", "2026-10-27", "2026-10-29", 400000, purpose="培训")
    result = await plan_for(slots, v12)
    assert ids(result) == ("T-BJS-CTU-1027-A", "T-CTU-BJS-1029-A", "H-CTU-BACKUP-01")
    hotel = result.plan.hotel
    assert hotel.exception is True and hotel.candidate.source == "backup_fixture"
    assert "backup_fixture" in hotel.note
    assert "上限 400.00 元/晚" in hotel.note  # human-readable note; the fen value stays in the rule result
    evaluation = evaluate_trip(slots, v12, "employee", TODAY, result.choice)
    assert evaluation.cost_breakdown.total_fen == 274000
    [violation] = evaluation.violations
    assert (violation.code, violation.rule_id, violation.actual_value, violation.limit_value) == (
        "HOTEL_OVER_CAP", "RULE-HOTEL-001", 45000, 40000)
    assert [c.provider for c in result.calls] == ["transport", "transport", "hotel_contract", "hotel_backup",
                                                  "weather"]
    assert_selected_ids_in_responses(result)


async def test_s04_only_candidate_violates_advance_booking_and_is_marked(v12):
    slots = trip("上海", "2026-10-22", "2026-10-23", 300000, purpose="紧急会议")
    result = await plan_for(slots, v12)
    assert ids(result) == ("T-BJS-SHA-1022-E", "T-SHA-BJS-1023-E", "H-SHA-CONTRACT-02")
    # 10-22 is 2 days ahead (< 3): exception; 10-23 is exactly 3 days: compliant.
    assert result.plan.transport.exception_legs == ["outbound"]
    evaluation = evaluate_trip(slots, v12, "employee", TODAY, result.choice)
    assert evaluation.cost_breakdown.total_fen == 180000
    assert [(v.code, v.subject) for v in evaluation.violations] == [
        ("ADVANCE_BOOKING_VIOLATION", "T-BJS-SHA-1022-E")]
    assert evaluation.outcome == "PENDING_APPROVAL"


# ------------------------------------------------------------- AC-023 preference


async def test_ac023_avoid_early_departure_changes_the_choice(v12):
    slots = trip("上海", "2026-11-05", "2026-11-06", 250000, purpose="客户拜访")
    neutral = await plan_for(slots, v12)
    # Same price and cabin: the stable secondary key (local departure time, then id) picks the earlier one.
    assert ids(neutral) == ("T-BJS-SHA-1105-EARLY", "T-SHA-BJS-1106-P", "H-SHA-CONTRACT-02")
    assert neutral.plan.ranking.preference_applied is False
    assert neutral.plan.ranking.preference_affected is False

    avoid = await plan_for(slots, v12, preferences=Preferences(avoid_early_departure=True))
    assert ids(avoid) == ("T-BJS-SHA-1105-LATE", "T-SHA-BJS-1106-P", "H-SHA-CONTRACT-02")
    assert avoid.plan.ranking.preference_applied is True
    assert avoid.plan.ranking.preference_affected is True
    assert avoid.plan.ranking.considered.outbound == ["T-BJS-SHA-1105-LATE", "T-BJS-SHA-1105-EARLY"]
    evaluation = evaluate_trip(slots, v12, "employee", TODAY, avoid.choice)
    assert evaluation.cost_breakdown.total_fen == 190000
    assert evaluation.outcome == "AUTO_APPROVED"

    cleared = await plan_for(slots, v12, preferences=Preferences(avoid_early_departure=False))
    assert ids(cleared) == ids(neutral)
    assert cleared.plan.ranking.preference_applied is False


# ------------------------------------------------------------- AC-013 faults


async def test_s09_transport_timeout_leaves_transport_for_manual_entry(v12):
    slots = trip("上海", "2026-10-25", "2026-10-26", 250000, purpose="客户拜访")
    result = await plan_for(slots, v12, faults=frozenset({"transport_timeout"}))
    plan = result.plan
    assert plan.incomplete is True and plan.missing == ["transport"]
    assert plan.transport.status == "manual_required"
    assert plan.transport.outbound is None and plan.transport.return_leg is None
    assert "超时" in plan.transport.note
    assert plan.ranking.considered.outbound == [] and plan.ranking.considered.return_leg == []
    assert plan.hotel.candidate.id == "H-SHA-CONTRACT-02" and plan.weather.status == "ok"
    assert [(c.provider, c.status) for c in result.calls][:2] == [("transport", "timeout"), ("transport", "timeout")]
    assert all(c.candidate_ids == [] for c in result.calls if c.provider == "transport")
    evaluation = evaluate_trip(slots, v12, "employee", TODAY, result.choice)
    cost = evaluation.cost_breakdown
    assert (cost.total_fen, cost.known_subtotal_fen, cost.transport_fen) == (None, 60000, None)
    assert "PLAN_INCOMPLETE" in [r.code for r in evaluation.approval_reasons]
    assert evaluation.outcome == "PENDING_APPROVAL"


async def test_ac013_no_protocol_hotel_without_backup_requires_manual_hotel(v12):
    slots = trip("上海", "2026-10-25", "2026-10-26", 250000, purpose="客户拜访")
    result = await plan_for(slots, v12, faults=frozenset({"no_protocol_hotel"}))
    plan = result.plan
    assert plan.hotel.status == "manual_required" and plan.hotel.candidate is None
    assert plan.missing == ["hotel"] and plan.incomplete is True
    assert "协议酒店库不可用" in plan.hotel.note
    assert ids(result)[:2] == ("T-BJS-SHA-1025-B", "T-SHA-BJS-1026-B")
    assert plan.weather.status == "ok"
    statuses = {c.provider: c.status for c in result.calls}
    assert statuses == {"transport": "ok", "hotel_contract": "unavailable", "hotel_backup": "ok", "weather": "ok"}
    evaluation = evaluate_trip(slots, v12, "employee", TODAY, result.choice)
    cost = evaluation.cost_breakdown
    assert (cost.total_fen, cost.known_subtotal_fen, cost.hotel_fen) == (None, 150000, None)
    assert "PLAN_INCOMPLETE" in [r.code for r in evaluation.approval_reasons]
    assert evaluation.outcome == "PENDING_APPROVAL"


async def test_no_protocol_hotel_falls_back_to_backup_catalogue(v12):
    slots = trip("成都", "2026-10-27", "2026-10-29", 400000, purpose="培训")
    result = await plan_for(slots, v12, faults=frozenset({"no_protocol_hotel"}))
    assert result.choice.hotel.id == "H-CTU-BACKUP-01"
    assert result.choice.hotel.source == "backup_fixture"


async def test_weather_unavailable_is_omitted_and_noted(v12):
    slots = trip("上海", "2026-10-28", "2026-10-30", 400000)
    result = await plan_for(slots, v12, faults=frozenset({"weather_unavailable"}))
    weather = result.plan.weather
    assert weather.status == "unavailable" and weather.days == [] and "已省略" in weather.note
    assert result.plan.incomplete is False
    assert ids(result) == ("T-BJS-SHA-1028-A", "T-SHA-BJS-1030-A", "H-SHA-CONTRACT-01")
    # Without weather data there is no weather tip in the itinerary.
    titles = [item.title for day in result.plan.itinerary for item in day.items]
    assert not any("雨" in title for title in titles)


async def test_all_faults_together_never_invent_data(v12):
    slots = trip("上海", "2026-10-28", "2026-10-30", 400000)
    result = await plan_for(slots, v12, faults=frozenset({"transport_timeout", "no_protocol_hotel",
                                                          "weather_unavailable"}))
    assert ids(result) == (None, None, None)
    assert result.plan.missing == ["transport", "hotel"]
    evaluation = evaluate_trip(slots, v12, "employee", TODAY, result.choice)
    assert evaluation.cost_breakdown.total_fen is None
    assert evaluation.cost_breakdown.known_subtotal_fen == 30000  # meals only
    assert evaluation.outcome == "PENDING_APPROVAL"


def test_fault_flags_round_trip_through_the_database(seeded):
    assert active_faults(seeded.db_path) == frozenset()
    set_fault(seeded.db_path, "transport_timeout", True, "2026-10-20T00:00:00+00:00")
    assert active_faults(seeded.db_path) == frozenset({"transport_timeout"})
    assert list_faults(seeded.db_path) == {"no_protocol_hotel": False, "transport_timeout": True,
                                           "weather_unavailable": False}
    with pytest.raises(KeyError):
        set_fault(seeded.db_path, "made_up_fault", True, "2026-10-20T00:00:00+00:00")
    with pytest.raises(ValueError):
        build_demo_providers(FIXTURES, frozenset({"made_up_fault"}))


# --------------------------------------------------- schema validation (§6.6)


async def test_invalid_fixture_degrades_instead_of_trusting_partial_data(v12, tmp_path):
    raw = json.loads((FIXTURES / "transport.json").read_text(encoding="utf-8"))["candidates"]
    raw[0] = {**raw[0], "price_fen": 1800.0}  # float money is never accepted (D-07)
    providers_dir = make_fixture_dir(tmp_path, transport=raw)
    with pytest.raises(ProviderInvalidResponse):
        await build_demo_providers(providers_dir).transport.search("北京", "上海", date(2026, 10, 25))
    slots = trip("上海", "2026-10-25", "2026-10-26", 250000, purpose="客户拜访")
    result = await plan_for(slots, v12, providers_dir=providers_dir)
    assert result.plan.transport.status == "manual_required"
    assert "返回无效数据" in result.plan.transport.note
    assert {c.status for c in result.calls if c.provider == "transport"} == {"invalid"}


async def test_unknown_field_and_duplicate_ids_are_rejected(tmp_path):
    raw = json.loads((FIXTURES / "hotels.json").read_text(encoding="utf-8"))["hotels"]
    with_extra = make_fixture_dir(tmp_path / "a", hotels=[{**raw[0], "discount": 0.9}])
    with pytest.raises(ProviderInvalidResponse):
        await build_demo_providers(with_extra).hotel.search_contract("上海")
    duplicated = make_fixture_dir(tmp_path / "b", hotels=[raw[0], raw[0]])
    with pytest.raises(ProviderInvalidResponse):
        await build_demo_providers(duplicated).hotel.search_contract("上海")


async def test_transport_search_filters_route_and_local_date():
    providers = build_demo_providers(FIXTURES)
    found = await providers.transport.search("北京市", " 上海 ", date(2026, 11, 5))
    # EARLY departs 2026-11-04T23:30Z, which is 11-05 07:30 local.
    assert sorted(c.id for c in found) == ["T-BJS-SHA-1105-EARLY", "T-BJS-SHA-1105-LATE"]
    assert await providers.transport.search("上海", "北京", date(2026, 11, 5)) == []
    with pytest.raises(ProviderTimeout):
        await build_demo_providers(FIXTURES, frozenset({"transport_timeout"})).transport.search(
            "北京", "上海", date(2026, 11, 5))


def test_fixed_clock_is_demo_today(seeded):
    assert FixedClock(seeded.demo_today).today() == date(2026, 10, 20)


# ------------------------------------------------------ AC-007 forged ids


async def test_forged_candidate_id_is_rejected(v12):
    slots = trip("上海", "2026-10-25", "2026-10-26", 250000, purpose="客户拜访")
    result = await plan_for(slots, v12)
    forged = result.choice.outbound.model_copy(update={"id": "T-BJS-SHA-1025-FAKE"})
    with pytest.raises(ForgedCandidateError, match="not in this run"):
        verify_choice(PlanChoice(forged, result.choice.return_leg, result.choice.hotel), result.responses)


async def test_real_id_with_altered_price_is_rejected(v12):
    slots = trip("上海", "2026-10-25", "2026-10-26", 250000, purpose="客户拜访")
    result = await plan_for(slots, v12)
    cheaper = result.choice.hotel.model_copy(update={"price_fen": 1})
    with pytest.raises(ForgedCandidateError, match="differs"):
        verify_choice(PlanChoice(result.choice.outbound, result.choice.return_leg, cheaper), result.responses)


async def test_candidate_from_another_run_is_rejected(v12):
    s01 = await plan_for(trip("上海", "2026-10-28", "2026-10-30", 400000), v12)
    s02 = await plan_for(trip("上海", "2026-10-25", "2026-10-26", 250000), v12)
    with pytest.raises(ForgedCandidateError):
        verify_choice(s01.choice, s02.responses)
    verify_choice(s01.choice, s01.responses)  # its own responses are fine


def test_empty_responses_reject_any_selection():
    hotel = HotelCandidate.model_validate(
        json.loads((FIXTURES / "hotels.json").read_text(encoding="utf-8"))["hotels"][0])
    with pytest.raises(ForgedCandidateError):
        verify_choice(PlanChoice(None, None, hotel), ProviderResponses())


# ---------------------------------------------------- selector ordering (REQ-013)


def leg(cid: str, *, price: int, hour_local: int = 10, minute: int = 0, cabin: str = "economy",
        mode: str = "air", day: date = date(2026, 11, 5)) -> TransportCandidate:
    local = datetime(day.year, day.month, day.day, hour_local, minute, tzinfo=timezone(timedelta(hours=8)))
    return TransportCandidate(id=cid, origin="北京", destination="上海", mode=mode, depart_at=local,
                              arrive_at=local + timedelta(hours=2), cabin=cabin, price_fen=price,
                              source="demo_fixture")


def hotel(hid: str, *, price: int, kind: str = "contract", rating: float = 4.5,
          ranges: list | None = None, valid_until: str = "2026-12-31") -> HotelCandidate:
    return HotelCandidate(id=hid, city="上海", name=hid, price_fen=price, rating=rating, distance_note="",
                          valid_until=valid_until, available_date_ranges=ranges,
                          source="demo_fixture" if kind == "contract" else "backup_fixture", kind=kind)


def test_compliance_ranks_before_price(v12):
    cheap_business = leg("T-1", price=30000, cabin="business")
    economy = leg("T-2", price=60000)
    chosen = select_leg([cheap_business, economy], v12, TODAY, False)
    assert chosen.candidate.id == "T-2" and chosen.exception is False
    assert [c.id for c in chosen.ranked] == ["T-2", "T-1"]


def test_price_ranks_before_preference(v12):
    early_cheap = leg("T-1", price=60000, hour_local=7)
    late = leg("T-2", price=60001, hour_local=14)
    assert select_leg([late, early_cheap], v12, TODAY, True).candidate.id == "T-1"


def test_preference_breaks_a_price_tie_and_08_00_is_not_early(v12):
    early = leg("T-A", price=60000, hour_local=7, minute=59)
    at_eight = leg("T-B", price=60000, hour_local=8)
    assert select_leg([at_eight, early], v12, TODAY, False).candidate.id == "T-A"
    assert select_leg([at_eight, early], v12, TODAY, True).candidate.id == "T-B"


def test_stable_secondary_key_is_departure_time_then_id(v12):
    a, b = leg("T-B", price=60000, hour_local=9), leg("T-A", price=60000, hour_local=9)
    c = leg("T-0", price=60000, hour_local=10)
    assert [x.id for x in select_leg([c, a, b], v12, TODAY, False).ranked] == ["T-A", "T-B", "T-0"]


def test_rail_is_exempt_from_advance_booking(v12):
    tomorrow = date(2026, 10, 21)
    air = leg("T-AIR", price=50000, day=tomorrow)
    rail = leg("T-RAIL", price=55000, day=tomorrow, mode="rail", cabin="second_class")
    chosen = select_leg([air, rail], v12, TODAY, False)
    assert chosen.candidate.id == "T-RAIL" and chosen.exception is False


def test_no_compliant_transport_takes_cheapest_and_marks_exception(v12):
    first = leg("T-F", price=90000, cabin="first")
    business = leg("T-B", price=70000, cabin="business")
    chosen = select_leg([first, business], v12, TODAY, False)
    assert chosen.candidate.id == "T-B" and chosen.exception is True
    assert select_leg([], v12, TODAY, False).candidate is None


NIGHTS = [date(2026, 10, 28), date(2026, 10, 29)]


def test_contract_hotel_beats_cheaper_compliant_backup():
    chosen = select_hotel([hotel("H-B", price=30000, kind="backup"), hotel("H-C", price=48000)], NIGHTS, 50000)
    assert chosen.candidate.id == "H-C" and chosen.exception is False


def test_compliant_backup_beats_over_cap_contract():
    chosen = select_hotel([hotel("H-C", price=52000), hotel("H-B", price=49000, kind="backup")], NIGHTS, 50000)
    assert chosen.candidate.id == "H-B" and chosen.exception is False


def test_hotel_tie_breaks_by_rating_then_id():
    ranked = select_hotel([hotel("H-2", price=40000, rating=4.0), hotel("H-3", price=40000, rating=4.8),
                           hotel("H-1", price=40000, rating=4.0)], NIGHTS, 50000).ranked
    assert [h.id for h in ranked] == ["H-3", "H-1", "H-2"]


def test_no_compliant_hotel_takes_least_over_cap():
    chosen = select_hotel([hotel("H-C", price=56000), hotel("H-B", price=51000, kind="backup")], NIGHTS, 50000)
    assert chosen.candidate.id == "H-B" and chosen.exception is True
    equal = select_hotel([hotel("H-B", price=51000, kind="backup"), hotel("H-C", price=51000)], NIGHTS, 50000)
    assert equal.candidate.id == "H-C"  # same overage: contract before backup


def test_value_equal_to_cap_is_compliant():
    assert select_hotel([hotel("H-1", price=50000)], NIGHTS, 50000).exception is False
    assert select_hotel([hotel("H-1", price=50001)], NIGHTS, 50000).exception is True


def test_unbookable_hotels_are_filtered_before_ranking():
    full_one_night = hotel("H-FULL", price=30000, ranges=[["2026-10-01", "2026-10-28"]])
    expired = hotel("H-EXP", price=31000, valid_until="2026-10-28")
    checkout_day_full = hotel("H-OK", price=45000, ranges=[["2026-10-28", "2026-10-29"]])
    chosen = select_hotel([full_one_night, expired, checkout_day_full], NIGHTS, 50000)
    assert chosen.candidate.id == "H-OK"
    assert [(f.id, f.nights) for f in chosen.filtered_out] == [
        ("H-FULL", [date(2026, 10, 29)]), ("H-EXP", [date(2026, 10, 29)])]
    nothing = select_hotel([full_one_night], NIGHTS, 50000)
    assert nothing.candidate is None and nothing.exception is False


# ------------------------------------------------------- plan shape details


async def test_same_day_trip_needs_no_hotel(v12, tmp_path):
    day = date(2026, 10, 25)
    out = leg("T-OUT", price=65000, hour_local=9, day=day).model_dump(mode="json")
    back = {**leg("T-BACK", price=65000, hour_local=18, day=day).model_dump(mode="json"),
            "id": "T-BACK", "origin": "上海", "destination": "北京"}
    providers_dir = make_fixture_dir(tmp_path, transport=[out, back])
    slots = trip("上海", "2026-10-25", "2026-10-25", 250000, purpose="客户拜访")
    result = await plan_for(slots, v12, providers_dir=providers_dir)
    assert result.plan.hotel.status == "not_needed" and result.plan.incomplete is False
    assert "hotel_contract" not in [c.provider for c in result.calls]
    evaluation = evaluate_trip(slots, v12, "employee", TODAY, result.choice)
    assert evaluation.cost_breakdown.total_fen == 130000 + 10000
    assert [i.title for i in result.plan.itinerary[0].items] == [
        "T-OUT 北京→上海 出发", "抵达上海", "客户拜访", "T-BACK 上海→北京 出发"]


async def test_itinerary_uses_local_times_weather_and_notes(v12):
    slots = trip("上海", "2026-10-28", "2026-10-30", 400000, meeting_address="星衡国际会议中心")
    prefs = Preferences(notes="靠窗")
    result = await plan_for(slots, v12, preferences=prefs)
    days = result.plan.to_json()["itinerary"]
    assert [d["date"] for d in days] == ["2026-10-28", "2026-10-29", "2026-10-30"]
    first = days[0]["items"]
    assert first[0] == {"time": "09:30", "title": "XC1028 北京→上海 出发", "source": "demo_fixture"}
    assert first[1]["time"] == "12:45"
    assert {"time": None, "title": "偏好备注：靠窗", "source": None} in first
    assert {"time": None, "title": "入住 星衡协议酒店·外滩店", "source": "demo_fixture"} in first
    assert {"time": None, "title": "天气小雨，建议携带雨具", "source": "demo_fixture"} in first
    assert {"time": None, "title": "供应商大会（星衡国际会议中心）", "source": None} in days[1]["items"]
    assert days[2]["items"][-1] == {"time": "19:00", "title": "XC1030 上海→北京 出发", "source": "demo_fixture"}
    # The notes text is never a ranking input.
    assert result.plan.ranking.preference_applied is False
    assert ids(result) == ("T-BJS-SHA-1028-A", "T-SHA-BJS-1030-A", "H-SHA-CONTRACT-01")


async def test_manager_uses_manager_hotel_cap(v12, tmp_path):
    raw = json.loads((FIXTURES / "hotels.json").read_text(encoding="utf-8"))["hotels"]
    pricey = {**raw[0], "id": "H-SHA-CONTRACT-09", "price_fen": 60000, "available_date_ranges": None}
    providers_dir = make_fixture_dir(tmp_path, hotels=[pricey])
    slots = trip("上海", "2026-10-28", "2026-10-30", 400000)
    as_employee = await plan_for(slots, v12, providers_dir=providers_dir)
    as_manager = await plan_for(slots, v12, providers_dir=providers_dir, role="manager")
    assert as_employee.plan.hotel.exception is True   # 60000 > employee A cap 50000
    assert as_manager.plan.hotel.exception is False   # 60000 <= manager A cap 65000


async def test_planning_is_deterministic(v12):
    slots = trip("上海", "2026-10-28", "2026-10-30", 400000)
    first, second = await plan_for(slots, v12), await plan_for(slots, v12)
    assert first.plan.to_json() == second.plan.to_json()
    assert [c.model_dump(exclude={"duration_ms"}) for c in first.calls] == [
        c.model_dump(exclude={"duration_ms"}) for c in second.calls]
