"""SSE event envelopes (docs/api.md §1.5). Transport-agnostic: the API layer frames them."""
from __future__ import annotations

import uuid
from typing import Any

from travel_desk.services.audit import now_ts

TERMINAL = frozenset({"done", "error"})


def make_event(event_type: str, *, trip_id: str, revision: int, trace_id: str,
               payload: dict[str, Any]) -> dict[str, Any]:
    return {"event_id": str(uuid.uuid4()), "trip_id": trip_id, "revision": revision, "type": event_type,
            "ts": now_ts(), "trace_id": trace_id, "payload": payload}


def trip_status(trip: dict[str, Any], *, previous: str | None, trace_id: str, status: str | None = None,
                replayed: bool = False) -> dict[str, Any]:
    """`status` overrides the trip's current status (for intermediate edges of one step, e.g. delivery)."""
    payload = {"status": status or trip["status"], "revision": trip["revision"],
               "policy_version": trip["policy_version"], "previous_status": previous}
    if replayed:
        payload["replayed"] = True
    return make_event("trip_status", trip_id=trip["id"], revision=trip["revision"], trace_id=trace_id,
                      payload=payload)


def stage(trip: dict[str, Any], name: str, message: str, *, trace_id: str) -> dict[str, Any]:
    return make_event("status", trip_id=trip["id"], revision=trip["revision"], trace_id=trace_id,
                      payload={"stage": name, "message": message})


def done(trip: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
    return make_event("done", trip_id=trip["id"], revision=trip["revision"], trace_id=trace_id,
                      payload={"status": trip["status"], "revision": trip["revision"]})


def error(trip_id: str, revision: int, *, code: str, message: str, details: dict[str, Any] | None,
          retryable: bool, trace_id: str) -> dict[str, Any]:
    return make_event("error", trip_id=trip_id, revision=revision, trace_id=trace_id,
                      payload={"code": code, "message": message, "details": details or {},
                               "retryable": retryable})
