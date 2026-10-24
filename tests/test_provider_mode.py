"""Tests for Provider mode switch and configuration (AC-030).

These tests verify that the TRAVEL_DESK_PROVIDER_MODE environment variable
correctly controls whether Demo or Live Providers are used, and that the
network guard allowlist is properly configured for each mode.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from travel_desk.config import ConfigError, Settings
from travel_desk.db.database import db_session
from travel_desk.main import create_app
from travel_desk.network_guard import allowed_hosts_for
from travel_desk.services import audit


class TestProviderModeConfiguration:
    """Tests for provider mode configuration in Settings."""

    def test_default_provider_mode_is_demo(self, tmp_path: Path):
        """Test that default provider mode is 'demo'."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            assert settings.provider_mode == "demo"

    def test_provider_mode_live(self, tmp_path: Path):
        """Test setting provider mode to 'live'."""
        env = {"TRAVEL_DESK_PROVIDER_MODE": "live"}
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            assert settings.provider_mode == "live"

    def test_provider_mode_invalid(self, tmp_path: Path):
        """Test that invalid provider mode raises ConfigError."""
        env = {"TRAVEL_DESK_PROVIDER_MODE": "invalid"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigError, match="must be one of demo, live"):
                Settings.from_env()

    def test_provider_mode_case_insensitive(self, tmp_path: Path):
        """Test that provider mode is case-insensitive."""
        env = {"TRAVEL_DESK_PROVIDER_MODE": "LIVE"}
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            assert settings.provider_mode == "live"

    def test_tavily_api_key_from_env(self, tmp_path: Path):
        """Test that Tavily API key is read from environment."""
        env = {
            "TRAVEL_DESK_PROVIDER_MODE": "live",
            "TRAVEL_DESK_TAVILY_API_KEY": "test-key-123",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            assert settings.tavily_api_key == "test-key-123"

    def test_tavily_api_key_optional(self, tmp_path: Path):
        """Test that Tavily API key is optional."""
        env = {"TRAVEL_DESK_PROVIDER_MODE": "live"}
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            assert settings.tavily_api_key is None

    def test_kiwi_mcp_url_default(self, tmp_path: Path):
        """Test default Kiwi MCP URL."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            assert settings.kiwi_mcp_url == "https://mcp.kiwi.com/"

    def test_kiwi_mcp_url_custom(self, tmp_path: Path):
        """Test custom Kiwi MCP URL."""
        env = {"TRAVEL_DESK_KIWI_MCP_URL": "https://custom.kiwi.com/mcp"}
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            assert settings.kiwi_mcp_url == "https://custom.kiwi.com/mcp"

    def test_qweather_config_from_env(self, tmp_path: Path):
        env = {
            "TRAVEL_DESK_QWEATHER_API_HOST": "abc123.re.qweatherapi.com",
            "TRAVEL_DESK_QWEATHER_API_KEY": "weather-key",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            assert settings.qweather_api_host == "abc123.re.qweatherapi.com"
            assert settings.qweather_api_key == "weather-key"

    def test_qweather_config_optional(self, tmp_path: Path):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            assert settings.qweather_api_host is None
            assert settings.qweather_api_key is None


class TestNetworkGuardAllowlist:
    """Tests for network guard allowlist based on provider mode."""

    def test_demo_mode_no_external_hosts(self):
        """Test that demo mode has no external hosts in allowlist."""
        hosts = allowed_hosts_for(llm_mode="fake", dashscope_base_url="", provider_mode="demo")
        assert len(hosts) == 0

    def test_demo_mode_with_real_llm(self):
        """Test demo mode with real LLM (only LLM host allowed)."""
        hosts = allowed_hosts_for(
            llm_mode="real",
            dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            provider_mode="demo",
        )
        assert "dashscope.aliyuncs.com" in hosts
        assert "mcp.kiwi.com" not in hosts
        assert "api.tavily.com" not in hosts

    def test_live_mode_includes_provider_hosts(self):
        """Test that live mode includes the configured QWeather API Host."""
        hosts = allowed_hosts_for(
            llm_mode="fake", dashscope_base_url="", provider_mode="live",
            qweather_api_host="abc123.re.qweatherapi.com",
        )
        assert hosts == frozenset({
            "mcp.kiwi.com", "api.tavily.com", "abc123.re.qweatherapi.com"
        })

    def test_live_mode_with_real_llm(self):
        hosts = allowed_hosts_for(
            llm_mode="real",
            dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            provider_mode="live",
            qweather_api_host="https://abc123.re.qweatherapi.com/",
        )
        assert hosts == frozenset({
            "dashscope.aliyuncs.com", "mcp.kiwi.com", "api.tavily.com",
            "abc123.re.qweatherapi.com",
        })

    def test_live_mode_without_qweather_config_is_fail_closed(self):
        hosts = allowed_hosts_for(llm_mode="fake", dashscope_base_url="", provider_mode="live")
        assert hosts == frozenset({"mcp.kiwi.com", "api.tavily.com"})

    def test_demo_mode_explicit(self):
        """Test explicit demo mode parameter."""
        hosts = allowed_hosts_for(
            llm_mode="fake",
            dashscope_base_url="",
            provider_mode="demo",
        )
        assert len(hosts) == 0


class TestProviderModeIntegration:
    """Integration tests for provider mode with workflow."""

    def test_settings_fields_exist(self, tmp_path: Path):
        """Test that all required settings fields exist."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            assert hasattr(settings, "provider_mode")
            assert hasattr(settings, "tavily_api_key")
            assert hasattr(settings, "kiwi_mcp_url")
            assert hasattr(settings, "qweather_api_host")
            assert hasattr(settings, "qweather_api_key")

    def test_settings_frozen(self, tmp_path: Path):
        """Test that Settings is frozen (immutable)."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            with pytest.raises(Exception):  # FrozenInstanceError
                settings.provider_mode = "live"

    def test_live_mode_missing_credentials_warn_without_values(self, settings: Settings, caplog):
        """AUTH-02 startup check: missing live credentials log a named warning, never a value."""
        import logging

        from travel_desk.config import warn_live_credential_gaps

        live = replace(settings, provider_mode="live", tavily_api_key=None,
                       qweather_api_host=None, qweather_api_key=None)
        with caplog.at_level(logging.WARNING, logger="travel_desk.config"):
            warn_live_credential_gaps(live)
        messages = " | ".join(r.getMessage() for r in caplog.records)
        assert "TRAVEL_DESK_TAVILY_API_KEY" in messages
        assert "TRAVEL_DESK_QWEATHER_API_HOST" in messages
        # HC-07: only variable names, never any value fragment.
        assert "tavily-test-key" not in messages and "weather-test-key" not in messages

    def test_demo_mode_and_configured_live_do_not_warn(self, settings: Settings, caplog):
        """The startup check is silent for demo mode and for a fully configured live mode."""
        import logging

        from travel_desk.config import warn_live_credential_gaps

        with caplog.at_level(logging.WARNING, logger="travel_desk.config"):
            warn_live_credential_gaps(settings)  # demo mode
            configured = replace(settings, provider_mode="live", tavily_api_key="tavily-test-key",
                                 qweather_api_host="test-api.qweather.test",
                                 qweather_api_key="weather-test-key")
            warn_live_credential_gaps(configured)
        assert caplog.records == []

    def test_live_provider_health_reports_actual_implementations(self, settings: Settings):
        live = replace(
            settings,
            provider_mode="live",
            tavily_api_key="tavily-test-key",
            qweather_api_host="test-api.qweather.test",
            qweather_api_key="weather-test-key",
        )
        with TestClient(create_app(live)) as client:
            with db_session(live.db_path) as conn:
                audit.record(
                    conn,
                    trip_id=None,
                    revision=None,
                    actor="test",
                    event_type="provider_call",
                    payload={
                        "provider": "weather",
                        "implementation": "demo_weather",
                        "status": "unavailable",
                        "error": "stale demo error",
                    },
                    trace_id="test-live-health",
                )
            response = client.get(
                "/api/health/providers", headers={"X-Demo-User": "u-wangwu"}
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["provider_mode"] == "live"
        implementations = {item["name"]: item["implementation"] for item in payload["providers"]}
        assert implementations == {
            "transport": "live_transport",
            "hotel_contract": "live_hotel_hybrid",
            "hotel_backup": "live_hotel_hybrid",
            "weather": "live_weather_qweather",
        }
        assert {item["mode"] for item in payload["providers"]} == {"live"}
        weather = next(item for item in payload["providers"] if item["name"] == "weather")
        assert weather["last_error"] is None
        assert payload["provider_credentials"] == {
            "tavily_api_key_configured": True,
            "qweather_api_host_configured": True,
            "qweather_api_key_configured": True,
        }
        assert "tavily-test-key" not in response.text
        assert "weather-test-key" not in response.text

    def test_live_provider_health_marks_unconfigured_weather_unavailable(self, settings: Settings):
        live = replace(
            settings,
            provider_mode="live",
            qweather_api_host=None,
            qweather_api_key=None,
        )
        with TestClient(create_app(live)) as client:
            payload = client.get(
                "/api/health/providers", headers={"X-Demo-User": "u-wangwu"}
            ).json()
        weather = next(item for item in payload["providers"] if item["name"] == "weather")
        assert weather["implementation"] == "live_weather_qweather_stub"
        assert weather["status"] == "unavailable"
