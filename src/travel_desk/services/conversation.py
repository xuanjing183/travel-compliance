"""Conversation turns: free text → slot extraction → validated write → ask (#3, #6; REQ-001/002).

The extractor (fake parser, recorded output or real model) only *proposes*
values. Every proposed value passes the same format check as `PATCH /slots`;
valid ones are written (audit `slots_changed`, origin `message`), invalid ones
are not written and are asked about again (D-14).

The assistant's reply is composed by code from the resulting slot state
(IMP-43): it asks exactly for the missing and rejected fields and never for a
field that is already filled, so "只追问缺失或非法项" and "不重复追问" hold by
construction rather than by prompt. Its `generator` is therefore `template`.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session
from travel_desk.domain.money import format_yuan
from travel_desk.domain.slots import REQUIRED_FIELDS, SLOT_FIELDS, field_problem, normalize_value
from travel_desk.domain.states import EDITABLE_STATES, TripStatus
from travel_desk.llm.dashscope import ModelUnavailable
from travel_desk.llm.slots import MAX_MESSAGE_CHARS, SlotExtractor
from travel_desk.services import audit, idempotency
from travel_desk.services.errors import ServiceError, invalid_state, require_revision, stale_revision
from travel_desk.services.trips import (
    apply_slot_changes,
    check_editable,
    editable_states,
    load_trip,
    owned_trip,
    slot_state,
)
from travel_desk.workflow import events
from travel_desk.workflow.engine import SubmitRun, Workflow

logger = logging.getLogger("travel_desk.conversation")

MESSAGES_ROUTE = "/api/trips/{trip_id}/messages"

FIELD_LABELS = {
    "origin": "出发城市", "destination": "目的城市", "start_date": "出发日期", "end_date": "返回日期",
    "purpose": "出差事由", "declared_budget_fen": "申报预算", "meeting_address": "会议地址",
    "preferences": "出行偏好",
}
_ASK_HINTS = {
    "start_date": "出发日期（具体到日，如 10月28日）", "end_date": "返回日期（具体到日）",
    "declared_budget_fen": "申报预算（元）",
}


def check_text(value: Any) -> str:
    """422 format check of the message text."""
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(422, "VALIDATION_ERROR", "text 必须是非空文本",
                           {"errors": [{"loc": ["body", "text"], "type": "string_type", "msg": "non-empty"}]})
    if len(value) > MAX_MESSAGE_CHARS:
        raise ServiceError(422, "VALIDATION_ERROR", f"text 最多 {MAX_MESSAGE_CHARS} 字",
                           {"errors": [{"loc": ["body", "text"], "type": "string_too_long",
                                        "msg": f"max {MAX_MESSAGE_CHARS}"}]})
    return value.strip()


def _display(name: str, value: Any) -> str:
    if name == "declared_budget_fen":
        return format_yuan(value)
    if name == "preferences":
        return "回避早班" if value.get("avoid_early_departure") else "已记录"
    return str(value)


def compose_ask(values: dict[str, Any], missing: list[str], rejected: list[dict[str, Any]],
                changes: list[dict[str, Any]]) -> str:
    parts = []
    if changes:
        recorded = "，".join(f"{FIELD_LABELS[c['field']]} {_display(c['field'], c['after'])}"
                             for c in changes if c["after"] is not None)
        if recorded:
            parts.append(f"已记录：{recorded}。")
    if rejected:
        parts.append("以下信息无法识别，请重新提供：" + "；".join(
            f"{FIELD_LABELS.get(r['field'], r['field'])}（{r['message']}）" for r in rejected) + "。")
    rejected_names = {r["field"] for r in rejected}
    still = [name for name in missing if name not in rejected_names]
    if still:
        asked = "、".join(_ASK_HINTS.get(name, FIELD_LABELS[name]) for name in still)
        parts.append(f"还需要：{asked}。")
    if not missing and not rejected:
        summary = "，".join(f"{FIELD_LABELS[name]} {_display(name, values[name])}"
                           for name in REQUIRED_FIELDS)
        parts.append(f"信息已齐全：{summary}。请核对，确认无误后点击“提交”。")
    return "".join(parts) or "没有识别到差旅信息，请描述出发城市、目的城市、日期、事由和预算。"


def screen(fields: dict[str, Any], current: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Split proposed values into (normalized accepted, rejected with reason). Unknown keys are dropped."""
    accepted: dict[str, Any] = {}
    rejected: list[dict[str, Any]] = []
    for name, value in fields.items():
        if name not in SLOT_FIELDS or value is None:
            continue
        if name == "preferences" and isinstance(value, dict):
            value = {**(current.get("preferences") or {}), **value}
        problem = field_problem(name, value)
        if problem:
            rejected.append({**problem, "value": value})
        else:
            accepted[name] = normalize_value(name, value)
    return accepted, rejected


@dataclass(frozen=True)
class Turn:
    trip: dict[str, Any]
    previous_status: str
    payload: dict[str, Any]
    ask: str


def apply_turn(settings: Settings, actor: dict[str, Any], trip_id: str, *, expected: int,
               extraction_fields: dict[str, Any], generator: str, trace_id: str,
               idem: idempotency.IdemRequest | None = None) -> Turn:
    """One transaction: write accepted values, audit the turn, store the idempotency key."""
    with db_session(settings.db_path) as conn:
        trip, _ = owned_trip(conn, trip_id, actor)
        check_editable(trip, expected)
        previous = trip["status"]
        accepted, rejected = screen(extraction_fields, trip["slots"] or {})
        trip, diff = apply_slot_changes(conn, trip, actor_id=actor["id"], normalized=accepted,
                                        trace_id=trace_id, origin="message", extra={"generator": generator})
        state = slot_state(trip)
        ask = compose_ask(state.values, state.missing_fields, rejected, diff)
        payload = {"slots": state.values, "missing_fields": state.missing_fields,
                   "invalid_fields": [*state.invalid_fields, *rejected], "changes": diff, "ask": ask,
                   "generator": generator, "source": "model"}
        audit.record(conn, trip_id=trip_id, revision=trip["revision"], actor=actor["id"],
                     event_type="message_processed",
                     payload={"generator": generator, "proposed_fields": sorted(extraction_fields),
                              "changed_fields": [c["field"] for c in diff],
                              "rejected": [{"field": r["field"], "code": r["code"]} for r in rejected],
                              "missing_fields": state.missing_fields, "ask": ask},
                     trace_id=trace_id)
        if idem is not None:
            idempotency.store(conn, idem, 200, {"trip_id": trip_id, "revision": trip["revision"],
                                                "status": trip["status"]})
        return Turn(trip=trip, previous_status=previous, payload=payload, ask=ask)


def record_model_error(settings: Settings, trip_id: str, actor_id: str, exc: ModelUnavailable, *,
                       stage: str, trace_id: str) -> int:
    with db_session(settings.db_path) as conn:
        trip = load_trip(conn, trip_id)
        revision = trip["revision"] if trip else 0
        audit.record(conn, trip_id=trip_id, revision=revision, actor=actor_id, event_type="error",
                     payload={"stage": stage, "code": "MODEL_UNAVAILABLE", "reason": exc.reason,
                              "error_type": exc.error_type, "retryable": exc.retryable},
                     trace_id=trace_id)
        return revision


def model_error_payload(exc: ModelUnavailable) -> dict[str, Any]:
    return {"code": "MODEL_UNAVAILABLE", "message": exc.reason, "retryable": exc.retryable,
            "details": {"error_type": exc.error_type} if exc.error_type else {}}


# ------------------------------------------------------------------ #6 messages (SSE)


async def post_message(workflow: Workflow, extractor: SlotExtractor, trip_id: str, actor: dict[str, Any],
                       body: Any, *, idempotency_key: str | None, trace_id: str) -> SubmitRun:
    settings = workflow.settings
    key = idempotency.require_key(idempotency_key) if idempotency_key is not None else None
    if not isinstance(body, dict):
        raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
    text = check_text(body.get("text"))
    expected = require_revision(body.get("expected_revision"), "expected_revision")
    lock = workflow.lock(trip_id)
    await lock.acquire()
    handed_over = False
    try:
        with read_session(settings.db_path) as conn:
            trip, _ = owned_trip(conn, trip_id, actor)
            req = None
            if key is not None:
                req = idempotency.make_request("POST", MESSAGES_ROUTE, {"trip_id": trip_id}, actor["id"],
                                               key, body)
                if idempotency.lookup(conn, req) is not None:
                    replay = events.trip_status(trip, previous=None, trace_id=trace_id, replayed=True)
                    return SubmitRun([replay, events.done(trip, trace_id=trace_id)])
            if expected != trip["revision"]:
                raise stale_revision(expected, trip["revision"])
            if TripStatus(trip["status"]) not in EDITABLE_STATES:
                raise invalid_state(trip["status"], editable_states())
        first = [events.stage(trip, "extracting", "正在识别差旅信息…", trace_id=trace_id)]
        queue: asyncio.Queue = asyncio.Queue()
        task = workflow.spawn(_message_run(settings, extractor, trip, actor, text, expected, req, trace_id,
                                           queue, lock))
        handed_over = True
        return SubmitRun(first, queue, task)
    finally:
        if not handed_over:
            lock.release()


async def _message_run(settings: Settings, extractor: SlotExtractor, trip: dict[str, Any],
                       actor: dict[str, Any], text: str, expected: int, req, trace_id: str,
                       queue: asyncio.Queue, lock: asyncio.Lock) -> None:
    trip_id = trip["id"]
    try:
        try:
            extraction = await extractor.extract(text, settings.demo_today)
        except ModelUnavailable as exc:
            revision = record_model_error(settings, trip_id, actor["id"], exc, stage="slot_extraction",
                                          trace_id=trace_id)
            payload = model_error_payload(exc)
            await queue.put(events.error(trip_id, revision, code=payload["code"], message=payload["message"],
                                         details=payload["details"], retryable=payload["retryable"],
                                         trace_id=trace_id))
            return
        turn = apply_turn(settings, actor, trip_id, expected=expected, extraction_fields=extraction.fields,
                          generator=extraction.generator, trace_id=trace_id, idem=req)
        current = turn.trip
        await queue.put(events.make_event("slots", trip_id=trip_id, revision=current["revision"],
                                          trace_id=trace_id, payload=turn.payload))
        if current["status"] != turn.previous_status:
            await queue.put(events.trip_status(current, previous=turn.previous_status, trace_id=trace_id))
        await queue.put(events.make_event("token", trip_id=trip_id, revision=current["revision"],
                                          trace_id=trace_id,
                                          payload={"text": turn.ask, "generator": "template"}))
        await queue.put(events.done(current, trace_id=trace_id))
    except ServiceError as exc:
        await queue.put(events.error(trip_id, trip["revision"], code=exc.code, message=exc.message,
                                     details=exc.details, retryable=False, trace_id=trace_id))
    except Exception as exc:  # noqa: BLE001 - exactly one terminal event per stream
        logger.warning("message run failed trip=%s type=%s trace_id=%s", trip_id, type(exc).__name__,
                       trace_id)
        await queue.put(events.error(trip_id, trip["revision"], code="INTERNAL_ERROR",
                                     message="处理消息时出现意外错误，申请未被修改，可重试",
                                     details={"error_type": type(exc).__name__}, retryable=True,
                                     trace_id=trace_id))
    finally:
        await queue.put(None)
        lock.release()


# ------------------------------------------------------------------ #3 create with text


async def first_turn(workflow: Workflow, extractor: SlotExtractor, trip: dict[str, Any],
                     actor: dict[str, Any], text: str, *, trace_id: str) -> dict[str, Any]:
    """Synchronous extraction for `POST /trips {text}`; the trip already exists either way."""
    settings = workflow.settings
    async with workflow.lock(trip["id"]):
        try:
            extraction = await extractor.extract(text, settings.demo_today)
        except ModelUnavailable as exc:
            record_model_error(settings, trip["id"], actor["id"], exc, stage="slot_extraction",
                               trace_id=trace_id)
            return {"error": model_error_payload(exc)}
        turn = apply_turn(settings, actor, trip["id"], expected=trip["revision"],
                          extraction_fields=extraction.fields, generator=extraction.generator,
                          trace_id=trace_id)
    return {**turn.payload, "text": turn.ask, "text_generator": "template"}
