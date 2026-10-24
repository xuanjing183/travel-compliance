"""Workflow engine: owns the checkpointer, the compiled graph and per-trip run locks.

Entry points (all called by the API layer in TASK-006, and directly by tests):

- `submit`   → `SubmitRun` whose `events()` is the SSE stream of one run (#8).
- `decide`   → resumes the interrupted run with the manager's decision (#13).
- `revalidate` → re-judges a pending trip against the published policy (#14, D-12).
- `retry_delivery` → admin retry of a `DELIVERY_PENDING` trip (#15).
- `recover`  → startup: abort runs cut off mid-planning, re-drive pending deliveries.
- `reset`    → demo reset incl. checkpoint threads (#26).

Business writes never depend on the SSE consumer: a run executes in a background
task and finishes even if the client disconnects (docs/api.md §1.5).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session, reset_database
from travel_desk.domain.states import TripEvent, TripStatus
from travel_desk.llm.summary import Summarizer, build_summarizer
from travel_desk.services import audit, idempotency, revalidation
from travel_desk.services.access import load_employee
from travel_desk.services.approvals import DECISIONS, apply_decision, check_decision_allowed
from travel_desk.services.delivery import DeliveryFault, deliver
from travel_desk.services.errors import (
    ServiceError,
    invalid_state,
    not_found,
    require_revision,
    stale_revision,
)
from travel_desk.services.trips import (
    PLAN_CLEARED,
    apply_transition,
    dumps,
    load_trip,
    owned_trip,
    slot_state,
    visible_trip,
)
from travel_desk.services.views import approval_view, trip_view
from travel_desk.workflow import events
from travel_desk.workflow.graph import GraphContext, build_graph

logger = logging.getLogger("travel_desk.workflow")

SUBMIT_ROUTE = "/api/trips/{trip_id}/submit"
DECISION_ROUTE = "/api/trips/{trip_id}/decision"
REVALIDATE_ROUTE = "/api/trips/{trip_id}/revalidate"
_INTERRUPTED = (TripStatus.VALIDATING.value, TripStatus.PLANNING.value)
_UNDELIVERED = (TripStatus.APPROVED.value, TripStatus.AUTO_APPROVED.value, TripStatus.DELIVERY_PENDING.value)


class SubmitRun:
    """Events of one submit request. Replays carry only a snapshot + `done` (§1.4)."""

    def __init__(self, first: list[dict[str, Any]], queue: asyncio.Queue | None = None,
                 task: asyncio.Task | None = None):
        self._first = first
        self._queue = queue
        self.task = task

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        for event in self._first:
            yield event
        if self._queue is None:
            return
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def collect(self) -> list[dict[str, Any]]:
        return [event async for event in self.events()]

    async def wait(self) -> None:
        if self.task is not None:
            await self.task


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _snapshot_preferences(owner: dict[str, Any], slots: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """D-20: Trip-level preferences override the personal ones, which are copied at submit time."""
    if slots.get("preferences"):
        return "trip_override", dict(slots["preferences"])
    if owner.get("preferences"):
        return "personal_snapshot", dict(owner["preferences"])
    return "none", {}


class Workflow:
    def __init__(self, settings: Settings, *, summarizer: Summarizer | None = None,
                 delivery_fault_hook: DeliveryFault | None = None):
        self.settings = settings
        self.summarizer = summarizer or build_summarizer(settings)
        self.delivery_fault_hook = delivery_fault_hook  # test-only (§4.7)
        self._conn: aiosqlite.Connection | None = None
        self.graph = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ lifecycle

    async def open(self) -> Workflow:
        self.settings.checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.settings.checkpoint_db_path))
        saver = AsyncSqliteSaver(self._conn)
        await saver.setup()
        self.saver = saver
        ctx = GraphContext(settings=self.settings, summarizer=self.summarizer,
                           fault_hook=lambda: self.delivery_fault_hook)
        self.graph = build_graph(ctx).compile(checkpointer=saver)
        return self

    async def close(self) -> None:
        await self.drain()
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def drain(self) -> None:
        """Wait for every in-flight run (shutdown, reset, tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _lock(self, trip_id: str) -> asyncio.Lock:
        return self._locks.setdefault(trip_id, asyncio.Lock())

    def lock(self, trip_id: str) -> asyncio.Lock:
        """Per-trip run lock, shared with other trip-mutating streams (messages, #6)."""
        return self._lock(trip_id)

    def spawn(self, coro) -> asyncio.Task:
        """Track a background run so `drain()` (shutdown, reset, tests) waits for it."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------------ submit (#8)

    async def submit(self, trip_id: str, actor: dict[str, Any], body: Any, *, idempotency_key: str | None,
                     trace_id: str) -> SubmitRun:
        key = idempotency.require_key(idempotency_key)
        if not isinstance(body, dict):
            raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
        expected = require_revision(body.get("expected_revision"), "expected_revision")
        lock = self._lock(trip_id)
        await lock.acquire()
        handed_over = False
        try:
            with db_session(self.settings.db_path) as conn:
                trip, owner = owned_trip(conn, trip_id, actor)
                req = idempotency.make_request("POST", SUBMIT_ROUTE, {"trip_id": trip_id}, actor["id"],
                                               key, body)
                stored = idempotency.lookup(conn, req)
                if stored is not None:
                    replay = events.trip_status(trip, previous=None, trace_id=trace_id, replayed=True)
                    return SubmitRun([replay, events.done(trip, trace_id=trace_id)])
                if expected != trip["revision"]:
                    raise stale_revision(expected, trip["revision"])
                if trip["status"] != TripStatus.COLLECTING.value:
                    raise invalid_state(trip["status"], [TripStatus.COLLECTING.value])
                state = slot_state(trip)
                if state.invalid_fields:
                    raise ServiceError(422, "INVALID_SLOT", "槽位格式不合法",
                                       {"invalid_fields": state.invalid_fields})
                if state.missing_fields:
                    raise ServiceError(422, "SLOTS_INCOMPLETE", "必填信息不完整，不能提交",
                                       {"missing_fields": state.missing_fields})
                source, snapshot = _snapshot_preferences(owner, state.values)
                thread_id = f"{trip_id}:rev-{trip['revision']}:run-{secrets.token_hex(4)}"
                trip = apply_transition(conn, trip, TripEvent.SUBMIT, actor=actor["id"], trace_id=trace_id,
                                        updates={**PLAN_CLEARED, "blocks_json": "[]", "thread_id": thread_id,
                                                 "preference_source": source,
                                                 "preference_snapshot_json": dumps(snapshot),
                                                 "delivery_error": None})
                audit.record(conn, trip_id=trip_id, revision=trip["revision"], actor=actor["id"],
                             event_type="submitted", payload={"slots": state.values, "thread_id": thread_id},
                             trace_id=trace_id)
                audit.record(conn, trip_id=trip_id, revision=trip["revision"], actor=actor["id"],
                             event_type="preference_snapshot",
                             payload={"preference_source": source, "preferences": snapshot},
                             trace_id=trace_id)
                idempotency.store(conn, req, 200, {"trip_id": trip_id, "revision": trip["revision"],
                                                   "thread_id": thread_id})
            first = [events.trip_status(trip, previous=TripStatus.COLLECTING.value, trace_id=trace_id)]
            queue: asyncio.Queue = asyncio.Queue()
            task = self.spawn(self._run(trip, actor["id"], trace_id, queue, lock))
            handed_over = True
            return SubmitRun(first, queue, task)
        finally:
            if not handed_over:
                lock.release()

    async def _run(self, trip: dict[str, Any], actor_id: str, trace_id: str, queue: asyncio.Queue,
                   lock: asyncio.Lock) -> None:
        trip_id, config = trip["id"], _config(trip["thread_id"])
        try:
            initial = {"trip_id": trip_id, "trace_id": trace_id, "actor": actor_id}
            async for chunk in self.graph.astream(initial, config, stream_mode="custom"):
                await queue.put(chunk)
            snapshot = await self.graph.aget_state(config)
            with read_session(self.settings.db_path) as conn:
                current = load_trip(conn, trip_id)
            if snapshot.values.get("result") == "no_approver":
                await queue.put(events.error(trip_id, current["revision"], code="NO_APPROVER",
                                             message="需要审批，但申请人没有直属经理，无法创建审批卡；方案未保留",
                                             details={"status": current["status"]}, retryable=False,
                                             trace_id=trace_id))
            else:
                await queue.put(events.done(current, trace_id=trace_id))
        except Exception as exc:  # noqa: BLE001 - the stream must always end with exactly one terminal event
            logger.warning("workflow run failed trip=%s type=%s trace_id=%s",
                           trip_id, type(exc).__name__, trace_id)
            revision = self._abort(trip_id, reason="workflow_failed", error_type=type(exc).__name__,
                                   trace_id=trace_id)
            await queue.put(events.error(trip_id, revision, code="WORKFLOW_FAILED",
                                         message="处理过程中出现意外错误，申请已退回填写状态，可重新提交",
                                         details={"error_type": type(exc).__name__}, retryable=True,
                                         trace_id=trace_id))
        finally:
            await queue.put(None)
            lock.release()

    def _abort(self, trip_id: str, *, reason: str, error_type: str | None, trace_id: str) -> int:
        """IMP-21: an interrupted VALIDATING/PLANNING run returns to COLLECTING without a half plan."""
        with db_session(self.settings.db_path) as conn:
            trip = load_trip(conn, trip_id)
            if trip is None:
                return 0
            audit.record(conn, trip_id=trip_id, revision=trip["revision"], actor="system", event_type="error",
                         payload={"stage": "workflow", "reason": reason, "error_type": error_type,
                                  "status": trip["status"]}, trace_id=trace_id)
            if trip["status"] in _INTERRUPTED:
                trip = apply_transition(conn, trip, TripEvent.ABORT, actor="system", trace_id=trace_id,
                                        updates=dict(PLAN_CLEARED))
            return trip["revision"]

    # ------------------------------------------------------------------ decision (#13)

    async def decide(self, trip_id: str, actor: dict[str, Any], body: Any, *, idempotency_key: str | None,
                     trace_id: str) -> tuple[int, dict[str, Any]]:
        key = idempotency.require_key(idempotency_key)
        if not isinstance(body, dict):
            raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
        decision = body.get("decision")
        if decision not in DECISIONS:
            raise ServiceError(400, "INVALID_DECISION", "decision 只能是 approve、reject 或 request_changes",
                               {"allowed": list(DECISIONS)})
        revision = require_revision(body.get("revision"), "revision")
        comment = body.get("comment")
        if comment is not None and not isinstance(comment, str):
            raise ServiceError(422, "VALIDATION_ERROR", "comment 必须是文本", {"field": "comment"})
        async with self._lock(trip_id):
            with read_session(self.settings.db_path) as conn:
                trip = load_trip(conn, trip_id)
                if trip is None:
                    raise not_found(trip_id)
                owner = load_employee(conn, trip["employee_id"])
                # Approval is never hidden behind 404: any existing trip answers 403 to a non-approver (§1.3).
                if actor["role"] != "manager":
                    raise ServiceError(403, "FORBIDDEN", "该操作仅限审批人", {"required_role": "manager"})
                if owner["id"] == actor["id"]:
                    raise ServiceError(403, "SELF_APPROVAL", "不能审批本人的差旅申请", {})
                if owner["manager_id"] != actor["id"]:
                    raise ServiceError(403, "NOT_DIRECT_MANAGER", "只有申请人的直属经理可以审批", {})
                req = idempotency.make_request("POST", DECISION_ROUTE, {"trip_id": trip_id}, actor["id"],
                                               key, body)
                stored = idempotency.lookup(conn, req)
                if stored is not None:
                    return stored.status_code, stored.body
                check_decision_allowed(conn, trip, revision)
            if decision != "approve" and not (comment and comment.strip()):
                raise ServiceError(422, "COMMENT_REQUIRED", "驳回或要求修改必须填写意见",
                                   {"decision": decision})
            comment = comment.strip() if comment else None
            payload = {"revision": revision, "decision": decision, "comment": comment,
                       "approver_id": actor["id"],
                       "trace_id": trace_id,
                       "idem": {"scope": req.scope, "key": req.key, "request_hash": req.request_hash}}
            config = _config(trip["thread_id"])
            state = await self.graph.aget_state(config)
            if state.interrupts:
                await self.graph.ainvoke(Command(resume=payload), config)
            else:
                # No live interrupt (e.g. the checkpoint was lost): same transaction, same delivery function.
                apply_decision(self.settings, trip_id=trip_id, revision=revision, approver_id=actor["id"],
                               decision=decision, comment=comment, trace_id=trace_id, via="direct", idem=req)
                # Note: delivery is no longer automatic after approval; admin must trigger it explicitly.
            with db_session(self.settings.db_path) as conn:
                trip = load_trip(conn, trip_id)
                row = conn.execute("SELECT * FROM approvals WHERE trip_id = ? AND revision = ?"
                                   " AND status = 'decided'",
                                   (trip_id, revision)).fetchone()
                response = {"trip": trip_view(conn, trip), "approval": approval_view(conn, row)}
                idempotency.finalize(conn, req, 200, response)
            return 200, response

    # ------------------------------------------------------------------ revalidate (#14, D-12)

    async def revalidate(self, trip_id: str, actor: dict[str, Any], body: Any, *, idempotency_key: str | None,
                         trace_id: str) -> tuple[int, dict[str, Any]]:
        key = idempotency.require_key(idempotency_key)
        if not isinstance(body, dict):
            raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
        revision = require_revision(body.get("revision"), "revision")
        async with self._lock(trip_id):
            with db_session(self.settings.db_path) as conn:
                trip, owner = visible_trip(conn, trip_id, actor)
                revalidation.check_actor(actor, owner)
                req = idempotency.make_request("POST", REVALIDATE_ROUTE, {"trip_id": trip_id}, actor["id"],
                                               key, body)
                stored = idempotency.lookup(conn, req)
                if stored is not None:
                    return stored.status_code, stored.body
                revalidation.check_revalidatable(trip, revision)
                trip, outcome = revalidation.revalidate(conn, self.settings, trip, owner, actor,
                                                        trace_id=trace_id)
                provisional = {"trip": trip_view(conn, trip), "previous_revision": revision,
                               "outcome": outcome}
                idempotency.store(conn, req, 200, provisional)
            # Note: delivery is no longer automatic after auto-approval; admin must trigger it explicitly.
            with db_session(self.settings.db_path) as conn:
                response = {"trip": trip_view(conn, load_trip(conn, trip_id)), "previous_revision": revision,
                            "outcome": outcome}
                idempotency.finalize(conn, req, 200, response)
            return 200, response

    # ------------------------------------------------------------------ delivery retry (#15)

    async def retry_delivery(self, trip_id: str, actor: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
        if actor["role"] != "admin":
            raise ServiceError(403, "FORBIDDEN", "该操作仅限管理员", {"required_role": "admin"})
        async with self._lock(trip_id):
            with read_session(self.settings.db_path) as conn:
                trip = load_trip(conn, trip_id)
                if trip is None:
                    raise not_found(trip_id)
                if trip["status"] != TripStatus.DELIVERY_PENDING.value:
                    raise invalid_state(trip["status"], [TripStatus.DELIVERY_PENDING.value])
            deliver(self.settings, trip_id, actor=actor["id"], trace_id=trace_id,
                    fault_hook=self.delivery_fault_hook)
            with read_session(self.settings.db_path) as conn:
                return trip_view(conn, load_trip(conn, trip_id))

    # ------------------------------------------------------------------ startup / reset

    async def recover(self, *, trace_id: str = "startup-recovery") -> dict[str, list[str]]:
        """Run once at startup, before requests are served."""
        with read_session(self.settings.db_path) as conn:
            rows = conn.execute("SELECT id, status FROM trips WHERE status IN (?,?)",
                                _INTERRUPTED).fetchall()
        report: dict[str, list[str]] = {"aborted": [], "delivered": [], "still_pending": []}
        for row in rows:
            async with self._lock(row["id"]):
                if row["status"] in _INTERRUPTED:
                    self._abort(row["id"], reason="interrupted_by_restart", error_type=None,
                                trace_id=trace_id)
                    report["aborted"].append(row["id"])
        return report

    async def reset(self, actor: dict[str, Any], *, trace_id: str) -> None:
        await self.drain()
        reset_database(self.settings)
        threads = {item.config["configurable"]["thread_id"] async for item in self.saver.alist(None)}
        for thread_id in threads:
            await self.saver.adelete_thread(thread_id)
        self._locks.clear()
        with db_session(self.settings.db_path) as conn:
            audit.record(conn, trip_id=None, revision=None, actor=actor["id"], event_type="demo_reset",
                         payload={"checkpoint_threads_deleted": len(threads)}, trace_id=trace_id)
