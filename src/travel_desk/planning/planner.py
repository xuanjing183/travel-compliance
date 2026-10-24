"""Build one revision's plan from provider responses (REQ-010～013, §6.6).

The planner only gathers and selects; it does not judge compliance outcomes
(that is `domain.rules.evaluate_trip`) and it must only be called after
`check_blocks` found nothing, so a blocked trip never reaches a provider (S-06).

Degradation never invents data: a failed or empty transport/hotel lookup leaves
that part `None` ("待人工补录"), which the rule core turns into
`total_fen = null` + `PLAN_INCOMPLETE` (D-18). Weather failure only drops the
weather section.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, TypeVar

from travel_desk.domain.dates import stay_nights, to_local
from travel_desk.domain.models import HotelCandidate, Preferences, TransportCandidate, TripSlots
from travel_desk.domain.money import format_yuan
from travel_desk.domain.policy import RuleSet
from travel_desk.domain.rules import PlanChoice, hotel_level
from travel_desk.planning.plan import (
    Considered,
    HotelPlan,
    ItineraryDay,
    ItineraryItem,
    Plan,
    ProviderCall,
    Ranking,
    TransportPlan,
    WeatherDayOut,
    WeatherPlan,
)
from travel_desk.planning.selector import (
    HotelSelection,
    LegSelection,
    ProviderResponses,
    select_hotel,
    select_leg,
    verify_choice,
)
from travel_desk.providers.base import ProviderError, Providers, WeatherDay

T = TypeVar("T")

_ERROR_TEXT = {"timeout": "超时", "unavailable": "不可用", "invalid": "返回无效数据"}


@dataclass(frozen=True)
class PlanningResult:
    plan: Plan
    choice: PlanChoice
    calls: list[ProviderCall]
    responses: ProviderResponses


async def _call(calls: list[ProviderCall], provider: str, implementation: str,
                request: dict[str, Any], awaitable: Awaitable[list[T]]) -> tuple[list[T] | None, str | None]:
    started = time.perf_counter()
    try:
        result = await awaitable
    except ProviderError as exc:
        calls.append(ProviderCall(
            provider=provider, implementation=implementation, request=request, status=exc.status,
            candidate_ids=[], error=str(exc), duration_ms=round((time.perf_counter() - started) * 1000),
        ))
        return None, exc.status
    ids = [getattr(item, "id", None) for item in result]
    calls.append(ProviderCall(
        provider=provider, implementation=implementation, request=request, status="ok",
        candidate_ids=[i for i in ids if i], error=None,
        duration_ms=round((time.perf_counter() - started) * 1000),
    ))
    return result, None


def _hhmm(candidate: TransportCandidate, field: str) -> str:
    return to_local(getattr(candidate, field)).strftime("%H:%M")


def _leg_title(leg: TransportCandidate) -> str:
    return f"{leg.service_no or leg.id} {leg.origin}→{leg.destination} 出发"


def _itinerary(slots: TripSlots, outbound: TransportCandidate | None, ret: TransportCandidate | None,
               hotel: HotelPlan, weather: WeatherPlan, preferences: Preferences | None) -> list[ItineraryDay]:
    weather_by_day = {day.date: day for day in weather.days}
    activity = slots.purpose + (f"（{slots.meeting_address}）" if slots.meeting_address else "")
    days: list[ItineraryDay] = []
    day = slots.start_date
    while day <= slots.end_date:
        items: list[ItineraryItem] = []
        if day == slots.start_date:
            if outbound is not None:
                items.append(ItineraryItem(time=_hhmm(outbound, "depart_at"), title=_leg_title(outbound),
                                           source=outbound.source))
                items.append(ItineraryItem(time=_hhmm(outbound, "arrive_at"),
                                           title=f"抵达{outbound.destination}", source=outbound.source))
            else:
                items.append(ItineraryItem(time=None, title="去程交通待人工补录", source=None))
            if preferences is not None and preferences.notes:
                items.append(ItineraryItem(time=None, title=f"偏好备注：{preferences.notes}", source=None))
            if hotel.status == "selected" and hotel.candidate is not None:
                items.append(ItineraryItem(time=None, title=f"入住 {hotel.candidate.name}",
                                           source=hotel.candidate.source))
            elif hotel.status == "manual_required":
                items.append(ItineraryItem(time=None, title="住宿待人工补录", source=None))
        items.append(ItineraryItem(time=None, title=activity, source=None))
        forecast = weather_by_day.get(day)
        if forecast is not None and "雨" in forecast.summary:
            items.append(ItineraryItem(time=None, title=f"天气{forecast.summary}，建议携带雨具",
                                       source=forecast.source))
        if day == slots.end_date:
            if ret is not None:
                items.append(ItineraryItem(time=_hhmm(ret, "depart_at"), title=_leg_title(ret),
                                           source=ret.source))
            else:
                items.append(ItineraryItem(time=None, title="返程交通待人工补录", source=None))
        days.append(ItineraryDay(date=day, items=items))
        day += timedelta(days=1)
    return days


async def build_plan(*, slots: TripSlots, rules: RuleSet, role: str, today: date, revision: int,
                     preferences: Preferences | None, providers: Providers) -> PlanningResult:
    calls: list[ProviderCall] = []
    avoid_early = bool(preferences and preferences.avoid_early_departure)
    level = hotel_level(role)
    tier = rules.tier_of(slots.destination)

    # ---- transport (REQ-010)
    legs: dict[str, LegSelection] = {}
    neutral: dict[str, LegSelection] = {}
    pools: dict[str, tuple[TransportCandidate, ...]] = {}
    notes: list[str] = []
    for label, src, dst, day in (("outbound", slots.origin, slots.destination, slots.start_date),
                                 ("return", slots.destination, slots.origin, slots.end_date)):
        request = {"origin": src, "destination": dst, "date": day.isoformat()}
        found, error = await _call(calls, "transport", providers.transport.name, request,
                                   providers.transport.search(src, dst, day))
        name = "去程" if label == "outbound" else "返程"
        if error is not None:
            notes.append(f"{name}：交通 Provider {_ERROR_TEXT[error]}，待人工补录")
            found = []
        elif not found:
            notes.append(f"{name}：无可用交通候选，待人工补录")
        pools[label] = tuple(found)
        legs[label] = select_leg(list(found), rules, today, avoid_early)
        neutral[label] = select_leg(list(found), rules, today, False)
    outbound, ret = legs["outbound"].candidate, legs["return"].candidate
    exception_legs = [label for label in ("outbound", "return") if legs[label].exception]
    if exception_legs:
        notes.append("无合规交通候选，已选价格最低的候选并标记例外")
    transport = TransportPlan(
        status="selected" if outbound is not None and ret is not None else "manual_required",
        outbound=outbound, return_leg=ret, exception_legs=exception_legs,
        note="；".join(notes) or None,
    )

    # ---- hotel (REQ-011, D-15)
    nights = stay_nights(slots.start_date, slots.end_date)
    hotels: list[HotelCandidate] = []
    selection = HotelSelection(None, False, [], [])
    if not nights:
        hotel = HotelPlan(status="not_needed", candidate=None, nights=0, exception=False,
                          note="当天往返，无需住宿")
    else:
        cap, _ = rules.hotel_cap(level, tier)
        city = slots.destination
        contract, contract_error = await _call(calls, "hotel_contract", providers.hotel.name, {"city": city},
                                               providers.hotel.search_contract(city))
        hotels.extend(contract or [])
        selection = select_hotel(hotels, nights, cap)
        if selection.candidate is None or selection.exception:
            backup, backup_error = await _call(calls, "hotel_backup", providers.hotel.name, {"city": city},
                                               providers.hotel.search_backup(city))
            hotels.extend(backup or [])
            selection = select_hotel(hotels, nights, cap)
        else:
            backup_error = None
        chosen = selection.candidate
        reasons = []
        if contract_error is not None:
            reasons.append(f"协议酒店库{_ERROR_TEXT[contract_error]}")
        if backup_error is not None:
            reasons.append(f"备用酒店目录{_ERROR_TEXT[backup_error]}")
        if chosen is None:
            reasons.append("无可订酒店候选，待人工补录")
        else:
            if chosen.kind == "backup":
                reasons.append("无合规可订协议酒店，选用已验证备用目录候选（backup_fixture）")
            if selection.exception:
                reasons.append(f"无住宿上限内的可订候选，选用超标最少的候选（上限 {format_yuan(cap)}/晚）")
        hotel = HotelPlan(
            status="selected" if chosen is not None else "manual_required",
            candidate=chosen, nights=len(nights), exception=selection.exception,
            note="；".join(reasons) or None,
        )

    # ---- weather (never blocks planning)
    span = (slots.end_date - slots.start_date).days
    dates = [slots.start_date + timedelta(days=i) for i in range(span + 1)]
    weather_request = {"city": slots.destination, "dates": [d.isoformat() for d in dates]}
    forecast, weather_error = await _call(calls, "weather", providers.weather.name, weather_request,
                                          providers.weather.forecast(slots.destination, dates))
    if weather_error is not None:
        weather = WeatherPlan(status="unavailable", days=[],
                              note=f"天气 Provider {_ERROR_TEXT[weather_error]}，已省略天气信息")
    else:
        weather = WeatherPlan(status="ok", days=[_weather_out(d) for d in forecast or []], note=None)

    # ---- choice and forgery check (AC-007)
    choice = PlanChoice(outbound=outbound, return_leg=ret, hotel=hotel.candidate)
    responses = ProviderResponses(outbound=pools["outbound"], return_leg=pools["return"],
                                  hotels=tuple(hotels))
    verify_choice(choice, responses)

    missing = []
    if outbound is None or ret is None:
        missing.append("transport")
    if hotel.status == "manual_required":
        missing.append("hotel")
    affected = any(legs[label].candidate != neutral[label].candidate for label in ("outbound", "return"))
    ranking = Ranking(
        preference_applied=avoid_early,
        preference_affected=affected,
        considered=Considered(
            outbound=[c.id for c in legs["outbound"].ranked],
            return_leg=[c.id for c in legs["return"].ranked],
            hotel=[h.id for h in selection.ranked],
        ),
        filtered_out=selection.filtered_out,
    )
    plan = Plan(
        revision=revision, policy_version=rules.version, incomplete=bool(missing), missing=missing,
        transport=transport, hotel=hotel, weather=weather,
        itinerary=_itinerary(slots, outbound, ret, hotel, weather, preferences),
        ranking=ranking,
    )
    return PlanningResult(plan=plan, choice=choice, calls=calls, responses=responses)


def _weather_out(day: WeatherDay) -> WeatherDayOut:
    return WeatherDayOut(date=day.date, summary=day.summary, temp_low_c=day.temp_low_c,
                         temp_high_c=day.temp_high_c, source=day.source)
