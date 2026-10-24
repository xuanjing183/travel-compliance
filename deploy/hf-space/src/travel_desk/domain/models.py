"""Shared value types (frozen in M1; providers, workflow and API all use these).

Provider payloads are validated against these models before anything else
sees them (§6.6). Amounts are strict integers in fen: floats and bools are
rejected rather than coerced (D-07).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from travel_desk.domain.dates import parse_iso_date

Source = Literal["demo_fixture", "backup_fixture", "live", "policy", "model"]
Role = Literal["employee", "manager", "admin"]
Fen = Annotated[StrictInt, Field(ge=0)]
NonBlank = Annotated[str, Field(min_length=1)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TransportCandidate(_Frozen):
    id: NonBlank
    origin: NonBlank
    destination: NonBlank
    mode: Literal["air", "rail"]
    carrier: str | None = None
    service_no: str | None = None
    depart_at: datetime
    arrive_at: datetime
    cabin: NonBlank
    price_fen: Fen
    source: Source

    @field_validator("depart_at", "arrive_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a UTC offset")
        return value

    @field_validator("arrive_at")
    @classmethod
    def _arrives_after_departure(cls, value: datetime, info) -> datetime:
        depart = info.data.get("depart_at")
        if depart is not None and value <= depart:
            raise ValueError("arrive_at must be after depart_at")
        return value


class HotelCandidate(_Frozen):
    """ContractHotel of §6.3, plus `kind` to tell the contract library from the backup catalogue."""

    id: NonBlank
    city: NonBlank
    name: NonBlank
    price_fen: Fen
    rating: float = Field(ge=0, le=5)
    distance_note: str
    valid_until: date
    available_date_ranges: list[tuple[date, date]] | None
    source: Source
    kind: Literal["contract", "backup"]

    @field_validator("valid_until", mode="before")
    @classmethod
    def _strict_date(cls, value: object) -> date:
        return parse_iso_date(value)

    @field_validator("available_date_ranges", mode="before")
    @classmethod
    def _strict_ranges(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("available_date_ranges must be a list or null")
        ranges = []
        for item in value:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("each range must be [first_night, last_night]")
            first, last = parse_iso_date(item[0]), parse_iso_date(item[1])
            if last < first:
                raise ValueError("range end is before range start")
            ranges.append((first, last))
        return ranges

    def unavailable_nights(self, nights: list[date]) -> list[date]:
        """Occupied nights that cannot be booked (empty list = the stay is bookable).

        A night is bookable when it lies inside one of the inclusive night ranges
        (null = every night) and the contract is still valid on that night.
        """
        if self.available_date_ranges is None:
            return [night for night in nights if night > self.valid_until]
        blocked = []
        for night in nights:
            inside = any(first <= night <= last for first, last in self.available_date_ranges)
            if not inside or night > self.valid_until:
                blocked.append(night)
        return blocked


class Preferences(_Frozen):
    """Closed preference set (docs/api.md §2.3). Unknown keys are rejected."""

    avoid_early_departure: StrictBool = False
    notes: Annotated[str, Field(max_length=200)] | None = None


class TripSlots(_Frozen):
    """A complete, format-valid set of slots. Semantic checks (blocks) happen later."""

    origin: NonBlank
    destination: NonBlank
    start_date: date
    end_date: date
    purpose: NonBlank
    declared_budget_fen: Annotated[StrictInt, Field(gt=0)]
    meeting_address: str | None = None
    preferences: Preferences | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _strict_date(cls, value: object) -> date:
        return parse_iso_date(value)
