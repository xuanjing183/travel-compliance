"""Slot checks (§4.4, D-14), calendar helpers (D-10) and the state machine (§6.8)."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from travel_desk.domain.dates import is_early_departure, local_date, stay_nights, trip_days, trip_nights
from travel_desk.domain.models import HotelCandidate
from travel_desk.domain.slots import REQUIRED_FIELDS, evaluate_slots
from travel_desk.domain.states import (
    EDITABLE_STATES,
    TERMINAL_STATES,
    InvalidTransition,
    TripEvent,
    TripStatus,
    allowed_events,
    transition,
)

FULL = {
    "origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
    "purpose": "供应商大会", "declared_budget_fen": 400000,
}

# ----------------------------------------------------------------------- slots


def test_missing_fields_follow_contract_order() -> None:
    state = evaluate_slots({"destination": "杭州"})  # S-05: "下周去杭州"
    assert state.missing_fields == ["origin", "start_date", "end_date", "purpose", "declared_budget_fen"]
    assert state.values["destination"] == "杭州" and not state.complete
    assert evaluate_slots({}).missing_fields == list(REQUIRED_FIELDS)


@pytest.mark.parametrize(("field", "value", "code"), [
    ("origin", "   ", "BLANK"),
    ("purpose", "", "BLANK"),
    ("start_date", "下周", "INVALID_DATE"),
    ("start_date", "20261028", "INVALID_DATE"),
    ("end_date", "2026-02-30", "INVALID_DATE"),
    ("declared_budget_fen", 0, "NOT_POSITIVE"),
    ("declared_budget_fen", -1, "NOT_POSITIVE"),
    ("declared_budget_fen", 4000.5, "NOT_INTEGER"),
    ("declared_budget_fen", True, "NOT_INTEGER"),
    ("declared_budget_fen", "400000", "NOT_INTEGER"),
    ("preferences", {"avoid_early_departure": "yes"}, "INVALID_PREFERENCES"),
    ("preferences", {"window_seat": True}, "INVALID_PREFERENCES"),
    ("preferences", {"notes": "x" * 201}, "INVALID_PREFERENCES"),
])
def test_format_errors_stay_in_collecting(field, value, code) -> None:
    state = evaluate_slots({**FULL, field: value})
    assert [p["code"] for p in state.invalid_fields] == [code]
    assert state.invalid_fields[0]["field"] == field
    assert not state.complete
    with pytest.raises(ValueError):
        state.to_slots()


def test_semantic_problems_are_not_format_problems() -> None:
    # D-14: past dates and reversed ranges parse fine; they are blocked at submit, not re-asked.
    state = evaluate_slots({**FULL, "start_date": "2026-10-19", "end_date": "2026-10-18"})
    assert state.complete and state.invalid_fields == []


def test_complete_slots_are_normalised() -> None:
    state = evaluate_slots({**FULL, "destination": " 上海市 ", "purpose": " 供应商  大会 ",
                            "meeting_address": "  ", "preferences": {"avoid_early_departure": True}})
    slots = state.to_slots()
    assert slots.destination == "上海" and slots.purpose == "供应商 大会"
    assert slots.meeting_address is None
    assert slots.preferences.avoid_early_departure is True
    assert slots.start_date == date(2026, 10, 28)


def test_unknown_slot_is_rejected() -> None:
    state = evaluate_slots({**FULL, "hotel_id": "H-SHA-CONTRACT-01"})
    assert state.invalid_fields[0]["code"] == "UNKNOWN_FIELD"


# ----------------------------------------------------------------------- dates


def test_early_departure_cutoff_is_0800_local_exclusive() -> None:
    assert is_early_departure(datetime(2026, 11, 4, 23, 30, tzinfo=UTC))      # 07:30 local
    assert is_early_departure(datetime(2026, 11, 4, 23, 59, tzinfo=UTC))      # 07:59 local
    assert not is_early_departure(datetime(2026, 11, 5, 0, 0, tzinfo=UTC))    # 08:00 local
    assert not is_early_departure(datetime(2026, 11, 5, 6, 0, tzinfo=UTC))    # 14:00 local
    assert is_early_departure(datetime(2026, 11, 4, 16, 0, tzinfo=UTC))       # 00:00 local


def test_local_date_crosses_midnight() -> None:
    assert local_date(datetime(2026, 11, 4, 23, 30, tzinfo=UTC)) == date(2026, 11, 5)
    with pytest.raises(ValueError):
        local_date(datetime(2026, 11, 4, 23, 30))


def test_nights_and_days() -> None:
    start, end = date(2026, 10, 28), date(2026, 10, 30)
    assert (trip_nights(start, end), trip_days(start, end)) == (2, 3)
    assert stay_nights(start, end) == [date(2026, 10, 28), date(2026, 10, 29)]
    assert stay_nights(start, start) == []


def test_hotel_availability_is_by_occupied_night() -> None:
    hotel = HotelCandidate.model_validate({
        "id": "H-SHA-CONTRACT-02", "city": "上海", "name": "x", "price_fen": 40000, "rating": 4.5,
        "distance_note": "", "valid_until": "2026-12-31", "source": "demo_fixture", "kind": "contract",
        "available_date_ranges": [["2026-01-01", "2026-10-27"], ["2026-10-30", "2026-12-31"]],
    })
    nights = stay_nights
    assert hotel.unavailable_nights(nights(date(2026, 10, 28), date(2026, 10, 30))) == \
        [date(2026, 10, 28), date(2026, 10, 29)]                                      # S-01: filtered
    assert hotel.unavailable_nights(nights(date(2026, 10, 27), date(2026, 10, 28))) == []  # check-out 10-28
    assert hotel.unavailable_nights(nights(date(2026, 10, 30), date(2026, 10, 31))) == []
    assert hotel.unavailable_nights(nights(date(2026, 10, 29), date(2026, 10, 30))) == [date(2026, 10, 29)]
    assert hotel.unavailable_nights(nights(date(2026, 12, 31), date(2027, 1, 2))) == [date(2027, 1, 1)]
    open_hotel = hotel.model_copy(update={"available_date_ranges": None})
    assert open_hotel.unavailable_nights(nights(date(2026, 10, 28), date(2026, 10, 30))) == []
    assert open_hotel.unavailable_nights([date(2027, 1, 1)]) == [date(2027, 1, 1)]  # past valid_until


# ----------------------------------------------------------------------- states

S, E = TripStatus, TripEvent


def _run(events, status=S.COLLECTING, revision=1):
    trail = [(status, revision)]
    for event in events:
        step = transition(status, revision, event)
        status, revision = step.target, step.revision
        trail.append((status, revision))
    return trail


def test_scenario_paths() -> None:
    golden = _run([E.SUBMIT, E.VALIDATED, E.REQUIRE_APPROVAL, E.APPROVE, E.START_DELIVERY, E.DELIVERED])
    assert golden[-1] == (S.DELIVERED, 1)
    auto = _run([E.SUBMIT, E.VALIDATED, E.AUTO_APPROVE, E.START_DELIVERY, E.DELIVERED])
    assert [s for s, _ in auto] == [S.COLLECTING, S.VALIDATING, S.PLANNING, S.AUTO_APPROVED,
                                    S.DELIVERY_PENDING, S.DELIVERED]
    blocked = _run([E.SUBMIT, E.BLOCK, E.EDIT_AFTER_BLOCK, E.SUBMIT])
    assert blocked[-2] == (S.COLLECTING, 1) and blocked[-1] == (S.VALIDATING, 1)  # D-09
    rejected = _run([E.SUBMIT, E.VALIDATED, E.REQUIRE_APPROVAL, E.REJECT])
    assert rejected[-1] == (S.REJECTED, 1)
    changes = _run([E.SUBMIT, E.VALIDATED, E.REQUIRE_APPROVAL, E.REQUEST_CHANGES,
                    E.SUBMIT, E.VALIDATED, E.REQUIRE_APPROVAL, E.APPROVE])
    assert changes[4] == (S.COLLECTING, 2) and changes[-1] == (S.APPROVED, 2)       # S-08
    revalidate = _run([E.SUBMIT, E.VALIDATED, E.REQUIRE_APPROVAL, E.REVALIDATE, E.VALIDATED,
                       E.REQUIRE_APPROVAL])
    assert revalidate[4] == (S.VALIDATING, 2) and revalidate[-1] == (S.PENDING_APPROVAL, 2)  # S-12
    to_auto = _run([E.REVALIDATE, E.VALIDATED, E.AUTO_APPROVE], S.PENDING_APPROVAL, 1)
    assert to_auto[-1] == (S.AUTO_APPROVED, 2)                                       # IMP-04
    to_block = _run([E.REVALIDATE, E.BLOCK], S.PENDING_APPROVAL, 1)
    assert to_block[-1] == (S.POLICY_BLOCKED, 2)                                     # IMP-05
    no_approver = _run([E.SUBMIT, E.VALIDATED, E.NO_APPROVER])
    assert no_approver[-1] == (S.COLLECTING, 1)                                      # D-21 / IMP-06


@pytest.mark.parametrize(("status", "event"), [
    (S.COLLECTING, E.APPROVE),
    (S.POLICY_BLOCKED, E.SUBMIT),          # must edit first (back to COLLECTING)
    (S.PENDING_APPROVAL, E.SUBMIT),
    (S.APPROVED, E.APPROVE),               # already decided → API answers 409
    (S.AUTO_APPROVED, E.APPROVE),
    (S.DELIVERY_PENDING, E.APPROVE),       # can retry delivery, never re-approve
    (S.DELIVERY_PENDING, E.REVALIDATE),
    (S.PLANNING, E.REVALIDATE),
    (S.REJECTED, E.REQUEST_CHANGES),
    (S.DELIVERED, E.START_DELIVERY),
    (S.COLLECTING, E.EDIT_AFTER_BLOCK),
])
def test_illegal_transitions_are_refused(status, event) -> None:
    with pytest.raises(InvalidTransition):
        transition(status, 1, event)


def test_terminal_and_editable_states() -> None:
    for status in TERMINAL_STATES:
        assert allowed_events(status) == []
    assert EDITABLE_STATES == {S.COLLECTING, S.POLICY_BLOCKED}
    assert allowed_events(S.DELIVERY_PENDING) == [E.DELIVERED]
    with pytest.raises(ValueError):
        transition(S.COLLECTING, 0, E.SUBMIT)
