"""Admin endpoints #25–#28: outbox, demo reset, fault switches, provider health."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from travel_desk.api.deps import get_workflow, require_admin
from travel_desk.api.errors import get_trace_id
from travel_desk.db.database import db_session, read_session
from travel_desk.db.repositories import list_faults, set_fault
from travel_desk.llm.dashscope import api_key_present
from travel_desk.providers.base import FAULT_KEYS
from travel_desk.services import audit
from travel_desk.services.errors import ServiceError
from travel_desk.services.views import list_outbox

router = APIRouter(prefix="/api", tags=["admin"])

RESET_CONFIRMATION = "RESET_DEMO_DATA"
# Which fault switch degrades which provider (docs/api.md #27/#28).
_DEMO_PROVIDERS = (("transport", "demo_transport", "transport_timeout"),
                   ("hotel_contract", "demo_hotel", "no_protocol_hotel"),
                   ("hotel_backup", "demo_hotel", None),
                   ("weather", "demo_weather", "weather_unavailable"))
_LIVE_PROVIDERS = (("transport", "live_transport", None),
                   ("hotel_contract", "live_hotel_hybrid", None),
                   ("hotel_backup", "live_hotel_hybrid", None),
                   ("weather", "live_weather_qweather", None))


@router.get("/outbox")
def outbox(request: Request, kind: str | None = Query(None), trip_id: str | None = Query(None),
           user: dict = Depends(require_admin)):
    if kind is not None and kind not in ("trip_delivery", "workflow_notification"):
        raise ServiceError(422, "VALIDATION_ERROR", "kind 只能是 trip_delivery 或 workflow_notification",
                           {"errors": [{"loc": ["query", "kind"], "type": "enum",
                                        "msg": "trip_delivery|workflow_notification"}]})
    with read_session(request.app.state.settings.db_path) as conn:
        return {"emails": list_outbox(conn, trip_id=trip_id, kind=kind)}


@router.post("/demo/reset")
async def demo_reset(request: Request, body: Any = Body(None), user: dict = Depends(require_admin)):
    if not isinstance(body, dict) or body.get("confirmation") != RESET_CONFIRMATION:
        raise ServiceError(422, "CONFIRMATION_REQUIRED", f"请在 confirmation 中输入 {RESET_CONFIRMATION}",
                           {"expected": RESET_CONFIRMATION})
    await get_workflow(request).reset(user, trace_id=get_trace_id(request))
    return {"status": "reset"}


@router.get("/demo/faults")
def get_faults(request: Request, user: dict = Depends(require_admin)):
    return {"faults": list_faults(request.app.state.settings.db_path)}


@router.post("/demo/faults")
def post_fault(request: Request, body: Any = Body(None), user: dict = Depends(require_admin)):
    settings = request.app.state.settings
    fault = body.get("fault") if isinstance(body, dict) else None
    if fault not in FAULT_KEYS:
        raise ServiceError(400, "UNKNOWN_FAULT", "未知的故障开关", {"allowed": list(FAULT_KEYS)})
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise ServiceError(422, "VALIDATION_ERROR", "enabled 必须是布尔值",
                           {"errors": [{"loc": ["body", "enabled"], "type": "bool_type", "msg": "boolean"}]})
    before = list_faults(settings.db_path).get(fault)
    set_fault(settings.db_path, fault, enabled, audit.now_ts())
    with db_session(settings.db_path) as conn:
        audit.record(conn, trip_id=None, revision=None, actor=user["id"], event_type="fault_changed",
                     payload={"fault": fault, "before": before, "after": enabled},
                     trace_id=get_trace_id(request))
    return {"faults": list_faults(settings.db_path)}


@router.get("/health/providers")
def provider_health(request: Request, user: dict = Depends(require_admin)):
    settings = request.app.state.settings
    is_live = settings.provider_mode == "live"
    provider_defs = _LIVE_PROVIDERS if is_live else _DEMO_PROVIDERS
    faults = list_faults(settings.db_path) if not is_live else {}
    providers = []
    with read_session(settings.db_path) as conn:
        for name, base_impl, fault in provider_defs:
            # For live weather, implementation depends on QWeather config.
            if is_live and name == "weather":
                impl = base_impl if settings.qweather_api_host else "live_weather_qweather_stub"
            else:
                impl = base_impl
            row = conn.execute(
                "SELECT ts, payload_json FROM audit_events WHERE type = 'provider_call'"
                " AND json_extract(payload_json, '$.provider') = ?"
                " AND json_extract(payload_json, '$.implementation') = ?"
                " AND json_extract(payload_json, '$.status') != 'ok' ORDER BY seq DESC LIMIT 1",
                (name, impl)).fetchone()
            last_error = None
            if row is not None:
                payload = json.loads(row["payload_json"])
                last_error = {"ts": row["ts"], "status": payload["status"], "error": payload.get("error")}
            if is_live:
                if name == "weather" and not settings.qweather_api_host:
                    status = "unavailable"
                else:
                    status = "ok" if last_error is None else "error"
            else:
                injected = bool(fault and faults.get(fault))
                status = "fault_injected" if injected else "ok"
            providers.append({"name": name, "implementation": impl,
                              "mode": "live" if is_live else "demo",
                              "status": status, "fault": fault,
                              "last_error": last_error})
    result: dict[str, Any] = {
        "llm_mode": settings.llm_mode,
        "llm": {"mode": settings.llm_mode, "text_model": settings.text_model,
                "vision_model": settings.vision_model,
                "api_key_configured": api_key_present() if settings.llm_mode == "real" else None},
        "providers": providers,
        "network": request.app.state.network_guard.summary(),
    }
    if is_live:
        result["provider_mode"] = "live"
        result["provider_credentials"] = {
            "tavily_api_key_configured": bool(settings.tavily_api_key),
            "qweather_api_host_configured": bool(settings.qweather_api_host),
            "qweather_api_key_configured": bool(settings.qweather_api_key),
        }
    return result
