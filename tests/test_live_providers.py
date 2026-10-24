"""Unit tests for Live Providers (AUTH-02).

These tests use mocked HTTP responses to verify the parsing and error handling
logic of LiveWeatherProvider, HybridHotelProvider, and LiveTransportProvider
without making real external API calls.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from travel_desk.config import REPO_ROOT
from travel_desk.providers.base import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    TransportCandidate,
    WeatherDay,
)
from travel_desk.providers.live import (
    HybridHotelProvider,
    LiveTransportProvider,
    LiveWeatherProvider,
    _kiwi_cabin,
    build_live_providers,
)


class TestLiveWeatherProvider:
    """Tests for QWeather city lookup and 30-day forecast."""

    @pytest.fixture
    def provider(self):
        return LiveWeatherProvider("test-api.qweather.test", "test-key")

    @staticmethod
    def response(payload):
        response = MagicMock()
        response.json.return_value = payload
        response.raise_for_status = MagicMock()
        return response

    @staticmethod
    def error_response(status: int, payload):
        """HTTP-level denial as served by the new QWeather console."""
        import httpx

        response = MagicMock()
        response.json.return_value = payload
        response.status_code = status
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=response)
        )
        return response

    @pytest.mark.asyncio
    async def test_location_id_prefers_exact_high_rank_city(self, provider):
        lookup = self.response({
            "code": "200",
            "location": [
                {"name": "洛阳", "id": "town", "rank": "90"},
                {"name": "洛阳", "id": "101180901", "rank": "20"},
            ],
        })
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=lookup)
            assert await provider._resolve_location("洛阳") == "101180901"

    @pytest.mark.asyncio
    async def test_location_not_found(self, provider):
        lookup = self.response({"code": "200", "location": []})
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=lookup)
            with pytest.raises(ProviderUnavailable, match="not found"):
                await provider._resolve_location("不存在的城市")

    @pytest.mark.asyncio
    async def test_geoapi_denial_falls_back_to_builtin_coordinates(self, provider):
        """Security-restricted credentials deny GeoAPI but still serve forecasts."""
        denied = self.error_response(403, {"error": {"status": 403, "title": "Security Restriction"}})
        forecast = self.response({
            "code": "200",
            "daily": [{"fxDate": "2026-11-01", "textDay": "晴", "tempMin": "3", "tempMax": "15"}],
        })
        with patch("httpx.AsyncClient") as mock_client:
            mock_get = AsyncMock(side_effect=[denied, forecast])
            mock_client.return_value.__aenter__.return_value.get = mock_get
            result = await provider.forecast("洛阳", [date(2026, 11, 1)])
        assert [day.summary for day in result] == ["晴"]
        assert mock_get.call_args_list[1].kwargs["params"]["location"] == "112.45,34.62"

    @pytest.mark.asyncio
    async def test_geoapi_denial_unknown_city_still_degrades(self, provider):
        denied = self.error_response(403, {"error": {"status": 403}})
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=denied)
            with pytest.raises(ProviderUnavailable, match="city lookup"):
                await provider.forecast("呼伦贝尔", [date(2026, 11, 1)])

    @pytest.mark.asyncio
    async def test_forecast_success(self, provider):
        dates = [date(2026, 11, 1), date(2026, 11, 2)]
        lookup = self.response({
            "code": "200", "location": [{"name": "北京", "id": "101010100", "rank": "10"}]
        })
        forecast = self.response({
            "code": "200",
            "daily": [
                {"fxDate": "2026-11-01", "textDay": "晴", "tempMin": "1", "tempMax": "11"},
                {"fxDate": "2026-11-02", "textDay": "多云", "tempMin": "0", "tempMax": "12"},
            ],
        })
        with patch("httpx.AsyncClient") as mock_client:
            mock_get = AsyncMock(side_effect=[lookup, forecast])
            mock_client.return_value.__aenter__.return_value.get = mock_get
            result = await provider.forecast("北京", dates)
        assert [day.summary for day in result] == ["晴", "多云"]
        assert result[0] == WeatherDay(
            date=date(2026, 11, 1), summary="晴", temp_low_c=1, temp_high_c=11, source="live"
        )
        assert mock_get.call_args_list[1].args[0].endswith("/v7/weather/30d")
        assert mock_get.call_args_list[1].kwargs["headers"] == {"X-QW-Api-Key": "test-key"}

    @pytest.mark.asyncio
    async def test_forecast_missing_requested_date(self, provider):
        lookup = self.response({
            "code": "200", "location": [{"name": "北京", "id": "101010100", "rank": "10"}]
        })
        forecast = self.response({"code": "200", "daily": []})
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=[lookup, forecast]
            )
            with pytest.raises(ProviderUnavailable, match="outside the 30d forecast response"):
                await provider.forecast("北京", [date(2026, 12, 31)])

    @pytest.mark.asyncio
    async def test_forecast_skips_past_dates(self, provider):
        """Dates before today (China) cannot be forecast and must not fail the section."""
        lookup = self.response({
            "code": "200", "location": [{"name": "洛阳", "id": "101180901", "rank": "10"}]
        })
        forecast = self.response({
            "code": "200",
            "daily": [{"fxDate": "2026-11-02", "textDay": "晴", "tempMin": "8", "tempMax": "18"}],
        })
        with patch("httpx.AsyncClient") as mock_client:
            mock_get = AsyncMock(side_effect=[lookup, forecast])
            mock_client.return_value.__aenter__.return_value.get = mock_get
            result = await provider.forecast("洛阳", [date(2026, 10, 19), date(2026, 11, 2)])
        assert [day.date for day in result] == [date(2026, 11, 2)]
        # Only the lookup + one forecast request: no variant fallback was needed.
        assert [call.args[0].rsplit("/", 1)[-1] for call in mock_get.call_args_list[1:]] == ["30d"]

    @pytest.mark.asyncio
    async def test_forecast_all_past_dates_raise_without_http_call(self, provider):
        with patch("httpx.AsyncClient") as mock_client:
            mock_get = AsyncMock()
            mock_client.return_value.__aenter__.return_value.get = mock_get
            with pytest.raises(ProviderUnavailable, match="in the past"):
                await provider.forecast("洛阳", [date(2026, 1, 1)])
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_forecast_falls_back_to_shorter_variant_on_tier_code(self, provider):
        """A 402 (no paid tier for 30d) retries with the next shorter span."""
        lookup = self.response({
            "code": "200", "location": [{"name": "北京", "id": "101010100", "rank": "10"}]
        })
        denied = self.response({"code": "402"})
        forecast = self.response({
            "code": "200",
            "daily": [{"fxDate": "2026-11-01", "textDay": "多云", "tempMin": "2", "tempMax": "12"}],
        })
        with patch("httpx.AsyncClient") as mock_client:
            mock_get = AsyncMock(side_effect=[lookup, denied, forecast])
            mock_client.return_value.__aenter__.return_value.get = mock_get
            result = await provider.forecast("北京", [date(2026, 11, 1)])
        assert [day.summary for day in result] == ["多云"]
        paths = [call.args[0] for call in mock_get.call_args_list[1:]]
        assert [path.rsplit("/", 2)[-2:] for path in paths] == [["weather", "30d"], ["weather", "15d"]]

    @pytest.mark.asyncio
    async def test_forecast_timeout(self, provider):
        import httpx
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.TimeoutException("timeout")
            )
            with pytest.raises(ProviderTimeout, match="timeout"):
                await provider.forecast("北京", [date(2026, 11, 1)])

    @pytest.mark.asyncio
    async def test_forecast_empty_dates(self, provider):
        assert await provider.forecast("北京", []) == []


class TestHybridHotelProvider:
    """Priced candidates must come only from the trusted local catalogue."""

    catalog = REPO_ROOT / "tests" / "fixtures" / "providers" / "hotels.json"

    @pytest.mark.asyncio
    async def test_local_contract_candidates_do_not_call_tavily(self):
        provider = HybridHotelProvider(self.catalog, "test-key")
        with patch("httpx.AsyncClient") as client:
            hotels = await provider.search_contract("上海")
        assert hotels
        assert all(hotel.kind == "contract" for hotel in hotels)
        client.assert_not_called()

    @pytest.mark.asyncio
    async def test_tavily_results_are_unpriced_references_not_candidates(self):
        provider = HybridHotelProvider(self.catalog, "test-key")
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"results": [
            {"title": "拉萨酒店推荐", "url": "https://example.test/lhasa",
             "content": "市中心商务选择", "score": 0.9, "published_date": "2026-09-01"},
            {"title": "", "url": "https://example.test/no-title"},  # dropped: no title
        ]}
        with patch("httpx.AsyncClient") as client:
            client.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
            hotels = await provider.search_backup("拉萨")
        assert hotels == []
        assert provider.last_reference_count == 2
        # Structured references keep title/url/snippet for display, never become candidates.
        assert provider.last_references == [{
            "title": "拉萨酒店推荐", "url": "https://example.test/lhasa",
            "snippet": "市中心商务选择", "score": 0.9, "published_date": "2026-09-01",
        }]

    @pytest.mark.asyncio
    async def test_reference_count_resets_between_searches(self):
        provider = HybridHotelProvider(self.catalog, "test-key")
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"results": [{"title": "拉萨酒店"}]}
        with patch("httpx.AsyncClient") as client:
            client.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
            assert await provider.search_backup("拉萨") == []
        assert provider.last_reference_count == 1
        assert await provider.search_backup("成都")
        assert provider.last_reference_count == 0

    @pytest.mark.asyncio
    async def test_missing_tavily_key_does_not_disable_local_catalog(self):
        provider = HybridHotelProvider(self.catalog, None)
        assert await provider.search_contract("上海")
        assert await provider.search_backup("洛阳")  # local backup catalogue covers Luoyang
        assert await provider.search_backup("拉萨") == []


class TestLiveTransportProvider:
    """Tests for LiveTransportProvider (Kiwi MCP)."""

    @pytest.mark.parametrize(("raw", "expected"), [
        ("Economy", "economy"), ("M", "economy"), ("经济舱", "economy"),
        ("Business", "business"), ("C", "business"), ("First", "first"),
        ("PremiumEconomy", "premium_economy"),
    ])
    def test_cabin_labels_are_normalized_for_policy_rules(self, raw, expected):
        assert _kiwi_cabin(raw) == expected

    @pytest.fixture
    def provider(self):
        return LiveTransportProvider("https://mcp.kiwi.com/")

    @pytest.mark.asyncio
    async def test_call_mcp_success(self, provider):
        """Test successful MCP call."""
        sse_response = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"content":[]}}\n\n'
        mock_response = MagicMock()
        mock_response.text = sse_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            result = await provider._call_mcp("search-flight", {})
            assert "result" in result or result == {"content": []}

    @pytest.mark.asyncio
    async def test_call_mcp_timeout(self, provider):
        """Test MCP call timeout handling."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.TimeoutException("timeout")
            )
            with pytest.raises(ProviderTimeout, match="timeout"):
                await provider._call_mcp("search-flight", {})

    @pytest.mark.asyncio
    async def test_parse_flights_success(self, provider):
        """Test flight parsing from Kiwi MCP response."""
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "currency": "CNY",
                        "itineraries": [
                            {
                                "id": "flight-1",
                                "price": 652.0,
                                "outbound": {
                                    "departureTime": "2026-11-01T07:00:00",
                                    "arrivalTime": "2026-11-01T08:55:00",
                                    "cabinClass": "Economy",
                                    "segments": [
                                        {
                                            "carrier": "MU",
                                            "carrierName": "China Eastern Airlines",
                                            "flightNumber": "MU5100",
                                        }
                                    ],
                                },
                            }
                        ],
                    }),
                }
            ]
        }

        flights = provider._parse_flights(result, "北京", "上海", date(2026, 11, 1))

        assert len(flights) == 1
        assert isinstance(flights[0], TransportCandidate)
        assert flights[0].origin == "北京"
        assert flights[0].destination == "上海"
        assert flights[0].service_no == "MU5100"
        assert flights[0].carrier == "China Eastern Airlines"
        assert flights[0].price_fen == 65200  # 652.0 CNY = 65200 fen
        assert flights[0].cabin == "economy"
        assert flights[0].source == "live"

    @pytest.mark.asyncio
    async def test_parse_flights_wrong_date(self, provider):
        """Test that flights on wrong date are filtered out."""
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "currency": "CNY",
                        "itineraries": [
                            {
                                "id": "flight-1",
                                "price": 652.0,
                                "outbound": {
                                    "departureTime": "2026-11-02T07:00:00",  # Wrong date
                                    "arrivalTime": "2026-11-02T08:55:00",
                                    "cabinClass": "Economy",
                                    "segments": [],
                                },
                            }
                        ],
                    }),
                }
            ]
        }

        flights = provider._parse_flights(result, "北京", "上海", date(2026, 11, 1))
        assert len(flights) == 0  # Filtered out

    @pytest.mark.asyncio
    async def test_parse_flights_rejects_non_cny_prices(self, provider):
        result = {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "currency": "EUR",
                    "itineraries": [{"id": "flight-1", "price": 100, "outbound": {}}],
                }),
            }]
        }
        with pytest.raises(ProviderInvalidResponse, match="expected CNY"):
            provider._parse_flights(result, "北京", "上海", date(2026, 11, 1))

    @pytest.mark.asyncio
    async def test_parse_flights_invalid_json(self, provider):
        """Test handling of invalid JSON in MCP response."""
        result = {
            "content": [
                {
                    "type": "text",
                    "text": "invalid json {",
                }
            ]
        }

        flights = provider._parse_flights(result, "北京", "上海", date(2026, 11, 1))
        assert len(flights) == 0

    @pytest.mark.asyncio
    async def test_search_integration(self, provider):
        """Test full search flow with mocked MCP call."""
        mcp_response = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "currency": "CNY",
                        "itineraries": [
                            {
                                "id": "flight-1",
                                "price": 652.0,
                                "outbound": {
                                    "departureTime": "2026-11-01T07:00:00",
                                    "arrivalTime": "2026-11-01T08:55:00",
                                    "cabinClass": "Economy",
                                    "segments": [
                                        {
                                            "carrier": "MU",
                                            "carrierName": "China Eastern",
                                            "flightNumber": "MU5100",
                                        }
                                    ],
                                },
                            }
                        ],
                    }),
                }
            ]
        }

        sse_text = f'event: message\ndata: {json.dumps({"jsonrpc":"2.0","id":1,"result":mcp_response})}\n\n'
        mock_response = MagicMock()
        mock_response.text = sse_text
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            flights = await provider.search("北京", "上海", date(2026, 11, 1))
            assert len(flights) == 1
            assert flights[0].price_fen == 65200


class TestBuildLiveProviders:
    """Tests for build_live_providers factory function."""

    providers_dir = REPO_ROOT / "tests" / "fixtures" / "providers"

    def test_build_with_all_params(self):
        transport, hotel, weather = build_live_providers(
            tavily_api_key="test-key",
            kiwi_mcp_url="https://mcp.kiwi.com/",
            providers_dir=self.providers_dir,
            qweather_api_host="test-api.qweather.test",
            qweather_api_key="weather-key",
        )
        assert isinstance(transport, LiveTransportProvider)
        assert isinstance(hotel, HybridHotelProvider)
        assert isinstance(weather, LiveWeatherProvider)

    @pytest.mark.asyncio
    async def test_hotel_catalog_works_without_tavily_key(self):
        _, hotel, _ = build_live_providers(
            tavily_api_key=None,
            kiwi_mcp_url="https://mcp.kiwi.com/",
            providers_dir=self.providers_dir,
            qweather_api_host="test-api.qweather.test",
            qweather_api_key="weather-key",
        )
        candidates = await hotel.search_contract("上海")
        assert candidates
        assert all(candidate.kind == "contract" for candidate in candidates)
        assert hotel.name == "live_hotel_hybrid"

    @pytest.mark.asyncio
    async def test_missing_qweather_config_degrades_only_weather(self):
        _, hotel, weather = build_live_providers(
            tavily_api_key=None,
            kiwi_mcp_url="https://mcp.kiwi.com/",
            providers_dir=self.providers_dir,
            qweather_api_host=None,
            qweather_api_key=None,
        )
        assert await hotel.search_contract("上海")
        with pytest.raises(ProviderUnavailable, match="QWeather API Host not configured"):
            await weather.forecast("上海", [date(2026, 11, 1)])
