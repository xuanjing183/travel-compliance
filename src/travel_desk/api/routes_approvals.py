"""Approval inbox #12 and uploads #9."""
from __future__ import annotations

import json as _json
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from travel_desk.api.deps import current_user, require_manager
from travel_desk.api.errors import get_trace_id
from travel_desk.db.database import read_session
from travel_desk.services import uploads
from travel_desk.services.errors import ServiceError
from travel_desk.services.views import approval_view

router = APIRouter(prefix="/api", tags=["approvals", "uploads"])

_VALID_TABS = {"pending", "decided", "initiated", "all"}
_VALID_TRIP_STATUSES = {
    "PENDING_APPROVAL", "APPROVED", "AUTO_APPROVED", "REJECTED",
    "DELIVERED", "POLICY_BLOCKED", "NO_APPROVER",
}


def _compute_summary(conn: Any, approver_id: str) -> dict[str, Any]:
    """Compute approval summary statistics for the stats bar."""
    today = date.today().isoformat()
    month_start = date.today().replace(day=1).isoformat()

    # Pending count
    pending = conn.execute(
        "SELECT COUNT(*) FROM approvals a JOIN trips t ON t.id = a.trip_id"
        " JOIN employees e ON e.id = t.employee_id"
        " WHERE a.approver_id = ? AND a.status = 'pending' AND a.revision = t.revision"
        " AND t.status = 'PENDING_APPROVAL' AND e.manager_id = ?",
        (approver_id, approver_id)).fetchone()[0]

    # Decided today
    decided_today = conn.execute(
        "SELECT COUNT(*) FROM approvals"
        " WHERE approver_id = ? AND status = 'decided' AND decided_at >= ?",
        (approver_id, today)).fetchone()[0]

    # Decided this month
    decided_month = conn.execute(
        "SELECT COUNT(*) FROM approvals"
        " WHERE approver_id = ? AND status = 'decided' AND decided_at >= ?",
        (approver_id, month_start)).fetchone()[0]

    # Average decision hours (last 30 days)
    thirty_days_ago = (date.today() - timedelta(days=30)).isoformat()
    avg_row = conn.execute(
        "SELECT AVG((julianday(decided_at) - julianday(created_at)) * 24) as avg_hours"
        " FROM approvals WHERE approver_id = ? AND status = 'decided' AND decided_at >= ?",
        (approver_id, thirty_days_ago)).fetchone()
    avg_hours = round(avg_row["avg_hours"], 1) if avg_row and avg_row["avg_hours"] else None

    return {
        "pending_count": pending,
        "decided_today": decided_today,
        "decided_this_month": decided_month,
        "avg_decision_hours": avg_hours,
    }


@router.get("/approvals")
def list_approvals(
    request: Request,
    tab: str = Query("pending"),
    trip_status: str | None = Query(None),
    since: str | None = Query(None),
    user: dict = Depends(require_manager),
):
    if tab not in _VALID_TABS:
        raise ServiceError(422, "VALIDATION_ERROR", f"tab 只支持 {sorted(_VALID_TABS)}",
                           {"errors": [{"loc": ["query", "tab"], "type": "enum", "msg": sorted(_VALID_TABS)}]})
    if trip_status is not None and trip_status not in _VALID_TRIP_STATUSES:
        raise ServiceError(422, "VALIDATION_ERROR", f"trip_status 只支持 {sorted(_VALID_TRIP_STATUSES)}",
                           {"errors": [{"loc": ["query", "trip_status"], "type": "enum",
                                        "msg": sorted(_VALID_TRIP_STATUSES)}]})
    with read_session(request.app.state.settings.db_path) as conn:
        if tab == "pending":
            rows = conn.execute(
                "SELECT a.* FROM approvals a JOIN trips t ON t.id = a.trip_id"
                " JOIN employees e ON e.id = t.employee_id"
                " WHERE a.approver_id = ? AND a.status = 'pending' AND a.revision = t.revision"
                " AND t.status = 'PENDING_APPROVAL' AND e.manager_id = ?"
                " ORDER BY a.created_at DESC",
                (user["id"], user["id"])).fetchall()
        elif tab == "decided":
            sql = ("SELECT a.* FROM approvals a"
                   " WHERE a.approver_id = ? AND a.status = 'decided'")
            params: list[Any] = [user["id"]]
            if trip_status is not None:
                sql += " AND EXISTS (SELECT 1 FROM trips t WHERE t.id = a.trip_id AND t.status = ?)"
                params.append(trip_status)
            sql += " ORDER BY a.decided_at DESC"
            rows = conn.execute(sql, params).fetchall()
        else:  # tab == "all"
            sql = "SELECT a.* FROM approvals a WHERE a.approver_id = ?"
            params = [user["id"]]
            if trip_status is not None:
                sql += " AND EXISTS (SELECT 1 FROM trips t WHERE t.id = a.trip_id AND t.status = ?)"
                params.append(trip_status)
            sql += " ORDER BY a.created_at DESC"
            rows = conn.execute(sql, params).fetchall()

        approvals = [approval_view(conn, row) for row in rows]

        # Apply since filter client-side (on created_at or decided_at)
        if since is not None:
            try:
                since_dt = datetime.fromisoformat(since)
                approvals = [
                    a for a in approvals
                    if (a.get("decided_at") or a.get("created_at", "")) >= since
                ]
            except ValueError:
                raise ServiceError(422, "VALIDATION_ERROR", "since 必须是 ISO 日期格式 (yyyy-mm-dd)",
                                   {"errors": [{"loc": ["query", "since"], "type": "value_error",
                                                "msg": "ISO 日期格式"}]})

        summary = _compute_summary(conn, user["id"])

    return {"approvals": approvals, "summary": summary}


@router.post("/uploads", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), user: dict = Depends(current_user)):
    settings = request.app.state.settings
    data = await file.read(settings.max_upload_bytes + 1)
    return uploads.save_upload(settings, user, filename=file.filename, data=data,
                               trace_id=get_trace_id(request))
