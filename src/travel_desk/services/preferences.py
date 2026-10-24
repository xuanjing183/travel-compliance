"""Personal preferences (#24, REQ-014, D-20).

Only the owner (employee or manager) may read or change their preferences; admin
has no preference capability (§6.4). Changes are audited as `preferences_changed`
(`trip_id=null`). Trips already submitted keep their snapshot (D-20): the next
submit copies whatever is current then.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session
from travel_desk.domain.models import Preferences
from travel_desk.services import audit
from travel_desk.services.access import load_employee
from travel_desk.services.errors import ServiceError
from travel_desk.services.trips import dumps


def _normalize(body: Any) -> dict[str, Any]:
    """422 format check; the stored form drops empty notes so `{}` means "no preference"."""
    if not isinstance(body, dict):
        raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
    try:
        prefs = Preferences.model_validate(body)
    except ValidationError as exc:
        errors = [{"loc": ["body", *map(str, e["loc"])], "type": e["type"], "msg": e["msg"]}
                  for e in exc.errors()]
        raise ServiceError(422, "INVALID_PREFERENCES",
                           "偏好只支持 avoid_early_departure（布尔）与 notes（≤200 字）",
                           {"errors": errors}) from None
    stored = prefs.model_dump(exclude_none=True)
    notes = stored.get("notes")
    if notes is not None:
        notes = " ".join(notes.split())
        if notes:
            stored["notes"] = notes
        else:
            stored.pop("notes")
    return stored


def _owner(conn, actor: dict[str, Any], user_id: str) -> dict[str, Any]:
    target = load_employee(conn, user_id)
    if target is None:
        raise ServiceError(404, "NOT_FOUND", "用户不存在", {"user_id": user_id})
    if actor["role"] == "admin":
        raise ServiceError(403, "FORBIDDEN", "管理员没有偏好设置能力", {"role": "admin"})
    if actor["id"] != user_id:
        raise ServiceError(403, "FORBIDDEN", "只能查看或修改本人的偏好", {"owner_required": True})
    return target


def view(user_id: str, preferences: dict[str, Any]) -> dict[str, Any]:
    return {"user_id": user_id, "preferences": preferences}


def get_preferences(settings: Settings, actor: dict[str, Any], user_id: str) -> dict[str, Any]:
    with read_session(settings.db_path) as conn:
        target = _owner(conn, actor, user_id)
    return view(user_id, target["preferences"])


def _write(settings: Settings, actor: dict[str, Any], user_id: str, new: dict[str, Any], *,
           action: str, trace_id: str) -> dict[str, Any]:
    with db_session(settings.db_path) as conn:
        target = _owner(conn, actor, user_id)
        before = target["preferences"]
        if before != new:
            conn.execute("UPDATE employees SET preferences_json = ? WHERE id = ?", (dumps(new), user_id))
        audit.record(conn, trip_id=None, revision=None, actor=actor["id"], event_type="preferences_changed",
                     payload={"user_id": user_id, "action": action, "before": before, "after": new,
                              "changed": before != new}, trace_id=trace_id)
    return view(user_id, new)


def put_preferences(settings: Settings, actor: dict[str, Any], user_id: str, body: Any, *,
                    trace_id: str) -> dict[str, Any]:
    new = _normalize(body)  # 422 format errors come before 404/403 (docs/api.md §1.3)
    return _write(settings, actor, user_id, new, action="put", trace_id=trace_id)


def delete_preferences(settings: Settings, actor: dict[str, Any], user_id: str, *,
                       trace_id: str) -> dict[str, Any]:
    return _write(settings, actor, user_id, {}, action="delete", trace_id=trace_id)
