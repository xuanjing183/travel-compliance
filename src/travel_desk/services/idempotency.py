"""Idempotency-Key handling (docs/api.md §1.4, HC-12).

Scope = method + route template + path params + actor; fingerprint = SHA-256 of
the canonical JSON body. The key row is written in the same transaction as the
business write, so a replay can never observe a half-applied request, and a
request that failed with 4xx leaves no key behind.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from travel_desk.services.audit import now_ts
from travel_desk.services.errors import ServiceError


@dataclass(frozen=True)
class IdemRequest:
    scope: str
    key: str
    request_hash: str


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: dict[str, Any]


def fingerprint(body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_request(method: str, route: str, path_params: dict[str, str], actor_id: str, key: str,
                 body: Any) -> IdemRequest:
    params = ",".join(f"{name}={path_params[name]}" for name in sorted(path_params))
    return IdemRequest(scope=f"{method} {route} [{params}] actor={actor_id}", key=key,
                       request_hash=fingerprint(body))


def require_key(key: str | None) -> str:
    if not key or not key.strip():
        raise ServiceError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key 请求头", {})
    if len(key) > 200:
        raise ServiceError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key 过长（最多 200 字符）", {})
    return key


def lookup(conn: sqlite3.Connection, request: IdemRequest) -> StoredResponse | None:
    row = conn.execute("SELECT request_hash, status_code, response_json FROM idempotency_keys"
                       " WHERE scope = ? AND key = ?", (request.scope, request.key)).fetchone()
    if row is None:
        return None
    if row["request_hash"] != request.request_hash:
        raise ServiceError(422, "IDEMPOTENCY_KEY_REUSED", "同一 Idempotency-Key 对应了不同的请求内容", {})
    return StoredResponse(row["status_code"], json.loads(row["response_json"]))


def store(conn: sqlite3.Connection, request: IdemRequest, status_code: int, body: dict[str, Any]) -> None:
    conn.execute("INSERT INTO idempotency_keys (scope, key, request_hash, status_code, response_json,"
                 " created_at) VALUES (?,?,?,?,?,?)",
                 (request.scope, request.key, request.request_hash, status_code,
                  json.dumps(body, ensure_ascii=False), now_ts()))


def finalize(conn: sqlite3.Connection, request: IdemRequest, status_code: int, body: dict[str, Any]) -> None:
    """Replace the provisional response stored with the business write by the final one."""
    conn.execute("UPDATE idempotency_keys SET status_code = ?, response_json = ? WHERE scope = ? AND key = ?",
                 (status_code, json.dumps(body, ensure_ascii=False), request.scope, request.key))
