"""Demo providers backed by the fixtures in `tests/fixtures/providers/` (§7.1).

Fixtures are re-read and schema-validated on every call, exactly as a live
response would be: a corrupted fixture yields `ProviderInvalidResponse`, never a
partially trusted list. Faults (§6.6, `faults` table) are a snapshot taken when
the planning run starts, so one run sees one consistent fault configuration.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from travel_desk.domain.dates import local_date
from travel_desk.domain.models import HotelCandidate, TransportCandidate
from travel_desk.domain.policy import normalize_city
from travel_desk.providers.base import (
    FAULT_KEYS,
    ProviderInvalidResponse,
    Providers,
    ProviderTimeout,
    ProviderUnavailable,
    WeatherDay,
)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderInvalidResponse(f"{path.name}: unreadable fixture ({type(exc).__name__})") from exc


def _validated(model, items: Any, label: str) -> list:
    if not isinstance(items, list):
        raise ProviderInvalidResponse(f"{label}: expected a list")
    try:
        parsed = [model.model_validate(item) for item in items]
    except ValidationError as exc:
        raise ProviderInvalidResponse(f"{label}: {exc.error_count()} schema error(s)") from exc
    ids = [item.id for item in parsed]
    if len(ids) != len(set(ids)):
        raise ProviderInvalidResponse(f"{label}: duplicate candidate id")
    return parsed


class DemoTransportProvider:
    name = "demo_transport"

    def __init__(self, path: Path, faults: frozenset[str]):
        self._path = path
        self._faults = faults

    async def search(self, origin: str, destination: str, depart_date: date) -> list[TransportCandidate]:
        if "transport_timeout" in self._faults:
            raise ProviderTimeout("交通 Provider 超时（故障注入 transport_timeout）")
        data = _read(self._path)
        candidates = _validated(TransportCandidate, data.get("candidates"), "transport")
        route = (normalize_city(origin), normalize_city(destination))
        return [
            c for c in candidates
            if (normalize_city(c.origin), normalize_city(c.destination)) == route
            and local_date(c.depart_at) == depart_date
        ]


class DemoHotelProvider:
    name = "demo_hotel"

    def __init__(self, path: Path, faults: frozenset[str]):
        self._path = path
        self._faults = faults

    def _city(self, city: str, kind: str) -> list[HotelCandidate]:
        hotels = _validated(HotelCandidate, _read(self._path).get("hotels"), "hotels")
        wanted = normalize_city(city)
        return [h for h in hotels if h.kind == kind and normalize_city(h.city) == wanted]

    async def search_contract(self, city: str) -> list[HotelCandidate]:
        if "no_protocol_hotel" in self._faults:
            raise ProviderUnavailable("协议酒店库不可用（故障注入 no_protocol_hotel）")
        return self._city(city, "contract")

    async def search_backup(self, city: str) -> list[HotelCandidate]:
        return self._city(city, "backup")


class DemoWeatherProvider:
    name = "demo_weather"

    def __init__(self, path: Path, faults: frozenset[str]):
        self._path = path
        self._faults = faults

    async def forecast(self, city: str, dates: list[date]) -> list[WeatherDay]:
        if "weather_unavailable" in self._faults:
            raise ProviderUnavailable("天气 Provider 不可用（故障注入 weather_unavailable）")
        data = _read(self._path)
        if not isinstance(data, dict) or not isinstance(data.get("default"), dict):
            raise ProviderInvalidResponse("weather: missing default forecast")
        by_city = (data.get("cities") or {}).get(normalize_city(city), {})
        days = []
        for day in dates:
            entry = by_city.get(day.isoformat(), data["default"])
            try:
                days.append(WeatherDay(
                    date=day, summary=entry["condition"], temp_low_c=entry["low_c"],
                    temp_high_c=entry["high_c"], source="demo_fixture",
                ))
            except (KeyError, TypeError, ValidationError) as exc:
                raise ProviderInvalidResponse(f"weather: invalid entry for {day.isoformat()}") from exc
        return days


def build_demo_providers(providers_dir: Path, faults: frozenset[str] = frozenset()) -> Providers:
    unknown = faults - set(FAULT_KEYS)
    if unknown:
        raise ValueError(f"unknown fault(s): {sorted(unknown)}")
    return Providers(
        transport=DemoTransportProvider(providers_dir / "transport.json", faults),
        hotel=DemoHotelProvider(providers_dir / "hotels.json", faults),
        weather=DemoWeatherProvider(providers_dir / "weather.json", faults),
        mode="demo",
        faults=faults,
    )
