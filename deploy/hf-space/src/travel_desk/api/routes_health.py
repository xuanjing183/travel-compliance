from __future__ import annotations

from fastapi import APIRouter, Request

from travel_desk.db.database import read_session

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    settings = request.app.state.settings
    db_ok = True
    try:
        with read_session(settings.db_path) as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception:  # noqa: BLE001 - health must report, not raise
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "demo_today": settings.demo_today.isoformat(),
        "llm_mode": settings.llm_mode,
    }
