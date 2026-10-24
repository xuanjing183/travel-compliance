"""Plan JSON (docs/api.md §2.4 "Plan") and provider-call records for the audit log."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from travel_desk.domain.models import HotelCandidate, Source, TransportCandidate

Leg = Literal["outbound", "return"]


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_by_name=True,
                              validate_by_alias=True, serialize_by_alias=True)


class TransportPlan(_Out):
    status: Literal["selected", "manual_required"]
    outbound: TransportCandidate | None
    return_leg: TransportCandidate | None = Field(alias="return")
    # Legs where no compliant candidate existed and the least-bad one was taken (REQ-013).
    exception_legs: list[Leg]
    note: str | None


class HotelPlan(_Out):
    status: Literal["selected", "manual_required", "not_needed"]
    candidate: HotelCandidate | None
    nights: int
    exception: bool
    note: str | None


class WeatherDayOut(_Out):
    date: date
    summary: str
    temp_low_c: int
    temp_high_c: int
    source: Source


class WeatherPlan(_Out):
    status: Literal["ok", "unavailable"]
    days: list[WeatherDayOut]
    note: str | None


class ItineraryItem(_Out):
    time: str | None  # local HH:MM; null = no fixed time (not invented)
    title: str
    source: Source | None  # null = taken from the applicant's own slots


class ItineraryDay(_Out):
    date: date
    items: list[ItineraryItem]


class FilteredOut(_Out):
    id: str
    reason: Literal["NOT_AVAILABLE_ON_NIGHTS"]
    nights: list[date]


class Considered(_Out):
    outbound: list[str]
    return_leg: list[str] = Field(alias="return")
    hotel: list[str]


class Ranking(_Out):
    preference_applied: bool
    preference_affected: bool
    considered: Considered
    filtered_out: list[FilteredOut]


class PlanSummary(_Out):
    """Model-written itinerary paragraph (§4.7). Text only: never amounts, candidates or results."""

    status: Literal["ok", "unavailable"]
    text: str | None
    source: Literal["model"] = "model"
    generator: str  # "fake" (deterministic test double) | "recorded:<file>" | real model name
    note: str | None


class Plan(_Out):
    revision: int
    policy_version: str
    incomplete: bool
    missing: list[Literal["transport", "hotel"]]
    transport: TransportPlan
    hotel: HotelPlan
    weather: WeatherPlan
    itinerary: list[ItineraryDay]
    ranking: Ranking
    summary: PlanSummary | None = None  # added by the workflow after planning (TASK-005)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ProviderCall(_Out):
    """One provider request, written as a `provider_call` audit event (REQ-021)."""

    provider: Literal["transport", "hotel_contract", "hotel_backup", "weather"]
    implementation: str
    request: dict[str, Any]
    status: Literal["ok", "timeout", "unavailable", "invalid"]
    candidate_ids: list[str]
    error: str | None
    duration_ms: int
