from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from travel_desk.config import MAX_UPLOAD_BYTES, REPO_ROOT, Settings
from travel_desk.main import create_app

POLICIES_DIR = REPO_ROOT / "tests" / "fixtures" / "policies"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "var"
    return Settings(
        root_dir=REPO_ROOT,
        data_dir=data_dir,
        db_path=data_dir / "app.sqlite",
        checkpoint_db_path=data_dir / "checkpoints.sqlite",
        upload_dir=data_dir / "uploads",
        policies_dir=POLICIES_DIR,
        llm_mode="fake",
        provider_mode="demo",
        text_model="qwen3.8-max",
        vision_model="qwen3.5-plus",
        embedding_model="qwen3.7-text-embedding",
        dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        demo_today=date(2026, 10, 20),
        max_upload_bytes=MAX_UPLOAD_BYTES,
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
