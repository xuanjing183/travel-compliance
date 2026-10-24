"""Reports / analytics endpoint — all data computed from the database."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from travel_desk.api.deps import current_user, require_admin
from travel_desk.db.database import read_session
from travel_desk.services.errors import ServiceError

router = APIRouter(prefix="/api", tags=["reports"])

_VALID_PERIODS = {"week", "month", "quarter", "year"}


def _period_start(period: str) -> str:
    today = date.today()
    if period == "week":
        # Monday of current week
        start = today - timedelta(days=today.weekday())
    elif period == "month":
        start = today.replace(day=1)
    elif period == "quarter":
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=quarter_month, day=1)
    else:  # year
        start = today.replace(month=1, day=1)
    return start.isoformat()


def _compute_summary(conn: Any, period_start: str) -> dict[str, Any]:
    """Summary cards: trip count, total cost, avg cost, approval pass rate."""
    # Trip count in period
    trip_count = conn.execute(
        "SELECT COUNT(*) FROM trips WHERE created_at >= ?", (period_start,)).fetchone()[0]

    # Total cost in period (sum of components, since total_fen can be null)
    total_row = conn.execute(
        "SELECT"
        " COALESCE(SUM(CAST(json_extract(cost_json, '$.transport_fen') AS INTEGER)), 0)"
        " + COALESCE(SUM(CAST(json_extract(cost_json, '$.hotel_fen') AS INTEGER)), 0)"
        " + COALESCE(SUM(CAST(json_extract(cost_json, '$.meal_fen') AS INTEGER)), 0)"
        " as total_fen"
        " FROM trips WHERE created_at >= ? AND cost_json IS NOT NULL",
        (period_start,)).fetchone()
    total_fen = total_row["total_fen"] or 0

    # Avg cost
    avg_fen = round(total_fen / trip_count) if trip_count > 0 else 0

    # Approval pass rate: approvals with decision='approve' / total decided
    approval_row = conn.execute(
        "SELECT COUNT(*) as total,"
        " SUM(CASE WHEN decision = 'approve' THEN 1 ELSE 0 END) as approved"
        " FROM approvals WHERE status = 'decided'").fetchone()
    total_decided = approval_row["total"] or 0
    total_approved = approval_row["approved"] or 0
    pass_rate = round(total_approved / total_decided * 100, 1) if total_decided > 0 else 0

    return {
        "trip_count": trip_count,
        "total_fen": total_fen,
        "avg_fen": avg_fen,
        "approval_pass_rate": pass_rate,
        "total_decided": total_decided,
        "total_approved": total_approved,
    }


def _monthly_trend(conn: Any) -> list[dict[str, Any]]:
    """Last 6 months: trip count and total cost per month."""
    today = date.today()
    months: list[dict[str, Any]] = []
    for i in range(5, -1, -1):
        # Go back i months from current month
        target_month = today.month - i
        target_year = today.year
        while target_month <= 0:
            target_month += 12
            target_year -= 1
        month_start = date(target_year, target_month, 1).isoformat()
        next_month = target_month + 1
        next_year = target_year
        if next_month > 12:
            next_month = 1
            next_year += 1
        month_end = date(next_year, next_month, 1).isoformat()

        row = conn.execute(
            "SELECT COUNT(*) as cnt,"
            " COALESCE(SUM(CAST(json_extract(cost_json, '$.transport_fen') AS INTEGER)), 0)"
            " + COALESCE(SUM(CAST(json_extract(cost_json, '$.hotel_fen') AS INTEGER)), 0)"
            " + COALESCE(SUM(CAST(json_extract(cost_json, '$.meal_fen') AS INTEGER)), 0)"
            " as total_fen"
            " FROM trips WHERE created_at >= ? AND created_at < ? AND cost_json IS NOT NULL",
            (month_start, month_end)).fetchone()
        months.append({
            "label": f"{target_month}月",
            "trip_count": row["cnt"],
            "total_fen": row["total_fen"],
        })
    return months


def _expense_breakdown(conn: Any, period_start: str) -> list[dict[str, Any]]:
    """Expense breakdown by category (transport, hotel, meal, other)."""
    row = conn.execute(
        "SELECT"
        " COALESCE(SUM(CAST(json_extract(cost_json, '$.transport_fen') AS INTEGER)), 0) as transport,"
        " COALESCE(SUM(CAST(json_extract(cost_json, '$.hotel_fen') AS INTEGER)), 0) as hotel,"
        " COALESCE(SUM(CAST(json_extract(cost_json, '$.meal_fen') AS INTEGER)), 0) as meal"
        " FROM trips WHERE created_at >= ? AND cost_json IS NOT NULL",
        (period_start,)).fetchone()

    transport = row["transport"] or 0
    hotel = row["hotel"] or 0
    meal = row["meal"] or 0
    component_total = transport + hotel + meal

    def pct(val: int) -> float:
        return round(val / component_total * 100, 1) if component_total > 0 else 0.0

    return [
        {"label": "机票交通", "fen": transport, "pct": pct(transport)},
        {"label": "酒店住宿", "fen": hotel, "pct": pct(hotel)},
        {"label": "餐饮补贴", "fen": meal, "pct": pct(meal)},
    ]


def _department_comparison(conn: Any, period_start: str) -> list[dict[str, Any]]:
    """Per-department: trip count, total cost, avg cost, percentage."""
    rows = conn.execute(
        "SELECT e.department,"
        " COUNT(t.id) as trip_count,"
        " COALESCE(SUM(CAST(json_extract(t.cost_json, '$.transport_fen') AS INTEGER)), 0)"
        " + COALESCE(SUM(CAST(json_extract(t.cost_json, '$.hotel_fen') AS INTEGER)), 0)"
        " + COALESCE(SUM(CAST(json_extract(t.cost_json, '$.meal_fen') AS INTEGER)), 0)"
        " as total_fen"
        " FROM trips t JOIN employees e ON e.id = t.employee_id"
        " WHERE t.created_at >= ? AND t.cost_json IS NOT NULL"
        " GROUP BY e.department ORDER BY total_fen DESC",
        (period_start,)).fetchall()

    grand_total = sum(r["total_fen"] for r in rows) or 1
    result = []
    for r in rows:
        total_fen = r["total_fen"]
        count = r["trip_count"]
        result.append({
            "department": r["department"],
            "trip_count": count,
            "total_fen": total_fen,
            "avg_fen": round(total_fen / count) if count > 0 else 0,
            "pct": round(total_fen / grand_total * 100, 1),
        })
    return result


def _top_destinations(conn: Any, period_start: str, limit: int = 5) -> list[dict[str, Any]]:
    """Top destinations by trip count."""
    rows = conn.execute(
        "SELECT json_extract(slots_json, '$.destination') as destination,"
        " COUNT(*) as trip_count"
        " FROM trips WHERE created_at >= ? AND slots_json IS NOT NULL"
        " GROUP BY destination ORDER BY trip_count DESC LIMIT ?",
        (period_start, limit)).fetchall()
    return [
        {"destination": r["destination"] or "未知", "trip_count": r["trip_count"]}
        for r in rows
    ]


def _approval_efficiency(conn: Any) -> dict[str, Any]:
    """Approval efficiency metrics."""
    # Average decision time in hours (last 30 days)
    thirty_days_ago = (date.today() - timedelta(days=30)).isoformat()
    avg_row = conn.execute(
        "SELECT AVG((julianday(decided_at) - julianday(created_at)) * 24) as avg_hours"
        " FROM approvals WHERE status = 'decided' AND decided_at >= ?",
        (thirty_days_ago,)).fetchone()
    avg_hours = round(avg_row["avg_hours"], 1) if avg_row and avg_row["avg_hours"] else None

    # First-pass rate: trips approved on first revision / total decided trips
    first_pass_row = conn.execute(
        "SELECT COUNT(DISTINCT a.trip_id) as total_trips,"
        " COUNT(DISTINCT CASE WHEN a.decision = 'approve' AND a.revision = 1 THEN a.trip_id END) as first_pass"
        " FROM approvals a WHERE a.status = 'decided'").fetchone()
    total_trips = first_pass_row["total_trips"] or 0
    first_pass = first_pass_row["first_pass"] or 0
    first_pass_rate = round(first_pass / total_trips * 100, 1) if total_trips > 0 else 0

    # Rejection rate
    reject_row = conn.execute(
        "SELECT COUNT(*) as total,"
        " SUM(CASE WHEN decision = 'reject' THEN 1 ELSE 0 END) as rejected"
        " FROM approvals WHERE status = 'decided'").fetchone()
    total_decided = reject_row["total"] or 0
    total_rejected = reject_row["rejected"] or 0
    reject_rate = round(total_rejected / total_decided * 100, 1) if total_decided > 0 else 0

    return {
        "avg_decision_hours": avg_hours,
        "avg_decision_days": round(avg_hours / 24, 1) if avg_hours else None,
        "first_pass_rate": first_pass_rate,
        "rejection_rate": reject_rate,
    }


@router.get("/reports")
def get_reports(
    request: Request,
    period: str = Query("month"),
    user: dict = Depends(current_user),
):
    if period not in _VALID_PERIODS:
        raise ServiceError(422, "VALIDATION_ERROR", f"period 只支持 {sorted(_VALID_PERIODS)}",
                           {"errors": [{"loc": ["query", "period"], "type": "enum",
                                        "msg": sorted(_VALID_PERIODS)}]})

    period_start = _period_start(period)

    with read_session(request.app.state.settings.db_path) as conn:
        summary = _compute_summary(conn, period_start)
        monthly = _monthly_trend(conn)
        breakdown = _expense_breakdown(conn, period_start)
        departments = _department_comparison(conn, period_start)
        destinations = _top_destinations(conn, period_start)
        efficiency = _approval_efficiency(conn)

    return {
        "period": period,
        "period_start": period_start,
        "summary": summary,
        "monthly_trend": monthly,
        "expense_breakdown": breakdown,
        "department_comparison": departments,
        "top_destinations": destinations,
        "approval_efficiency": efficiency,
    }
