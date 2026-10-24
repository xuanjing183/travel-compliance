"""Seed data: demo users, policy versions, confirmed rules, and faults.

Seed rule quotes are verified against the shipped policy documents at seed
time. A mismatch fails initialization instead of producing a policy whose
justification cannot be traced (HC-02).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from travel_desk.config import Settings

CITY_TIERS = {
    "A": ["北京", "上海", "广州", "深圳"],
    "B": ["杭州", "南京", "成都", "武汉", "西安", "重庆", "苏州", "天津"],
}
ALLOWED_CABINS = ["economy", "second_class"]

QUOTES_V12 = {
    "city": "A 类城市：北京、上海、广州、深圳。B 类城市：杭州、南京、成都、武汉、西安、重庆、苏州、天津。C 类城市：上述以外的其他城市。",
    "hotel_employee": "员工住宿上限：A 类城市每晚上限 500 元；B 类城市每晚上限 400 元；C 类城市每晚上限 300 元。",
    "hotel_manager": "经理及以上人员住宿上限：A 类城市每晚上限 650 元；B 类城市每晚上限 500 元；C 类城市每晚上限 400 元。",
    "meal": "伙食补贴按出差自然日计发：A 类城市每天 100 元；B 类城市每天 80 元；C 类城市每天 60 元。",
    "cabin": "全体员工乘坐经济舱或高铁二等座，不得乘坐公务舱、头等舱或高铁一等座。",
    "advance": "机票出发日距申请日不少于 3 个自然日。",
    "auto": "单次差旅总额不超过 3000 元、无制度违规且不超过申报预算的，可自动通过；否则由直属经理审批。",
    "duration": "单次出差不超过 15 个自然日。",
}

QUOTES_V10 = {
    **QUOTES_V12,
    "advance": "机票出发日距申请日不少于 5 个自然日。",
    "auto": "单次差旅总额不超过 2000 元、无制度违规且不超过申报预算的，可自动通过；否则由直属经理审批。",
}

SEED_EMPLOYEES = [
    {
        "id": "u-zhangsan",
        "name": "张三",
        "role": "employee",
        "department": "销售部",
        "manager_id": "u-lisi",
        "home_city": "北京",
    },
    {
        "id": "u-lisi",
        "name": "李四",
        "role": "manager",
        "department": "销售部",
        "manager_id": None,
        "home_city": "北京",
    },
    {
        "id": "u-zhaoliu",
        "name": "赵六",
        "role": "manager",
        "department": "采购部",
        "manager_id": None,
        "home_city": "上海",
    },
    {
        "id": "u-wangwu",
        "name": "王五",
        "role": "admin",
        "department": "行政财务部",
        "manager_id": None,
        "home_city": "北京",
    },
]

FAULTS = ["transport_timeout", "no_protocol_hotel", "weather_unavailable"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _rules_for(version: str, quotes: dict[str, str]) -> list[dict]:
    return [
        {
            "id": "RULE-CITY-001",
            "type": "city_tier",
            "condition": {"scope": "all"},
            "value": {"tiers": CITY_TIERS, "fallback_tier": "C"},
            "unit": "city_tier",
            "clause": "POL-1",
            "quote": quotes["city"],
        },
        {
            "id": "RULE-HOTEL-001",
            "type": "hotel_cap",
            "condition": {"level": "employee"},
            "value": {"caps_fen": {"A": 50000, "B": 40000, "C": 30000}},
            "unit": "fen_per_night",
            "clause": "POL-2",
            "quote": quotes["hotel_employee"],
        },
        {
            "id": "RULE-HOTEL-002",
            "type": "hotel_cap",
            "condition": {"level": "manager"},
            "value": {"caps_fen": {"A": 65000, "B": 50000, "C": 40000}},
            "unit": "fen_per_night",
            "clause": "POL-2",
            "quote": quotes["hotel_manager"],
        },
        {
            "id": "RULE-MEAL-001",
            "type": "meal_allowance",
            "condition": {"scope": "all"},
            "value": {"caps_fen": {"A": 10000, "B": 8000, "C": 6000}},
            "unit": "fen_per_day",
            "clause": "POL-3",
            "quote": quotes["meal"],
        },
        {
            "id": "RULE-CLASS-001",
            "type": "cabin_class",
            "condition": {"scope": "all"},
            "value": {"allowed": ALLOWED_CABINS},
            "unit": "cabin",
            "clause": "POL-4",
            "quote": quotes["cabin"],
        },
        {
            "id": "RULE-ADVANCE-001",
            "type": "advance_booking_days",
            "condition": {"mode": "air"},
            "value": {"days": 5 if version == "v1.0" else 3},
            "unit": "days",
            "clause": "POL-5",
            "quote": quotes["advance"],
        },
        {
            "id": "RULE-AUTO-001",
            "type": "auto_approve_limit",
            "condition": {"scope": "all"},
            "value": {"value_fen": 200000 if version == "v1.0" else 300000},
            "unit": "fen",
            "clause": "POL-6",
            "quote": quotes["auto"],
        },
        {
            "id": "RULE-DURATION-001",
            "type": "trip_duration_limit",
            "condition": {"scope": "all"},
            "value": {"days": 15},
            "unit": "days",
            "clause": "POL-7",
            "quote": quotes["duration"],
        },
    ]


SEED_POLICIES = [
    {
        "version": "v1.0",
        "status": "archived",
        "effective_date": "2025-01-01",
        "file": "v1.0.md",
        "quotes": QUOTES_V10,
    },
    {
        "version": "v1.2",
        "status": "published",
        "effective_date": "2026-01-01",
        "file": "v1.2.md",
        "quotes": QUOTES_V12,
    },
]


def _document(settings: Settings, filename: str) -> tuple[Path, str, str, str]:
    path = settings.policies_dir / filename
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        stored = str(path.relative_to(settings.root_dir))
    except ValueError:
        stored = str(path)
    return path, text, digest, stored


def locate_line(text: str, quote: str) -> str:
    for number, line in enumerate(text.splitlines(), start=1):
        if quote in line:
            return f"line:{number}"
    raise ValueError(f"rule quote not found in policy document: {quote[:40]}...")


def seed_if_empty(conn: sqlite3.Connection, settings: Settings) -> None:
    count = conn.execute("SELECT COUNT(*) AS n FROM employees").fetchone()["n"]
    if count:
        return
    _seed_all(conn, settings)


def _seed_all(conn: sqlite3.Connection, settings: Settings) -> None:
    now = utc_now()
    # Insert managers before their reports so the manager_id foreign key resolves.
    for employee in sorted(SEED_EMPLOYEES, key=lambda e: e["manager_id"] is not None):
        conn.execute(
            "INSERT INTO employees (id, name, role, department, manager_id, home_city,"
            " preferences_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                employee["id"],
                employee["name"],
                employee["role"],
                employee["department"],
                employee["manager_id"],
                employee["home_city"],
                "{}",
                now,
            ),
        )

    for fault in FAULTS:
        conn.execute(
            "INSERT INTO faults (key, enabled, updated_at) VALUES (?, 0, ?)", (fault, now)
        )

    for policy in SEED_POLICIES:
        _, text, digest, stored = _document(settings, policy["file"])
        conn.execute(
            "INSERT INTO policy_versions (version, status, effective_date, document_path,"
            " document_hash, created_at, published_at) VALUES (?,?,?,?,?,?,?)",
            (
                policy["version"],
                policy["status"],
                policy["effective_date"],
                stored,
                digest,
                now,
                now if policy["status"] == "published" else None,
            ),
        )
        for rule in _rules_for(policy["version"], policy["quotes"]):
            locator = locate_line(text, rule["quote"])
            conn.execute(
                "INSERT INTO policy_rules (version, id, type, condition_json, value_json,"
                " unit, clause, quote, locator, quote_status, status, confirmed_by,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'valid','confirmed',?,?,?)",
                (
                    policy["version"],
                    rule["id"],
                    rule["type"],
                    json.dumps(rule["condition"], ensure_ascii=False),
                    json.dumps(rule["value"], ensure_ascii=False),
                    rule["unit"],
                    rule["clause"],
                    rule["quote"],
                    locator,
                    "u-wangwu",
                    now,
                    now,
                ),
            )

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('seeded_at', ?)", (now,)
    )


def _tracked_files(conn: sqlite3.Connection, settings: Settings) -> list[Path]:
    """Files the app wrote under upload_dir, as recorded in the database.

    Reset removes only these, never the whole directory, so a misconfigured
    TRAVEL_DESK_UPLOAD_DIR cannot wipe unrelated user files (HC-08).
    """
    root = settings.upload_dir.resolve()
    raw = [row["path"] for row in conn.execute("SELECT path FROM uploads")]
    raw += [row["document_path"] for row in conn.execute("SELECT document_path FROM policy_versions")]
    tracked = []
    for value in raw:
        path = Path(value)
        path = (path if path.is_absolute() else settings.root_dir / path).resolve()
        if path.is_relative_to(root) and path != root:
            tracked.append(path)
    return tracked


def reset_demo_data(conn: sqlite3.Connection, settings: Settings) -> None:
    files = _tracked_files(conn, settings)
    # The append-only triggers on audit_events/outbox_emails allow deletion only
    # while this flag is set; deleting `meta` last clears it in the same transaction.
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('reset_in_progress', '1')")
    for table in [
        "idempotency_keys",
        "approvals",
        "outbox_emails",
        "audit_events",
        "image_extractions",
        "uploads",
        "itineraries",
        "trips",
        "faults",
        "policy_rules",
        "policy_versions",
        "employees",
        "meta",
    ]:
        conn.execute(f"DELETE FROM {table}")
    for path in files:
        path.unlink(missing_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    _seed_all(conn, settings)
