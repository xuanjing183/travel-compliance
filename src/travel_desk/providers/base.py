"""Provider interfaces (§6.6).

Every provider returns values already validated against the shared models, or
raises one of the errors below. Planning code never sees raw provider payloads,
so an invalid response can only degrade a plan part to "待人工补录"; it can never
turn into an invented candidate or price.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from travel_desk.domain.dates import LOCAL_TZ
from travel_desk.domain.models import HotelCandidate, Source, TransportCandidate

FAULT_KEYS = ("transport_timeout", "no_protocol_hotel", "weather_unavailable")


class ProviderError(Exception):
    status: Literal["timeout", "unavailable", "invalid"] = "unavailable"


class ProviderTimeout(ProviderError):
    status = "timeout"


class ProviderUnavailable(ProviderError):
    status = "unavailable"


class ProviderInvalidResponse(ProviderError):
    """The provider answered, but the payload failed schema validation."""

    status = "invalid"


class WeatherDay(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date
    summary: str
    temp_low_c: int
    temp_high_c: int
    source: Source


class TransportProvider(Protocol):
    name: str

    async def search(self, origin: str, destination: str, depart_date: date) -> list[TransportCandidate]:
        """Candidates on the route whose departure falls on `depart_date` (departure-city local date)."""
        ...


class HotelProvider(Protocol):
    name: str

    async def search_contract(self, city: str) -> list[HotelCandidate]:
        """Contract-library hotels in `city` (availability is filtered by the selector)."""
        ...

    async def search_backup(self, city: str) -> list[HotelCandidate]:
        """Verified backup catalogue for `city` (D-15)."""
        ...


class WeatherProvider(Protocol):
    name: str

    async def forecast(self, city: str, dates: list[date]) -> list[WeatherDay]: ...


class Clock(Protocol):
    def today(self) -> date: ...


class FixedClock:
    """Demo clock: always `DEMO_TODAY`, so every run is reproducible (§6.6)."""

    def __init__(self, today: date):
        self._today = today

    def today(self) -> date:
        return self._today


class SystemClock:
    """Live clock: the current date in local time (UTC+8)."""

    def today(self) -> date:
        return datetime.now(LOCAL_TZ).date()


class Providers:
    """The provider set used for one planning run."""

    def __init__(self, transport: TransportProvider, hotel: HotelProvider, weather: WeatherProvider,
                 *, mode: str, faults: frozenset[str] = frozenset()):
        self.transport = transport
        self.hotel = hotel
        self.weather = weather
        self.mode = mode
        self.faults = faults
