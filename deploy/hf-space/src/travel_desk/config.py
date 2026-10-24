"""Runtime configuration.

Secrets are only read from the process environment and are never logged,
persisted, or returned by the API (HC-07). `.env` files are loaded at import
time as a convenience for local development; production deployments should
inject secrets through the process environment instead.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root so that local development picks up secrets
# without requiring the operator to export them manually. System environment
# variables always take precedence (override=False is the default).
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# The material demos enable LangSmith tracing through their .env. This project
# must never upload traces: force it off before any LangChain import.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


class ConfigError(ValueError):
    """Invalid configuration: fail at startup rather than run with a silent default."""


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data_dir: Path
    db_path: Path
    checkpoint_db_path: Path
    upload_dir: Path
    policies_dir: Path
    llm_mode: str
    provider_mode: str
    text_model: str
    vision_model: str
    embedding_model: str
    dashscope_base_url: str
    demo_today: date
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    # Real-mode policy Q&A retrieval: "vector" or "hybrid" (BM25 + RRF); AC-025 decides (REQ-029).
    # Fake/recorded modes always use BM25 (no embeddings without network, HC-06).
    retrieval_mode: str = "vector"
    providers_dir: Path = REPO_ROOT / "tests" / "fixtures" / "providers"
    recorded_dir: Path = REPO_ROOT / "tests" / "fixtures" / "recorded"
    # AUTH-01 evidence: every real model call is appended here; the guard also reads the ledger.
    usage_log_path: Path = REPO_ROOT / "artifacts" / "llm-usage.jsonl"
    cost_ledger_path: Path = REPO_ROOT / "artifacts" / "cost-ledger.md"
    # AUTH-02 Live Provider credentials (read from environment, never logged).
    tavily_api_key: str | None = None
    kiwi_mcp_url: str = "https://mcp.kiwi.com/"
    qweather_api_host: str | None = None
    qweather_api_key: str | None = None

    @property
    def static_dir(self) -> Path:
        return Path(__file__).resolve().parent / "static"

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = _env_path("TRAVEL_DESK_DATA_DIR", REPO_ROOT / "var")
        raw_today = _env_str("DEMO_TODAY", "2026-10-20")
        try:
            # Python 3.11 also accepts compact ISO forms; the contract date format is YYYY-MM-DD only.
            if len(raw_today) != 10:
                raise ValueError(raw_today)
            demo_today = date.fromisoformat(raw_today)
        except ValueError as exc:
            raise ConfigError("DEMO_TODAY must be YYYY-MM-DD") from exc
        mode = _env_str("TRAVEL_DESK_LLM_MODE", "fake").lower()
        if mode not in {"fake", "recorded", "real"}:
            raise ConfigError("TRAVEL_DESK_LLM_MODE must be one of fake, recorded, real")
        provider_mode = _env_str("TRAVEL_DESK_PROVIDER_MODE", "demo").lower()
        if provider_mode not in {"demo", "live"}:
            raise ConfigError("TRAVEL_DESK_PROVIDER_MODE must be one of demo, live")
        retrieval = _env_str("TRAVEL_DESK_RETRIEVAL", "vector").lower()
        if retrieval not in {"vector", "hybrid"}:
            raise ConfigError("TRAVEL_DESK_RETRIEVAL must be one of vector, hybrid")
        return cls(
            root_dir=REPO_ROOT,
            data_dir=data_dir,
            db_path=_env_path("TRAVEL_DESK_DB", data_dir / "app.sqlite"),
            checkpoint_db_path=_env_path(
                "TRAVEL_DESK_CHECKPOINT_DB", data_dir / "checkpoints.sqlite"
            ),
            upload_dir=_env_path("TRAVEL_DESK_UPLOAD_DIR", data_dir / "uploads"),
            policies_dir=_env_path(
                "TRAVEL_DESK_POLICIES_DIR", REPO_ROOT / "tests" / "fixtures" / "policies"
            ),
            llm_mode=mode,
            provider_mode=provider_mode,
            text_model=_env_str("TRAVEL_DESK_TEXT_MODEL", "qwen3.8-max"),
            vision_model=_env_str("TRAVEL_DESK_VISION_MODEL", "qwen3.5-plus"),
            embedding_model=_env_str("TRAVEL_DESK_EMBED_MODEL", "qwen3.7-text-embedding"),
            dashscope_base_url=_env_str("TRAVEL_DESK_DASHSCOPE_BASE_URL", DEFAULT_DASHSCOPE_BASE_URL),
            demo_today=demo_today,
            max_upload_bytes=_env_int("TRAVEL_DESK_MAX_UPLOAD_BYTES", MAX_UPLOAD_BYTES),
            retrieval_mode=retrieval,
            providers_dir=_env_path(
                "TRAVEL_DESK_PROVIDERS_DIR", REPO_ROOT / "tests" / "fixtures" / "providers"
            ),
            recorded_dir=_env_path(
                "TRAVEL_DESK_RECORDED_DIR", REPO_ROOT / "tests" / "fixtures" / "recorded"
            ),
            usage_log_path=_env_path("TRAVEL_DESK_USAGE_LOG", REPO_ROOT / "artifacts" / "llm-usage.jsonl"),
            tavily_api_key=_env_str("TRAVEL_DESK_TAVILY_API_KEY", "") or None,
            kiwi_mcp_url=_env_str("TRAVEL_DESK_KIWI_MCP_URL", "https://mcp.kiwi.com/"),
            qweather_api_host=_env_str("TRAVEL_DESK_QWEATHER_API_HOST", "") or None,
            qweather_api_key=_env_str("TRAVEL_DESK_QWEATHER_API_KEY", "") or None,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def reset_settings_cache() -> None:
    """Used by tests after changing environment variables."""
    get_settings.cache_clear()


logger = logging.getLogger(__name__)


def warn_live_credential_gaps(settings: Settings) -> None:
    """AUTH-02 startup check: log missing live credentials (names only, never values)."""
    if settings.provider_mode != "live":
        return
    if not settings.tavily_api_key:
        logger.warning("TRAVEL_DESK_TAVILY_API_KEY is not set; Tavily references disabled")
    if not settings.qweather_api_host:
        logger.warning("TRAVEL_DESK_QWEATHER_API_HOST is not set; weather forecasts unavailable")
    if not settings.qweather_api_key:
        logger.warning("TRAVEL_DESK_QWEATHER_API_KEY is not set; weather forecasts unavailable")
