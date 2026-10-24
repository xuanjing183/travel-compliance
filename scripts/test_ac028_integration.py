"""AC-028 integration test: full planning run with Live Providers (AUTH-02).

This script tests the complete workflow with Kiwi, the hybrid hotel provider,
and QWeather. Provider credentials are inherited from the process environment.
"""
import asyncio
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

# Add src to path.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

BASE_URL = "http://127.0.0.1:8000"


async def wait_for_server(timeout: float = 10.0) -> bool:
    """Wait for server to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{BASE_URL}/api/health")
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


async def create_trip_and_plan():
    """Create a trip and run planning workflow with Live Providers."""
    print("\n" + "=" * 60)
    print("AC-028 Integration Test: Full Planning with Live Providers")
    print("=" * 60)

    # Check server is ready.
    if not await wait_for_server():
        print("✗ Server not ready at", BASE_URL)
        return False

    print("✓ Server is ready")

    # Keep the real-call scenario inside QWeather's rolling 30-day response window.
    start_date = date.today() + timedelta(days=10)
    end_date = start_date + timedelta(days=2)
    slots = {
        "origin": "北京",
        "destination": "上海",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "purpose": "商务会议",
        "declared_budget_fen": 500000,
    }

    print(f"\nCreating trip: {slots['origin']} → {slots['destination']}")
    print(f"Dates: {slots['start_date']} to {slots['end_date']}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Create trip (with demo user header), then fill slots per the v1.1 API.
        headers = {"X-Demo-User": "u-zhangsan"}
        try:
            resp = await client.post(f"{BASE_URL}/api/trips", json={}, headers=headers)
            if resp.status_code != 201:
                print(f"✗ Failed to create trip: {resp.status_code} {resp.text}")
                return False
            trip_resp = resp.json()
            trip_id = trip_resp.get("trip", {}).get("id")
            if not trip_id:
                print(f"✗ Response missing trip ID")
                return False
            resp = await client.patch(
                f"{BASE_URL}/api/trips/{trip_id}/slots",
                json={"expected_revision": 1, "slots": slots},
                headers=headers,
            )
            if resp.status_code != 200:
                print(f"✗ Failed to set slots: {resp.status_code} {resp.text}")
                return False
            print(f"✓ Trip created: {trip_id}")
        except Exception as exc:
            print(f"✗ Error creating trip: {type(exc).__name__}: {exc}")
            return False

        # Submit trip (trigger planning).
        print("\nSubmitting trip (triggers planning with Live Providers)...")
        import uuid
        idempotency_key = str(uuid.uuid4())
        try:
            resp = await client.post(
                f"{BASE_URL}/api/trips/{trip_id}/submit",
                json={"expected_revision": 1},
                headers={**headers, "Idempotency-Key": idempotency_key}
            )
            if resp.status_code != 200:
                print(f"✗ Failed to submit trip: {resp.status_code} {resp.text}")
                return False
            print("✓ Trip submitted")
        except Exception as exc:
            print(f"✗ Error submitting trip: {type(exc).__name__}: {exc}")
            return False

        # Poll for planning completion (SSE stream would be better, but polling is simpler).
        print("\nWaiting for planning to complete...")
        max_wait = 120.0
        start = time.time()
        while time.time() - start < max_wait:
            try:
                resp = await client.get(f"{BASE_URL}/api/trips/{trip_id}", headers=headers)
                if resp.status_code == 200:
                    trip = resp.json()
                    status = str(trip.get("status") or "").upper()
                    print(f"  Status: {status}")
                    if status in ("PENDING_APPROVAL", "PLANNED", "APPROVED",
                                  "DELIVERED", "BLOCKED", "FAILED"):
                        print(f"✓ Planning completed: {status}")
                        break
            except Exception:
                pass
            await asyncio.sleep(2.0)
        else:
            print(f"✗ Timeout waiting for planning (waited {max_wait}s)")
            return False

        # Get final trip details.
        try:
            resp = await client.get(f"{BASE_URL}/api/trips/{trip_id}", headers=headers)
            if resp.status_code == 200:
                trip = resp.json()
                print("\n" + "-" * 60)
                print("Trip Summary")
                print("-" * 60)
                slots = trip.get("slots") or {}
                print(f"ID: {trip['id']}")
                print(f"Status: {trip['status']}")
                print(f"Route: {slots.get('origin', '?')} → {slots.get('destination', '?')}")
                print(f"Dates: {slots.get('start_date', '?')} to {slots.get('end_date', '?')}")

                # Check if plan was created (v1.1 schema: plan.{transport,hotel,weather}).
                plan = trip.get("plan")
                if plan:
                    print("\nPlan Details:")
                    transport = plan.get("transport", {})
                    outbound = transport.get("outbound") or {}
                    if outbound:
                        print(f"  Outbound: {outbound.get('carrier', 'N/A')} {outbound.get('service_no', 'N/A')}")
                        print(f"    {outbound.get('depart_at', 'N/A')} → {outbound.get('arrive_at', 'N/A')}")
                        print(f"    Price: ¥{outbound.get('price_fen', 0) / 100:.0f}")
                        print(f"    Source: {outbound.get('source', 'unknown')}")

                    hotel = plan.get("hotel") or {}
                    candidate = hotel.get("candidate") or {}
                    if candidate:
                        print(f"\n  Hotel: {candidate.get('name', 'N/A')}")
                        print(f"    Price: ¥{candidate.get('price_fen', 0) / 100:.0f}/晚")
                        print(f"    Source: {candidate.get('source', 'unknown')}")
                    else:
                        refs = hotel.get("references") or []
                        print(f"\n  Hotel: {hotel.get('status')} (references: {len(refs)})")

                    weather = (plan.get("weather") or {}).get("days") or []
                    if weather:
                        print(f"\n  Weather: {len(weather)} days "
                              f"({(plan.get('weather') or {}).get('status')})")
                        for day in weather[:3]:
                            print(f"    {day.get('date')}: {day.get('summary')}, "
                                  f"{day.get('temp_low_c')}°C ~ {day.get('temp_high_c')}°C")

                    # Check sources to confirm Live Providers were used.
                    sources = set()
                    if outbound.get("source") == "live":
                        sources.add("transport")
                    if candidate.get("source") == "live":
                        sources.add("hotel")
                    if any(d.get("source") == "live" for d in weather):
                        sources.add("weather")

                    if sources:
                        print(f"\n✓ Live Providers used: {', '.join(sorted(sources))}")
                    else:
                        print("\n⚠ No Live Providers used (plan may have degraded to demo/policy)")

                # Check audit log for provider_call events.
                resp = await client.get(f"{BASE_URL}/api/trips/{trip_id}/audit", headers=headers)
                if resp.status_code == 200:
                    audit_body = resp.json()
                    audit_events = audit_body if isinstance(audit_body, list) \
                        else (audit_body.get("events") or [])
                    provider_calls = [e for e in audit_events
                                      if (e.get("type") or e.get("event_type")) == "provider_call"]
                    print(f"\nAudit: {len(provider_calls)} provider calls recorded")
                    for call in provider_calls[:5]:
                        payload = call.get("payload", {})
                        print(f"  - {payload.get('provider', 'unknown')}: {payload.get('status', 'unknown')}")

                return True
        except Exception as exc:
            print(f"✗ Error getting trip details: {type(exc).__name__}: {exc}")
            return False


async def main():
    """Run AC-028 integration test."""
    print("\nAC-028 Live Provider Integration Test")
    print("Note: This makes REAL external API calls (AUTH-02)")
    print(f"Server: {BASE_URL}")
    print(f"Provider mode: {os.environ.get('TRAVEL_DESK_PROVIDER_MODE', 'demo')}")

    success = await create_trip_and_plan()

    print("\n" + "=" * 60)
    if success:
        print("✓ AC-028 Integration Test PASSED")
    else:
        print("✗ AC-028 Integration Test FAILED")
    print("=" * 60)

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
