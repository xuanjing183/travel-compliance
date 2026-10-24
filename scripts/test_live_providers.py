"""Exercise Live Providers with real external calls (AC-028 revalidation).

Credentials are read only from the process environment. No secret value is
printed or persisted by this script.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Add src to path when the script is run directly.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from travel_desk.providers.live import (  # noqa: E402
    HybridHotelProvider,
    LiveTransportProvider,
    LiveWeatherProvider,
)


def heading(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


async def test_weather() -> bool | None:
    """Test QWeather city lookup and 30-day forecast."""
    heading("Testing LiveWeatherProvider (QWeather 30-day forecast)")
    api_host = os.environ.get("TRAVEL_DESK_QWEATHER_API_HOST")
    api_key = os.environ.get("TRAVEL_DESK_QWEATHER_API_KEY")
    if not api_host or not api_key:
        print("SKIP: QWeather API Host or API Key is not configured")
        return None

    provider = LiveWeatherProvider(api_host, api_key)
    city = "洛阳"
    start = date.today() + timedelta(days=7)
    dates = [start + timedelta(days=offset) for offset in range(3)]
    try:
        forecast = await provider.forecast(city, dates)
    except Exception as exc:  # noqa: BLE001 - evidence script reports provider boundary errors
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return False

    print(f"PASS: {city}, {len(forecast)} days")
    for day in forecast:
        print(f"  {day.date}: {day.summary}, {day.temp_low_c}~{day.temp_high_c} C")
    return True


async def test_hotel() -> bool:
    """Test trusted local prices and optional unpriced Tavily references."""
    heading("Testing HybridHotelProvider (local prices + Tavily references)")
    provider = HybridHotelProvider(
        REPO_ROOT / "tests" / "fixtures" / "providers" / "hotels.json",
        os.environ.get("TRAVEL_DESK_TAVILY_API_KEY"),
    )

    contract = await provider.search_contract("上海")
    if not contract:
        print("FAIL: trusted local contract catalogue returned no Shanghai hotels")
        return False
    print(f"PASS: {len(contract)} trusted local contract hotels")
    for hotel in contract[:3]:
        print(f"  {hotel.name}: CNY {hotel.price_fen / 100:.0f}/night ({hotel.id})")

    # 洛阳 now has verified local backup candidates (city-classification, 2026-10-21):
    # they must be priced locals, and Tavily must not have been called for them.
    backup = await provider.search_backup("洛阳")
    if not backup:
        print("FAIL: 洛阳 trusted backup catalogue returned no candidates")
        return False
    if any(c.kind != "backup" or c.price_fen <= 0 for c in backup):
        print("FAIL: backup candidates must be priced local catalogue entries")
        return False
    print(f"PASS: {len(backup)} trusted local backup hotels for 洛阳")
    for hotel in backup[:3]:
        print(f"  {hotel.name}: CNY {hotel.price_fen / 100:.0f}/night ({hotel.id})")

    # A city outside the local catalogue triggers the Tavily reference branch:
    # search_backup still returns [] (never priced candidates) and only fills
    # structured unpriced references.
    references = await provider.search_backup("拉萨")
    if references:
        print("FAIL: Tavily references were converted into priced candidates")
        return False
    if os.environ.get("TRAVEL_DESK_TAVILY_API_KEY"):
        refs = provider.last_references
        if provider.last_reference_count == 0 or not refs:
            print("FAIL: Tavily was configured but produced no structured references")
            return False
        if any(not ref.get("title") or not ref.get("url") for ref in refs):
            print("FAIL: Tavily references must carry title and url")
            return False
        print(f"PASS: Tavily returned {provider.last_reference_count} unpriced references "
              f"(first: {refs[0]['title'][:40]})")
    else:
        if provider.last_reference_count or provider.last_references:
            print("FAIL: references produced without a Tavily key")
            return False
        print("SKIP: Tavily key not configured; trusted local prices remain available")
    return True


async def test_transport() -> bool:
    """Test Kiwi MCP flight search with CNY prices."""
    heading("Testing LiveTransportProvider (Kiwi MCP)")
    provider = LiveTransportProvider(
        os.environ.get("TRAVEL_DESK_KIWI_MCP_URL", "https://mcp.kiwi.com/")
    )
    depart_date = date.today() + timedelta(days=10)
    try:
        flights = await provider.search("北京", "上海", depart_date)
    except Exception as exc:  # noqa: BLE001 - evidence script reports provider boundary errors
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return False

    print(f"PASS: {len(flights)} CNY flight candidates for {depart_date}")
    for flight in flights[:5]:
        print(
            f"  {flight.service_no or 'N/A'}: {flight.depart_at:%H:%M} -> "
            f"{flight.arrive_at:%H:%M}, CNY {flight.price_fen / 100:.0f}"
        )
    return True


async def main() -> int:
    heading("AC-028 Live Provider real-call revalidation")
    results = {
        "weather": await test_weather(),
        "hotel": await test_hotel(),
        "transport": await test_transport(),
    }
    heading("Summary")
    for name, result in results.items():
        status = "PASS" if result is True else "FAIL" if result is False else "SKIP"
        print(f"{name:12s}: {status}")
    return 1 if any(result is False for result in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
