"""Policy versions: upload, rule extraction, review and publication (#19–#23, REQ-005～008).

Error order follows docs/api.md §1.3: 401 → 400 (missing Idempotency-Key, unknown
rule status) → 422 format → 413/415 (upload payload, same position as #9) →
404 (a draft is invisible to non-admins) → 403 non-admin → idempotency replay →
409 state → 422 business.

Publication is one transaction: the draft becomes `published` and the previous
published version `archived` (the unique partial index guarantees one published
version at any instant). Every write is audited with `trip_id=null`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session
from travel_desk.domain.policy import CLOSED_RULE_TYPES, PolicyRuleError, RuleSet, validate_rule_shape
from travel_desk.llm.dashscope import ModelUnavailable
from travel_desk.policy import documents
from travel_desk.policy.extraction import CANONICAL_UNITS, RuleExtractor, compile_candidates
from travel_desk.services import audit, idempotency
from travel_desk.services.errors import ServiceError
from travel_desk.services.trips import dumps

VERSION_RE = re.compile(r"^v\d{1,3}(?:\.\d{1,3}){0,3}(?:-[a-z0-9]{1,16})?$")
RULE_STATUSES = ("confirmed", "rejected", "unsupported")
PATCH_FIELDS = {"status", "value", "condition", "modification_reason"}
MAX_REASON = 500
PUBLISH_ROUTE = "/api/policies/{version}/publish"
# Policy writes are rare; serializing them in-process keeps check-then-write sequences
# (version exists? / which version is published?) free of races without IMMEDIATE transactions.
_WRITE_LOCK = threading.Lock()


def _format_error(field: str, msg: str, message: str) -> ServiceError:
    return ServiceError(422, "VALIDATION_ERROR", message,
                        {"errors": [{"loc": ["body", field], "type": "value_error", "msg": msg}]})


def _not_found(version: str) -> ServiceError:
    return ServiceError(404, "POLICY_VERSION_NOT_FOUND", "制度版本不存在", {"version": version})


def _require_admin(actor: dict[str, Any]) -> None:
    if actor["role"] != "admin":
        raise ServiceError(403, "FORBIDDEN", "该操作仅限管理员", {"required_role": "admin"})


def _not_draft(row: sqlite3.Row) -> ServiceError:
    return ServiceError(409, "POLICY_NOT_DRAFT", "只有 draft 版本可以执行该操作",
                        {"version": row["version"], "status": row["status"]})


def visible_version(conn: sqlite3.Connection, version: str, actor: dict[str, Any]) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM policy_versions WHERE version = ?", (version,)).fetchone()
    if row is None or (row["status"] == "draft" and actor["role"] != "admin"):
        raise _not_found(version)
    return row


def _rules(conn: sqlite3.Connection, version: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM policy_rules WHERE version = ? ORDER BY rowid", (version,)).fetchall()
    rules = []
    for row in rows:
        item = dict(row)
        item["condition"] = json.loads(item.pop("condition_json"))
        item["value"] = json.loads(item.pop("value_json"))
        rules.append(item)
    return rules


def rule_problems(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Why this rule could not be confirmed as it stands ([] = it can)."""
    problems = []
    if rule["quote_status"] != "valid":
        problems.append({"code": "INVALID_QUOTE", "message": "原文引用在规范化后的制度全文中找不到"})
    if rule["type"] not in CLOSED_RULE_TYPES:
        problems.append({"code": "UNSUPPORTED_RULE_TYPE", "message": f"规则引擎不支持类型 {rule['type']}"})
        return problems
    problems.extend(validate_rule_shape(rule["type"], rule["condition"], rule["value"]))
    expected = CANONICAL_UNITS[rule["type"]]
    if rule["unit"] != expected:
        problems.append({"code": "UNIT_MISMATCH", "expected": expected, "actual": rule["unit"],
                         "message": f"{rule['type']} 的单位应为 {expected}"})
    return problems


def rule_view(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rule["id"], "version": rule["version"], "type": rule["type"],
        "supported": rule["type"] in CLOSED_RULE_TYPES,
        "condition": rule["condition"], "value": rule["value"], "unit": rule["unit"],
        "clause": rule["clause"], "quote": rule["quote"], "locator": rule["locator"] or None,
        "quote_status": rule["quote_status"], "status": rule["status"], "problems": rule_problems(rule),
        "confirmed_by": rule["confirmed_by"], "modification_reason": rule["modification_reason"],
        "created_at": rule["created_at"], "updated_at": rule["updated_at"], "source": "policy",
    }


def _counts(rules: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in ("candidate", "confirmed", "rejected", "unsupported")}
    for rule in rules:
        counts[rule["status"]] += 1
    counts["invalid_quote"] = sum(1 for r in rules if r["quote_status"] != "valid")
    return counts


def version_view(conn: sqlite3.Connection, row: sqlite3.Row, *, include_rules: bool,
                 admin: bool) -> dict[str, Any]:
    rules = _rules(conn, row["version"])
    view = {
        "version": row["version"], "status": row["status"], "effective_date": row["effective_date"],
        "document": {"kind": documents.kind_of_path(row["document_path"]), "sha256": row["document_hash"]},
        "created_at": row["created_at"], "published_at": row["published_at"],
    }
    if admin:
        view["rule_counts"] = _counts(rules)
    if include_rules:
        shown = rules if admin else [r for r in rules if r["status"] == "confirmed"]
        view["rules"] = [rule_view(r) for r in shown]
    return view


# ------------------------------------------------------------------ #19 read


def list_policies(settings: Settings, actor: dict[str, Any]) -> dict[str, Any]:
    admin = actor["role"] == "admin"
    with read_session(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM policy_versions ORDER BY created_at, version").fetchall()
        return {"policies": [version_view(conn, row, include_rules=False, admin=admin)
                             for row in rows if admin or row["status"] != "draft"]}


def get_policy(settings: Settings, actor: dict[str, Any], version: str) -> dict[str, Any]:
    with read_session(settings.db_path) as conn:
        row = visible_version(conn, version, actor)
        return version_view(conn, row, include_rules=True, admin=actor["role"] == "admin")


# ------------------------------------------------------------------ #20 upload


def check_upload_fields(version: Any, effective_date: Any) -> tuple[str, str]:
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version.strip()):
        raise _format_error("version", "pattern v<major>[.<minor>…][-tag]",
                            "version 格式应为 v1.3 或 v9.1-test 这样的版本号")
    if not isinstance(effective_date, str) or len(effective_date.strip()) != 10:
        raise _format_error("effective_date", "YYYY-MM-DD", "effective_date 必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(effective_date.strip())
    except ValueError:
        raise _format_error("effective_date", "YYYY-MM-DD", "effective_date 必须是 YYYY-MM-DD") from None
    return version.strip(), effective_date.strip()


def upload_policy(settings: Settings, actor: dict[str, Any], *, version: Any, effective_date: Any,
                  filename: str | None, data: bytes | None, trace_id: str) -> dict[str, Any]:
    """`data` holds at most max_upload_bytes + 1 bytes (the route reads no further)."""
    version, effective_date = check_upload_fields(version, effective_date)
    if data is None:
        raise _format_error("file", "required", "缺少制度文件 file")
    if len(data) > settings.max_upload_bytes:
        raise ServiceError(413, "PAYLOAD_TOO_LARGE", "文件超过 10 MiB 上限",
                           {"max_bytes": settings.max_upload_bytes})
    try:
        kind = documents.sniff(filename, data)
        document = documents.parse(kind, data)
    except documents.DocumentError as exc:
        raise ServiceError(exc.status_code, exc.code, exc.message, exc.details) from None
    _require_admin(actor)

    digest = hashlib.sha256(data).hexdigest()
    folder = settings.upload_dir / "policies"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{version}-{uuid.uuid4().hex[:12]}.{'pdf' if kind == 'pdf' else 'md'}"
    with _WRITE_LOCK, db_session(settings.db_path) as conn:
        if conn.execute("SELECT 1 FROM policy_versions WHERE version = ?", (version,)).fetchone():
            raise ServiceError(409, "VERSION_EXISTS", "该制度版本号已存在", {"version": version})
        path.write_bytes(data)
        try:
            now = audit.now_ts()
            conn.execute("INSERT INTO policy_versions (version, status, effective_date, document_path,"
                         " document_hash, created_at, published_at) VALUES (?, 'draft', ?, ?, ?, ?, NULL)",
                         (version, effective_date, str(path), digest, now))
            audit.record(conn, trip_id=None, revision=None, actor=actor["id"], event_type="policy_uploaded",
                         payload={"version": version, "effective_date": effective_date, "kind": kind,
                                  "sha256": digest, "size_bytes": len(data),
                                  "normalized_text_sha256": document.normalized_sha256,
                                  "segments": len(document.segments), "pages": document.page_count,
                                  "filename": (filename or "")[-200:] or None},
                         trace_id=trace_id, refs=[{"kind": "policy", "id": version}])
            row = conn.execute("SELECT * FROM policy_versions WHERE version = ?", (version,)).fetchone()
            return version_view(conn, row, include_rules=True, admin=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise


# ------------------------------------------------------------------ #21 extract


def _document_of(settings: Settings, row: sqlite3.Row) -> documents.PolicyDocument:
    path = Path(row["document_path"])
    path = path if path.is_absolute() else settings.root_dir / path
    return documents.load(path, documents.kind_of_path(row["document_path"]))


def _last_generator(conn: sqlite3.Connection, version: str) -> str | None:
    for row in conn.execute("SELECT payload_json FROM audit_events WHERE type = 'policy_extracted'"
                            " AND trip_id IS NULL ORDER BY seq DESC"):
        payload = json.loads(row["payload_json"])
        if payload.get("version") == version:
            return payload.get("generator")
    return None


def _extraction_result(conn: sqlite3.Connection, version: str, *, reused: bool,
                       replaced: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": version, "reused": reused, "replaced": replaced,
            "generator": _last_generator(conn, version),
            "candidates": [rule_view(r) for r in _rules(conn, version)]}


def _summary(rule: dict[str, Any]) -> dict[str, Any]:
    return {"id": rule["id"], "type": rule["type"], "status": rule["status"],
            "quote_status": rule["quote_status"]}


async def extract_rules(settings: Settings, extractor: RuleExtractor, actor: dict[str, Any], version: str,
                        body: Any, *, trace_id: str) -> dict[str, Any]:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
    overwrite = body.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise _format_error("overwrite", "bool", "overwrite 必须是布尔值")
    with read_session(settings.db_path) as conn:
        row = visible_version(conn, version, actor)
        _require_admin(actor)
        if row["status"] != "draft":
            raise _not_draft(row)
        if not overwrite and _rules(conn, version):
            return _extraction_result(conn, version, reused=True, replaced=[])
        document = _document_of(settings, row)

    try:
        extraction = await extractor.extract(document)
        compiled = compile_candidates(document, extraction.candidates)
    except ModelUnavailable as exc:
        with db_session(settings.db_path) as conn:
            audit.record(conn, trip_id=None, revision=None, actor="system", event_type="error",
                         payload={"stage": "policy_extraction", "version": version, "message": exc.reason,
                                  "error_type": exc.error_type, "retryable": exc.retryable},
                         trace_id=trace_id, refs=[{"kind": "policy", "id": version}])
        raise ServiceError(503, "MODEL_UNAVAILABLE", exc.reason,
                           {"retryable": exc.retryable, "stage": "policy_extraction"}) from None

    with _WRITE_LOCK, db_session(settings.db_path) as conn:
        row = visible_version(conn, version, actor)
        if row["status"] != "draft":
            raise _not_draft(row)
        existing = _rules(conn, version)
        if existing and not overwrite:  # another request finished first: keep its result
            return _extraction_result(conn, version, reused=True, replaced=[])
        replaced = [_summary(r) for r in existing]
        conn.execute("DELETE FROM policy_rules WHERE version = ?", (version,))
        now = audit.now_ts()
        for rule in compiled:
            conn.execute(
                "INSERT INTO policy_rules (version, id, type, condition_json, value_json, unit, clause,"
                " quote, locator, quote_status, status, confirmed_by, modification_reason, created_at,"
                " updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,?,?)",
                (version, rule["id"], rule["type"], dumps(rule["condition"]), dumps(rule["value"]),
                 rule["unit"], rule["clause"], rule["quote"], rule["locator"], rule["quote_status"],
                 rule["status"], now, now))
        audit.record(conn, trip_id=None, revision=None, actor=actor["id"], event_type="policy_extracted",
                     payload={"version": version, "generator": extraction.generator, "overwrite": overwrite,
                              "replaced": replaced, "candidates": [_summary(r) for r in compiled],
                              "counts": _counts(compiled)},
                     trace_id=trace_id, refs=[{"kind": "policy", "id": version}])
        return _extraction_result(conn, version, reused=False, replaced=replaced)


# ------------------------------------------------------------------ #22 review


def check_rule_patch(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
    status = body.get("status")
    if status is not None and (not isinstance(status, str) or status not in RULE_STATUSES):
        raise ServiceError(400, "INVALID_RULE_STATUS", "status 只能是 confirmed、rejected 或 unsupported",
                           {"allowed": list(RULE_STATUSES)})
    unknown = sorted(set(body) - PATCH_FIELDS)
    if unknown:
        raise ServiceError(422, "VALIDATION_ERROR",
                           "只允许修改 status、value、condition 和 modification_reason",
                           {"errors": [{"loc": ["body", name], "type": "extra_forbidden",
                                        "msg": "not allowed"} for name in unknown]})
    if status is None:
        raise _format_error("status", "required", "缺少 status")
    for name in ("value", "condition"):
        if name in body and not isinstance(body[name], dict):
            raise _format_error(name, "object", f"{name} 必须是 JSON 对象")
    reason = body.get("modification_reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > MAX_REASON):
        raise _format_error("modification_reason", f"string <= {MAX_REASON}",
                            f"modification_reason 必须是不超过 {MAX_REASON} 字的文本")
    return body


def update_rule(settings: Settings, actor: dict[str, Any], version: str, rule_id: str, body: Any, *,
                trace_id: str) -> dict[str, Any]:
    body = check_rule_patch(body)
    with _WRITE_LOCK, db_session(settings.db_path) as conn:
        row = visible_version(conn, version, actor)
        rule = next((r for r in _rules(conn, version) if r["id"] == rule_id), None)
        if rule is None:
            raise ServiceError(404, "RULE_NOT_FOUND", "规则不存在", {"version": version, "rule_id": rule_id})
        _require_admin(actor)
        if row["status"] != "draft":
            raise _not_draft(row)

        after = {**rule, "status": body["status"]}
        changed_fields = [name for name in ("value", "condition")
                          if name in body and body[name] != rule[name]]
        for name in changed_fields:
            after[name] = body[name]
        reason = (body.get("modification_reason") or "").strip()
        if changed_fields and not reason:
            raise ServiceError(422, "MODIFICATION_REASON_REQUIRED", "修改了规则数值或条件，必须填写修改理由",
                               {"modified_fields": changed_fields})
        if after["status"] == "confirmed":
            if rule["quote_status"] != "valid":
                raise ServiceError(422, "QUOTE_INVALID", "原文引用无效的候选不能确认，只能拒绝或标记不支持",
                                   {"rule_id": rule_id, "quote": rule["quote"]})
            if rule["type"] not in CLOSED_RULE_TYPES:
                raise ServiceError(422, "UNSUPPORTED_TYPE", "规则引擎不支持该类型，不能确认",
                                   {"rule_id": rule_id, "type": rule["type"],
                                    "supported_types": list(CLOSED_RULE_TYPES)})
            problems = rule_problems(after)
            if problems:
                raise ServiceError(422, "INVALID_RULE_SHAPE", "规则的条件、数值或单位不符合该类型的结构",
                                   {"rule_id": rule_id, "problems": problems})
        if changed_fields:
            after["modification_reason"] = reason
        after["confirmed_by"] = actor["id"] if after["status"] == "confirmed" else None
        after["updated_at"] = audit.now_ts()
        conn.execute("UPDATE policy_rules SET status = ?, value_json = ?, condition_json = ?,"
                     " confirmed_by = ?, modification_reason = ?, updated_at = ?"
                     " WHERE version = ? AND id = ?",
                     (after["status"], dumps(after["value"]), dumps(after["condition"]),
                      after["confirmed_by"], after["modification_reason"], after["updated_at"], version,
                      rule_id))

        def snapshot(r: dict[str, Any]) -> dict[str, Any]:
            return {"status": r["status"], "value": r["value"], "condition": r["condition"]}

        audit.record(conn, trip_id=None, revision=None, actor=actor["id"], event_type="policy_rule_updated",
                     payload={"version": version, "rule_id": rule_id, "before": snapshot(rule),
                              "after": snapshot(after), "modified_fields": changed_fields,
                              "modification_reason": reason or None, "quote": rule["quote"],
                              "locator": rule["locator"] or None},
                     trace_id=trace_id, refs=[{"kind": "rule", "id": rule_id, "version": version}])
        return rule_view(after)


# ------------------------------------------------------------------ #23 publish


def publish(settings: Settings, actor: dict[str, Any], version: str, *, idempotency_key: str | None,
            trace_id: str) -> dict[str, Any]:
    key = idempotency.require_key(idempotency_key)
    request = idempotency.make_request("POST", PUBLISH_ROUTE, {"version": version}, actor["id"], key, {})
    with _WRITE_LOCK, db_session(settings.db_path) as conn:
        row = visible_version(conn, version, actor)
        _require_admin(actor)
        stored = idempotency.lookup(conn, request)
        if stored is not None:
            return stored.body
        if row["status"] != "draft":
            raise _not_draft(row)
        rules = _rules(conn, version)
        pending = [r["id"] for r in rules if r["status"] == "candidate"]
        if pending:
            raise ServiceError(409, "UNPROCESSED_CANDIDATES", "还有未处理的候选规则，不能发布",
                               {"rule_ids": pending})
        try:
            RuleSet.from_rules(version, rules)
        except PolicyRuleError as exc:
            missing = sorted({p["type"] for p in exc.problems if p["code"] == "MISSING_RULE_TYPE"})
            if missing:
                raise ServiceError(422, "MISSING_RULE_TYPES", "封闭规则类型缺少已确认规则，不能发布",
                                   {"missing_types": missing, "problems": exc.problems}) from None
            raise ServiceError(422, "RULE_SET_INVALID", "已确认规则无法组成可用的规则集，不能发布",
                               {"problems": exc.problems}) from None

        previous = conn.execute("SELECT version FROM policy_versions WHERE status = 'published'").fetchone()
        now = audit.now_ts()
        if previous is not None:
            conn.execute("UPDATE policy_versions SET status = 'archived' WHERE version = ?",
                         (previous["version"],))
        conn.execute("UPDATE policy_versions SET status = 'published', published_at = ? WHERE version = ?",
                     (now, version))
        confirmed = [r["id"] for r in rules if r["status"] == "confirmed"]
        archived = previous["version"] if previous else None
        audit.record(conn, trip_id=None, revision=None, actor=actor["id"], event_type="policy_published",
                     payload={"version": version, "archived": archived, "confirmed_rule_ids": confirmed,
                              "counts": _counts(rules)},
                     trace_id=trace_id, refs=[{"kind": "policy", "id": version}]
                     + ([{"kind": "policy", "id": archived}] if archived else []))
        row = conn.execute("SELECT * FROM policy_versions WHERE version = ?", (version,)).fetchone()
        response = {"policy": version_view(conn, row, include_rules=False, admin=True),
                    "archived_version": archived, "confirmed_rule_ids": confirmed}
        idempotency.store(conn, request, 200, response)
        return response
