"""Demo identity resolution.

`X-Demo-User` is a demo-only identity switch, not production authentication.
Every endpoint that touches trip or policy data must go through these
dependencies so role and reporting-line checks stay server-side.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, Request

from travel_desk.api.errors import ApiError
from travel_desk.config import Settings
from travel_desk.db.repositories import get_employee


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_workflow(request: Request):
    return request.app.state.workflow


def current_user(
    request: Request,
    x_demo_user: str | None = Header(default=None, alias="X-Demo-User"),
) -> dict[str, Any]:
    if not x_demo_user:
        raise ApiError(401, "UNAUTHENTICATED", "缺少 X-Demo-User 请求头", {})
    employee = get_employee(get_settings_from_app(request).db_path, x_demo_user)
    if employee is None:
        raise ApiError(401, "UNKNOWN_DEMO_USER", "未知的演示账号", {"user_id": x_demo_user})
    return employee


def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "admin":
        raise ApiError(403, "FORBIDDEN", "该操作仅限管理员", {"required_role": "admin"})
    return user


def require_manager(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    # §6.4: approval is manager-only; admin has no approval capability.
    # Reporting-line (direct subordinate, not self) checks happen per trip in the service layer.
    if user["role"] != "manager":
        raise ApiError(403, "FORBIDDEN", "该操作仅限审批人", {"required_role": "manager"})
    return user
