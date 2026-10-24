"""Server-side visibility rules (§6.4). Roles come from the database, never from the client."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def load_employee(conn: sqlite3.Connection, employee_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT id, name, role, department, manager_id, home_city, preferences_json"
                       " FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["preferences"] = json.loads(item.pop("preferences_json") or "{}")
    return item


def can_view(actor: dict[str, Any], owner: dict[str, Any]) -> bool:
    """Admin sees every trip; others see their own; a manager also sees direct reports' trips."""
    if actor["role"] == "admin" or actor["id"] == owner["id"]:
        return True
    return actor["role"] == "manager" and owner.get("manager_id") == actor["id"]
