"""Code-owned candidate selection (REQ-013, HC-01).

Order of operations, per part of the plan:

1. Filter necessary conditions: route and departure date (done by the provider
   query), and for hotels every occupied night must be bookable (§7.1).
2. Rank by compliance first, then price, then preference; ties are broken by a
   stable secondary key so the result is reproducible (AC-023, IMP-07).
3. If nothing is compliant, take the least-bad trustworthy candidate and mark it
   as an exception; the rule core then reports the violation.

Legs are ranked independently: the total is the sum of the legs, so the cheapest
compliant combination is the combination of the cheapest compliant legs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from travel_desk.domain.dates import is_early_departure, local_date, to_local
from travel_desk.domain.models import HotelCandidate, TransportCandidate
from travel_desk.domain.policy import RuleSet
from travel_desk.domain.rules import PlanChoice
from travel_desk.planning.plan import FilteredOut


class ForgedCandidateError(ValueError):
    """A selected candidate is not, byte for byte, one the provider returned this run (§6.6)."""


# ------------------------------------------------------------------ transport


def transport_compliant(candidate: TransportCandidate, rules: RuleSet, today: date) -> bool:
    if candidate.cabin not in rules.allowed_cabins:
        return False
    requirement = rules.advance_requirement(candidate.mode)
    if requirement is None:
        return True
    days, _ = requirement
    return (local_date(candidate.depart_at) - today).days >= days


def transport_key(candidate: TransportCandidate, rules: RuleSet, today: date, avoid_early: bool):
    """(non-compliant, price, preference penalty, local departure time, id) ascending."""
    penalty = 1 if avoid_early and is_early_departure(candidate.depart_at) else 0
    return (
        not transport_compliant(candidate, rules, today),
        candidate.price_fen,
        penalty,
        to_local(candidate.depart_at),
        candidate.id,
    )


@dataclass(frozen=True)
class LegSelection:
    candidate: TransportCandidate | None
    exception: bool
    ranked: list[TransportCandidate]


def select_leg(candidates: list[TransportCandidate], rules: RuleSet, today: date,
               avoid_early: bool) -> LegSelection:
    ranked = sorted(candidates, key=lambda c: transport_key(c, rules, today, avoid_early))
    if not ranked:
        return LegSelection(None, False, [])
    best = ranked[0]
    return LegSelection(best, not transport_compliant(best, rules, today), ranked)


# ---------------------------------------------------------------------- hotel


@dataclass(frozen=True)
class HotelSelection:
    candidate: HotelCandidate | None
    exception: bool
    ranked: list[HotelCandidate]
    filtered_out: list[FilteredOut]


def hotel_key(hotel: HotelCandidate, cap_fen: int):
    """Compliant contract < compliant backup (D-15) < over cap by the smallest amount.

    Within a stage: price, then higher rating, then contract before backup, then id.
    """
    over = max(0, hotel.price_fen - cap_fen)
    if over == 0:
        stage = 0 if hotel.kind == "contract" else 1
    else:
        stage = 2
    return (stage, over, hotel.price_fen, -hotel.rating, hotel.kind != "contract", hotel.id)


def select_hotel(candidates: list[HotelCandidate], nights: list[date], cap_fen: int) -> HotelSelection:
    bookable: list[HotelCandidate] = []
    filtered: list[FilteredOut] = []
    for hotel in candidates:
        blocked = hotel.unavailable_nights(nights)
        if blocked:
            filtered.append(FilteredOut(id=hotel.id, reason="NOT_AVAILABLE_ON_NIGHTS", nights=blocked))
        else:
            bookable.append(hotel)
    ranked = sorted(bookable, key=lambda h: hotel_key(h, cap_fen))
    if not ranked:
        return HotelSelection(None, False, [], filtered)
    best = ranked[0]
    return HotelSelection(best, best.price_fen > cap_fen, ranked, filtered)


# ------------------------------------------------------------ forgery check


@dataclass(frozen=True)
class ProviderResponses:
    """Everything the providers returned in one planning run."""

    outbound: tuple[TransportCandidate, ...] = ()
    return_leg: tuple[TransportCandidate, ...] = ()
    hotels: tuple[HotelCandidate, ...] = ()


def verify_choice(choice: PlanChoice, responses: ProviderResponses) -> None:
    """Reject any selected candidate whose id is unknown or whose fields were altered.

    Comparing the whole value (not just the id) also rejects a real id carrying a
    changed price, which §6.6 forbids as well.
    """
    pools = (
        ("outbound", choice.outbound, responses.outbound),
        ("return", choice.return_leg, responses.return_leg),
        ("hotel", choice.hotel, responses.hotels),
    )
    for label, selected, pool in pools:
        if selected is None:
            continue
        match = next((item for item in pool if item.id == selected.id), None)
        if match is None:
            raise ForgedCandidateError(f"{label} {selected.id} is not in this run's provider response")
        if match != selected:
            raise ForgedCandidateError(f"{label} {selected.id} differs from the provider response")
