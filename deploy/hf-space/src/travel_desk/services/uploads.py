"""Uploads and invitation-image extraction (#9–#11, REQ-003).

Files stay local (`upload_dir/<id>.<ext>`); the type is decided by magic bytes,
never by the client's filename or Content-Type. An extraction is a pending
*proposal*: it does not touch the Trip until the owner confirms it, possibly
after editing, and only the confirmed values are written (origin `image`).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session
from travel_desk.domain.slots import field_problem, normalize_value
from travel_desk.domain.states import TripStatus
from travel_desk.llm.dashscope import ModelUnavailable
from travel_desk.llm.vision import INVITATION_FIELDS, InvitationExtractor
from travel_desk.services import audit
from travel_desk.services.conversation import model_error_payload, record_model_error
from travel_desk.services.errors import ServiceError, invalid_state, require_revision
from travel_desk.services.trips import apply_slot_changes, check_editable, check_slot_changes, owned_trip

_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png", "png"), (b"\xff\xd8\xff", "image/jpeg", "jpg"))


def sniff_image(data: bytes) -> tuple[str, str] | None:
    for magic, content_type, ext in _MAGIC:
        if data.startswith(magic):
            return content_type, ext
    return None


def save_upload(settings: Settings, actor: dict[str, Any], *, filename: str | None, data: bytes,
                trace_id: str) -> dict[str, Any]:
    """`data` holds at most max_upload_bytes + 1 bytes (the route reads no further)."""
    if len(data) > settings.max_upload_bytes:
        raise ServiceError(413, "PAYLOAD_TOO_LARGE", "文件超过 10 MiB 上限",
                           {"max_bytes": settings.max_upload_bytes})
    sniffed = sniff_image(data)
    if sniffed is None:
        raise ServiceError(415, "UNSUPPORTED_MEDIA_TYPE", "只支持 PNG 或 JPG 图片（按文件内容判定）",
                           {"allowed": ["image/png", "image/jpeg"]})
    if actor["role"] not in ("employee", "manager"):
        raise ServiceError(403, "FORBIDDEN", "管理员不能上传邀请图片", {"role": actor["role"]})
    content_type, ext = sniffed
    upload_id = str(uuid.uuid4())
    sha256 = hashlib.sha256(data).hexdigest()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    path = settings.upload_dir / f"{upload_id}.{ext}"
    path.write_bytes(data)
    safe_name = (filename or f"upload.{ext}")[-200:]
    try:
        with db_session(settings.db_path) as conn:
            conn.execute("INSERT INTO uploads (id, filename, content_type, path, sha256, size_bytes,"
                         " uploaded_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (upload_id, safe_name, content_type, str(path), sha256, len(data), actor["id"],
                          audit.now_ts()))
            audit.record(conn, trip_id=None, revision=None, actor=actor["id"], event_type="file_uploaded",
                         payload={"upload_id": upload_id, "sha256": sha256, "size_bytes": len(data),
                                  "content_type": content_type}, trace_id=trace_id)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"id": upload_id, "filename": safe_name, "sha256": sha256, "size_bytes": len(data),
            "content_type": content_type}


def _extraction_view(row) -> dict[str, Any]:
    result = json.loads(row["result_json"])
    return {"id": row["id"], "trip_id": row["trip_id"], "upload_id": row["upload_id"],
            "status": row["status"],
            "fields": result["fields"], "source": "model", "generator": result["generator"],
            "applied": json.loads(row["applied_json"]) if row["applied_json"] else None,
            "created_at": row["created_at"], "confirmed_at": row["confirmed_at"]}


async def extract_invitation(settings: Settings, extractor: InvitationExtractor, actor: dict[str, Any],
                             trip_id: str, body: Any, *, trace_id: str) -> dict[str, Any]:
    if not isinstance(body, dict) or not isinstance(body.get("upload_id"), str) or not body["upload_id"]:
        raise ServiceError(422, "VALIDATION_ERROR", "upload_id 必须是非空字符串",
                           {"errors": [{"loc": ["body", "upload_id"], "type": "string_type",
                                        "msg": "required"}]})
    upload_id = body["upload_id"]
    with read_session(settings.db_path) as conn:
        trip, _ = owned_trip(conn, trip_id, actor)
        upload = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if upload is None or upload["uploaded_by"] != actor["id"]:
            raise ServiceError(404, "UPLOAD_NOT_FOUND", "上传文件不存在或不属于当前用户",
                               {"upload_id": upload_id})
        if trip["status"] != TripStatus.COLLECTING.value:
            raise invalid_state(trip["status"], [TripStatus.COLLECTING.value])
    try:
        image = Path(upload["path"]).read_bytes()
    except OSError:
        raise ServiceError(404, "UPLOAD_NOT_FOUND", "上传文件已不存在", {"upload_id": upload_id}) from None
    try:
        result = await extractor.extract(image, upload["content_type"], upload["sha256"])
    except ModelUnavailable as exc:
        record_model_error(settings, trip_id, actor["id"], exc, stage="image_extraction", trace_id=trace_id)
        payload = model_error_payload(exc)
        raise ServiceError(503, "MODEL_UNAVAILABLE", payload["message"],
                           {**payload["details"], "retryable": exc.retryable}) from None
    extraction_id = str(uuid.uuid4())
    with db_session(settings.db_path) as conn:
        conn.execute("INSERT INTO image_extractions (id, trip_id, upload_id, status, result_json, created_at)"
                     " VALUES (?,?,?,?,?,?)",
                     (extraction_id, trip_id, upload_id, "pending",
                      json.dumps({"fields": result.fields, "generator": result.generator},
                                 ensure_ascii=False),
                      audit.now_ts()))
        audit.record(conn, trip_id=trip_id, revision=trip["revision"], actor=actor["id"],
                     event_type="image_extracted",
                     payload={"extraction_id": extraction_id, "upload_id": upload_id,
                              "sha256": upload["sha256"],
                              "generator": result.generator, "fields": result.fields}, trace_id=trace_id)
        row = conn.execute("SELECT * FROM image_extractions WHERE id = ?", (extraction_id,)).fetchone()
        return _extraction_view(row)


def _as_written(name: str, value: Any) -> Any:
    """How a proposed value would be stored, to tell real edits from normalisation."""
    if value is None or field_problem(name, value):
        return value
    return normalize_value(name, value)


def _check_fields(fields: Any) -> dict[str, Any]:
    if not isinstance(fields, dict):
        raise ServiceError(422, "INVALID_SLOT", "fields 必须是对象", {"invalid_fields": []})
    unknown = [{"field": name, "code": "UNKNOWN_FIELD", "message": "邀请函只包含城市、日期、事由和地址"}
               for name in fields if name not in INVITATION_FIELDS]
    if unknown:
        raise ServiceError(422, "INVALID_SLOT", "槽位格式不合法", {"invalid_fields": unknown})
    present = {name: value for name, value in fields.items() if value is not None}
    if not present:
        raise ServiceError(422, "INVALID_SLOT", "没有可写入的字段", {"invalid_fields": []})
    return check_slot_changes(present)


def confirm_extraction(settings: Settings, actor: dict[str, Any], trip_id: str, extraction_id: str, body: Any,
                       *, trace_id: str) -> dict[str, Any]:
    """Write the confirmed (possibly edited) values. A null field is left untouched, not cleared."""
    if not isinstance(body, dict):
        raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
    expected = require_revision(body.get("expected_revision"), "expected_revision")
    edited = body.get("fields")
    normalized = _check_fields(edited) if edited is not None else None
    with db_session(settings.db_path) as conn:
        trip, _ = owned_trip(conn, trip_id, actor)
        row = conn.execute("SELECT * FROM image_extractions WHERE id = ? AND trip_id = ?",
                           (extraction_id, trip_id)).fetchone()
        if row is None:
            raise ServiceError(404, "EXTRACTION_NOT_FOUND", "图片识别结果不存在",
                               {"extraction_id": extraction_id})
        if row["status"] != "pending":
            raise ServiceError(409, "EXTRACTION_NOT_PENDING", "该识别结果已处理", {"status": row["status"]})
        check_editable(trip, expected)
        proposed = json.loads(row["result_json"])["fields"]
        if normalized is None:
            normalized = _check_fields(proposed)
        edited_fields = sorted(name for name in INVITATION_FIELDS
                               if normalized.get(name) != _as_written(name, proposed.get(name)))
        trip, diff = apply_slot_changes(conn, trip, actor_id=actor["id"], normalized=normalized,
                                        trace_id=trace_id, origin="image",
                                        extra={"extraction_id": extraction_id})
        conn.execute("UPDATE image_extractions SET status = 'confirmed', applied_json = ?, confirmed_at = ?,"
                     " confirmed_by = ? WHERE id = ? AND status = 'pending'",
                     (json.dumps(normalized, ensure_ascii=False), audit.now_ts(), actor["id"], extraction_id))
        audit.record(conn, trip_id=trip_id, revision=trip["revision"], actor=actor["id"],
                     event_type="image_confirmed",
                     payload={"extraction_id": extraction_id, "applied": normalized,
                              "edited_fields": edited_fields, "changed_fields": [c["field"] for c in diff]},
                     trace_id=trace_id)
        return trip
