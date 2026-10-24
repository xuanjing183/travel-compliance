"""Live Provider implementations (AUTH-02).

These providers make real external API calls to Kiwi MCP (transport), Tavily (hotel
references), and QWeather (weather). They are only used when TRAVEL_DESK_PROVIDER_MODE=live.

All providers handle failures gracefully and raise ProviderUnavailable or
ProviderTimeout so the workflow can degrade rather than crash.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from travel_desk.domain.models import HotelCandidate
from travel_desk.domain.policy import normalize_city
from travel_desk.providers.base import (
    ProviderError,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    TransportCandidate,
    TransportProvider,
    HotelProvider,
    WeatherProvider,
    WeatherDay,
)
from travel_desk.providers.qweather_geo import city_coordinates

logger = logging.getLogger(__name__)

# QWeather forecast tiers: try longest span first, fall back on code 402.
_FORECAST_TIERS = ("30d", "15d", "7d")

# Cabin normalisation for Kiwi MCP responses.
_CABIN_MAP = {
    "economy": "economy", "m": "economy", "经济舱": "economy",
    "business": "business", "c": "business", "商务舱": "business",
    "first": "first", "f": "first", "头等舱": "first",
    "premium_economy": "premium_economy", "premium economy": "premium_economy",
    "w": "premium_economy", "超经": "premium_economy",
}


def _kiwi_cabin(raw: str) -> str:
    """Normalise a Kiwi cabin label to the policy vocabulary."""
    key = raw.strip().lower().replace("_", "").replace(" ", "")
    # Try with underscores/spaces removed first
    for label, normalised in _CABIN_MAP.items():
        if key == label.replace("_", "").replace(" ", ""):
            return normalised
    # Fallback: try original lowered
    lowered = raw.strip().lower()
    if lowered in _CABIN_MAP:
        return _CABIN_MAP[lowered]
    return lowered


class _QWeatherStub:
    """Weather stub when QWeather credentials are not configured."""

    name = "live_weather_qweather_stub"

    async def forecast(self, city: str, dates: list[date]) -> list[WeatherDay]:
        raise ProviderUnavailable("QWeather API Host not configured")


class LiveWeatherProvider:
    """QWeather city lookup and 30-day forecast."""

    name = "live_weather_qweather"

    def __init__(self, api_host: str, api_key: str):
        self._api_host = api_host.rstrip("/")
        self._api_key = api_key

    async def _resolve_location(self, city: str) -> str:
        """Resolve a city name to a QWeather location ID via GeoAPI."""
        url = f"https://{self._api_host}/geo/v2/city/lookup"
        params = {"location": city, "number": 10}
        headers = {"X-QW-Api-Key": self._api_key}
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(url, params=params, headers=headers)
            except httpx.TimeoutException:
                raise ProviderTimeout("QWeather GeoAPI timeout")
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                raise ProviderUnavailable(f"QWeather GeoAPI denied city lookup for {city!r}")
            data = resp.json()
        if data.get("code") != "200":
            raise ProviderUnavailable(f"QWeather GeoAPI error: code={data.get('code')}")
        locations = data.get("location", [])
        if not locations:
            raise ProviderUnavailable(f"QWeather city not found: {city!r}")
        # Prefer exact name match with highest rank (lowest rank number).
        candidates = [loc for loc in locations if loc.get("name") == city]
        if not candidates:
            candidates = locations
        best = min(candidates, key=lambda loc: int(loc.get("rank", "999")))
        return best["id"]

    async def forecast(self, city: str, dates: list[date]) -> list[WeatherDay]:
        if not dates:
            return []
        today = datetime.now(timezone.utc).astimezone().date()
        future_dates = [d for d in dates if d >= today]
        if not future_dates:
            raise ProviderUnavailable("all requested dates are in the past")
        # Resolve location; fall back to builtin coordinates on GeoAPI denial.
        try:
            location = await self._resolve_location(city)
        except ProviderUnavailable:
            coords = city_coordinates(city)
            if coords is not None:
                location = f"{coords[0]},{coords[1]}"
            else:
                raise ProviderUnavailable(
                    f"QWeather city lookup failed for {city!r} and no builtin coordinates")
        async with httpx.AsyncClient(timeout=10) as client:
            for tier in _FORECAST_TIERS:
                url = f"https://{self._api_host}/v7/weather/{tier}"
                params = {"location": location}
                headers = {"X-QW-Api-Key": self._api_key}
                try:
                    resp = await client.get(url, params=params, headers=headers)
                    resp.raise_for_status()
                except httpx.TimeoutException:
                    raise ProviderTimeout("QWeather forecast timeout")
                except httpx.HTTPStatusError:
                    raise ProviderUnavailable("QWeather forecast HTTP error")
                data = resp.json()
                code = data.get("code")
                if code == "402":
                    continue  # tier not available, try shorter span
                if code != "200":
                    continue
                daily = data.get("daily", [])
                by_date = {d["fxDate"]: d for d in daily}
                results = []
                for d in future_dates:
                    entry = by_date.get(d.isoformat())
                    if entry is None:
                        raise ProviderUnavailable(
                            f"date {d.isoformat()} outside the {tier} forecast response")
                    results.append(WeatherDay(
                        date=d,
                        summary=entry.get("textDay", ""),
                        temp_low_c=int(entry["tempMin"]),
                        temp_high_c=int(entry["tempMax"]),
                        source="live",
                    ))
                return results
        raise ProviderUnavailable("QWeather forecast unavailable across all tiers")


class HybridHotelProvider:
    """Trusted local hotel catalogue with optional Tavily references.

    Contract and backup candidates always come from the local fixture catalogue.
    Tavily is only used for unpriced references (title/url/snippet) for cities
    outside the local catalogue — never for priced candidates.
    """

    name = "live_hotel_hybrid"

    def __init__(self, catalog_path: Path, tavily_api_key: str | None):
        self._catalog_path = catalog_path
        self._tavily_key = tavily_api_key
        self.last_references: list[dict] = []
        self.last_reference_count: int = 0

    def _read_catalog(self) -> list[HotelCandidate]:
        data = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        return [HotelCandidate.model_validate(h) for h in data.get("hotels", [])]

    async def search_contract(self, city: str) -> list[HotelCandidate]:
        wanted = normalize_city(city)
        return [h for h in self._read_catalog()
                if h.kind == "contract" and normalize_city(h.city) == wanted]

    async def search_backup(self, city: str) -> list[HotelCandidate]:
        wanted = normalize_city(city)
        local = [h for h in self._read_catalog()
                 if h.kind == "backup" and normalize_city(h.city) == wanted]
        self.last_references = []
        self.last_reference_count = 0
        if local:
            return local
        # No local backup; call Tavily for unpriced references only.
        if self._tavily_key:
            self.last_references = await self._tavily_search(city)
            # last_reference_count is set inside _tavily_search (total results, not just stored)
        return []

    async def _tavily_search(self, city: str) -> list[dict]:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": self._tavily_key,
            "query": f"{city} 酒店推荐",
            "max_results": 5,
            "search_depth": "basic",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            except httpx.TimeoutException:
                raise ProviderTimeout("Tavily search timeout")
            except httpx.HTTPStatusError as exc:
                raise ProviderUnavailable(f"Tavily search HTTP {exc.response.status_code}")
            data = resp.json()
        results = data.get("results", [])
        self.last_reference_count = len(results)
        refs = []
        for r in results:
            title = r.get("title", "").strip()
            if not title:
                continue
            ref = {"title": title, "url": r.get("url", "")}
            if r.get("content"):
                ref["snippet"] = r["content"]
            if r.get("score") is not None:
                ref["score"] = r["score"]
            if r.get("published_date"):
                ref["published_date"] = r["published_date"]
            refs.append(ref)
        return refs


class LiveTransportProvider:
    """Kiwi MCP (JSON-RPC over SSE) flight search."""

    name = "live_transport"

    def __init__(self, kiwi_mcp_url: str = "https://mcp.kiwi.com/"):
        self._mcp_url = kiwi_mcp_url.rstrip("/") + "/"

    async def _call_mcp(self, method: str, params: dict) -> dict:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": method,
                "arguments": params,
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(self._mcp_url, json=body)
                resp.raise_for_status()
            except httpx.TimeoutException:
                raise ProviderTimeout("Kiwi MCP call timeout")
            except httpx.HTTPStatusError as exc:
                raise ProviderUnavailable(f"Kiwi MCP HTTP {exc.response.status_code}")
        # Parse SSE response: extract the last data line.
        text = resp.text
        result = None
        for line in text.splitlines():
            if line.startswith("data: "):
                result = json.loads(line[6:])
        if result is None:
            raise ProviderUnavailable("Kiwi MCP returned no SSE data")
        # Check for JSON-RPC error
        if "error" in result:
            err = result["error"]
            raise ProviderUnavailable(
                f"Kiwi MCP error {err.get('code')}: {err.get('message')}")
        return result.get("result", result)

    def _parse_flights(self, result: dict, origin: str, destination: str,
                       depart_date: date) -> list[TransportCandidate]:
        content = result.get("content", [])
        if not content:
            return []
        text = content[0].get("text", "")
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []
        if data.get("currency") != "CNY":
            raise ProviderInvalidResponse(
                f"Kiwi: expected CNY, got {data.get('currency')!r}")
        # Kiwi returns local times (China Standard Time, UTC+8)
        china_tz = timezone(timedelta(hours=8))
        flights = []
        for itin in data.get("itineraries", []):
            outbound = itin.get("outbound", {})
            dep_str = outbound.get("departureTime", "")
            arr_str = outbound.get("arrivalTime", "")
            if not dep_str or not arr_str:
                continue
            # Parse as local China time, then convert to UTC for storage
            dep_dt_local = datetime.fromisoformat(dep_str).replace(tzinfo=china_tz)
            arr_dt_local = datetime.fromisoformat(arr_str).replace(tzinfo=china_tz)
            # Filter by local departure date (not UTC)
            if dep_dt_local.date() != depart_date:
                continue
            # Convert to UTC for storage
            dep_dt = dep_dt_local.astimezone(timezone.utc)
            arr_dt = arr_dt_local.astimezone(timezone.utc)
            segments = outbound.get("segments", [])
            seg = segments[0] if segments else {}
            cabin_raw = outbound.get("cabinClass", "Economy")
            cabin = _kiwi_cabin(cabin_raw)
            price = itin.get("price", 0)
            price_fen = int(price * 100) if isinstance(price, float) else int(price) * 100
            flight_id = itin.get("id", f"KIWI-{dep_str}")
            flights.append(TransportCandidate(
                id=flight_id,
                origin=origin,
                destination=destination,
                mode="air",
                carrier=seg.get("carrierName") or seg.get("carrier"),
                service_no=seg.get("flightNumber"),
                depart_at=dep_dt,
                arrive_at=arr_dt,
                cabin=cabin,
                price_fen=price_fen,
                source="live",
            ))
        return flights

    async def search(self, origin: str, destination: str, depart_date: date) -> list[TransportCandidate]:
        # Kiwi MCP uses flyFrom/flyTo and dd/mm/yyyy date format
        date_str = depart_date.strftime("%d/%m/%Y")
        result = await self._call_mcp("search-flight", {
            "flyFrom": origin,
            "flyTo": destination,
            "departureDate": date_str,
            "currency": "CNY",
        })
        return self._parse_flights(result, origin, destination, depart_date)


def build_live_providers(
    *,
    tavily_api_key: str | None,
    kiwi_mcp_url: str,
    providers_dir: Path,
    qweather_api_host: str | None,
    qweather_api_key: str | None,
) -> tuple[TransportProvider, HotelProvider, WeatherProvider]:
    """Build Live Provider instances from configuration."""
    transport = LiveTransportProvider(kiwi_mcp_url)
    hotel = HybridHotelProvider(providers_dir / "hotels.json", tavily_api_key)
    if qweather_api_host and qweather_api_key:
        weather = LiveWeatherProvider(qweather_api_host, qweather_api_key)
    else:
        weather = _QWeatherStub()
    return transport, hotel, weather
