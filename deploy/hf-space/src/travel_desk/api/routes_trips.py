"""Trip endpoints #3–#8, #10, #11, #13, #15–#17 (docs/api.md §3)."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from travel_desk.api.deps import current_user, get_workflow
from travel_desk.api.errors import get_trace_id
from travel_desk.api.sse import sse_response
from travel_desk.db.database import read_session
from travel_desk.services import audit, conversation, uploads
from travel_desk.services.errors import ServiceError
from travel_desk.services.trips import create_trip, update_slots, visible_trip
from travel_desk.services.views import list_trip_summaries, trip_view

router = APIRouter(prefix="/api/trips", tags=["trips"])

_SCOPES = {"employee": ("mine",), "manager": ("mine", "reports"), "admin": ("all",)}
_DEFAULT_SCOPE = {"employee": "mine", "manager": "mine", "admin": "all"}


def _view(request: Request, trip: dict[str, Any]) -> dict[str, Any]:
    with read_session(request.app.state.settings.db_path) as conn:
        return trip_view(conn, trip)


@router.post("", status_code=201)
async def create(request: Request, body: Any = Body(None), user: dict = Depends(current_user)):
    settings, trace_id = request.app.state.settings, get_trace_id(request)
    if body is not None and not isinstance(body, dict):
        raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
    text = (body or {}).get("text")
    if text is not None:
        text = conversation.check_text(text)
    trip = create_trip(settings, user, trace_id=trace_id)
    assistant = None
    if text is not None:
        assistant = await conversation.first_turn(get_workflow(request), request.app.state.slot_extractor,
                                                  trip, user, text, trace_id=trace_id)
        with read_session(settings.db_path) as conn:
            trip = visible_trip(conn, trip["id"], user)[0]
    return {"trip": _view(request, trip), "assistant": assistant}


@router.get("")
def list_trips(request: Request, scope: str | None = Query(None), user: dict = Depends(current_user)):
    allowed = _SCOPES[user["role"]]
    if scope is not None and scope not in ("mine", "reports", "all"):
        raise ServiceError(422, "VALIDATION_ERROR", "scope 只能是 mine、reports 或 all",
                           {"errors": [{"loc": ["query", "scope"], "type": "enum",
                                        "msg": "mine|reports|all"}]})
    scope = scope or _DEFAULT_SCOPE[user["role"]]
    if scope not in allowed:
        raise ServiceError(403, "FORBIDDEN", "当前角色不能查看该范围",
                           {"scope": scope, "allowed": list(allowed)})
    with read_session(request.app.state.settings.db_path) as conn:
        if scope == "all":
            ids = None
        elif scope == "mine":
            ids = [user["id"]]
        else:
            ids = [row["id"] for row in conn.execute("SELECT id FROM employees WHERE manager_id = ?",
                                                     (user["id"],))]
        return {"scope": scope, "trips": list_trip_summaries(conn, ids)}


@router.get("/{trip_id}")
def get_trip(request: Request, trip_id: str, user: dict = Depends(current_user)):
    with read_session(request.app.state.settings.db_path) as conn:
        trip, _ = visible_trip(conn, trip_id, user)
        return trip_view(conn, trip)


@router.patch("/{trip_id}/slots")
def patch_slots(request: Request, trip_id: str, body: Any = Body(None), user: dict = Depends(current_user)):
    if not isinstance(body, dict):
        raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
    trip = update_slots(request.app.state.settings, user, trip_id,
                        expected_revision=body.get("expected_revision"),
                        changes=body.get("slots"), trace_id=get_trace_id(request))
    return _view(request, trip)


@router.post("/{trip_id}/messages")
async def post_message(request: Request, trip_id: str, body: Any = Body(None),
                       idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                       user: dict = Depends(current_user)):
    run = await conversation.post_message(get_workflow(request), request.app.state.slot_extractor, trip_id,
                                          user, body, idempotency_key=idempotency_key,
                                          trace_id=get_trace_id(request))
    return sse_response(run)


@router.post("/{trip_id}/submit")
async def submit(request: Request, trip_id: str, body: Any = Body(None),
                 idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                 user: dict = Depends(current_user)):
    run = await get_workflow(request).submit(trip_id, user, body, idempotency_key=idempotency_key,
                                             trace_id=get_trace_id(request))
    return sse_response(run)


@router.post("/{trip_id}/image-extractions", status_code=201)
async def extract_image(request: Request, trip_id: str, body: Any = Body(None),
                        user: dict = Depends(current_user)):
    state = request.app.state
    return await uploads.extract_invitation(state.settings, state.invitation_extractor,
                                            user, trip_id, body, trace_id=get_trace_id(request))


@router.post("/{trip_id}/image-extractions/{extraction_id}/confirm")
def confirm_image(request: Request, trip_id: str, extraction_id: str, body: Any = Body(None),
                  user: dict = Depends(current_user)):
    trip = uploads.confirm_extraction(request.app.state.settings, user, trip_id, extraction_id, body,
                                      trace_id=get_trace_id(request))
    return _view(request, trip)


@router.post("/{trip_id}/decision")
async def decision(request: Request, trip_id: str, body: Any = Body(None),
                   idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                   user: dict = Depends(current_user)):
    status, payload = await get_workflow(request).decide(trip_id, user, body, idempotency_key=idempotency_key,
                                                         trace_id=get_trace_id(request))
    return JSONResponse(status_code=status, content=payload)


@router.post("/{trip_id}/revalidate")
async def revalidate(request: Request, trip_id: str, body: Any = Body(None),
                     idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                     user: dict = Depends(current_user)):
    status, payload = await get_workflow(request).revalidate(trip_id, user, body,
                                                             idempotency_key=idempotency_key,
                                                             trace_id=get_trace_id(request))
    return JSONResponse(status_code=status, content=payload)


@router.post("/{trip_id}/delivery/retry")
async def retry_delivery(request: Request, trip_id: str, user: dict = Depends(current_user)):
    return await get_workflow(request).retry_delivery(trip_id, user, trace_id=get_trace_id(request))


@router.get("/{trip_id}/audit")
def trip_audit(request: Request, trip_id: str, user: dict = Depends(current_user)):
    with read_session(request.app.state.settings.db_path) as conn:
        visible_trip(conn, trip_id, user)
        return {"events": audit.list_events(conn, trip_id)}


@router.get("/{trip_id}/itinerary")
def itinerary(request: Request, trip_id: str, revision: int | None = Query(None, ge=1),
              user: dict = Depends(current_user)):
    with read_session(request.app.state.settings.db_path) as conn:
        trip, _ = visible_trip(conn, trip_id, user)
        wanted = revision or trip["revision"]
        row = conn.execute("SELECT content_json, created_at FROM itineraries"
                           " WHERE trip_id = ? AND revision = ?", (trip_id, wanted)).fetchone()
        if row is None:
            raise ServiceError(404, "ITINERARY_NOT_FOUND", "该版本没有行程单", {"trip_id": trip_id,
                                                                            "revision": wanted})
        return {"trip_id": trip_id, "revision": wanted, "created_at": row["created_at"],
                "itinerary": json.loads(row["content_json"])}
