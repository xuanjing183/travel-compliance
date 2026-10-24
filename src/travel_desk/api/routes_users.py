"""Demo accounts (#2) and personal preferences (#24)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Request

from travel_desk.api.deps import current_user
from travel_desk.api.errors import get_trace_id
from travel_desk.db.repositories import list_employees
from travel_desk.services import preferences

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users")
def users(request: Request) -> dict:
    """Public demo account list for the login switcher (contract 6.5)."""
    return {"users": list_employees(request.app.state.settings.db_path)}


@router.get("/users/{user_id}/preferences")
def get_preferences(request: Request, user_id: str, user: dict = Depends(current_user)):
    return preferences.get_preferences(request.app.state.settings, user, user_id)


@router.put("/users/{user_id}/preferences")
def put_preferences(request: Request, user_id: str, body: Any = Body(None),
                    user: dict = Depends(current_user)):
    return preferences.put_preferences(request.app.state.settings, user, user_id, body,
                                       trace_id=get_trace_id(request))


@router.delete("/users/{user_id}/preferences")
def delete_preferences(request: Request, user_id: str, user: dict = Depends(current_user)):
    return preferences.delete_preferences(request.app.state.settings, user, user_id,
                                          trace_id=get_trace_id(request))
