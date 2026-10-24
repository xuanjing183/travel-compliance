"""Trip state machine (§6.8, D-09, D-12, D-21, IMP-03..06).

Every status change goes through `transition`, which rejects edges the
contract does not allow and says whether the revision increases. The
workflow and services must not write `trips.status` any other way.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TripStatus(str, Enum):
    COLLECTING = "COLLECTING"
    VALIDATING = "VALIDATING"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    PLANNING = "PLANNING"
    AUTO_APPROVED = "AUTO_APPROVED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    DELIVERY_PENDING = "DELIVERY_PENDING"
    DELIVERED = "DELIVERED"
    REJECTED = "REJECTED"


class TripEvent(str, Enum):
    SUBMIT = "submit"                      # D-13 explicit submit
    BLOCK = "block"                        # hard block found (D-14)
    VALIDATED = "validated"                # inputs valid → planning
    AUTO_APPROVE = "auto_approve"
    REQUIRE_APPROVAL = "require_approval"
    NO_APPROVER = "no_approver"            # D-21 / IMP-06
    ABORT = "abort"                        # unexpected planning failure: back to editing, nothing kept
    EDIT_AFTER_BLOCK = "edit_after_block"  # D-09 / IMP-03, revision unchanged
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"    # revision + 1
    REVALIDATE = "revalidate"              # D-12, revision + 1, providers not called
    START_DELIVERY = "start_delivery"
    DELIVERED = "delivered"


S, E = TripStatus, TripEvent

# event -> (allowed source states, target, revision increment)
_TRANSITIONS: dict[TripEvent, tuple[frozenset[TripStatus], TripStatus, int]] = {
    E.SUBMIT: (frozenset({S.COLLECTING}), S.VALIDATING, 0),
    E.BLOCK: (frozenset({S.VALIDATING}), S.POLICY_BLOCKED, 0),
    E.VALIDATED: (frozenset({S.VALIDATING}), S.PLANNING, 0),
    E.AUTO_APPROVE: (frozenset({S.PLANNING}), S.AUTO_APPROVED, 0),
    E.REQUIRE_APPROVAL: (frozenset({S.PLANNING}), S.PENDING_APPROVAL, 0),
    E.NO_APPROVER: (frozenset({S.PLANNING}), S.COLLECTING, 0),
    E.ABORT: (frozenset({S.VALIDATING, S.PLANNING}), S.COLLECTING, 0),
    E.EDIT_AFTER_BLOCK: (frozenset({S.POLICY_BLOCKED}), S.COLLECTING, 0),
    E.APPROVE: (frozenset({S.PENDING_APPROVAL}), S.APPROVED, 0),
    E.REJECT: (frozenset({S.PENDING_APPROVAL}), S.REJECTED, 0),
    E.REQUEST_CHANGES: (frozenset({S.PENDING_APPROVAL}), S.COLLECTING, 1),
    E.REVALIDATE: (frozenset({S.PENDING_APPROVAL}), S.VALIDATING, 1),
    E.START_DELIVERY: (frozenset({S.APPROVED, S.AUTO_APPROVED}), S.DELIVERY_PENDING, 0),
    E.DELIVERED: (frozenset({S.DELIVERY_PENDING}), S.DELIVERED, 0),
}

TERMINAL_STATES = frozenset({S.DELIVERED, S.REJECTED})
EDITABLE_STATES = frozenset({S.COLLECTING, S.POLICY_BLOCKED})  # slots/messages allowed (IMP-03)


class InvalidTransition(ValueError):
    def __init__(self, status: TripStatus, event: TripEvent):
        self.status, self.event = status, event
        super().__init__(f"event {event.value} is not allowed in state {status.value}")


@dataclass(frozen=True)
class Transition:
    source: TripStatus
    target: TripStatus
    event: TripEvent
    revision: int  # revision after the transition


def can_apply(status: TripStatus | str, event: TripEvent) -> bool:
    return TripStatus(status) in _TRANSITIONS[event][0]


def transition(status: TripStatus | str, revision: int, event: TripEvent) -> Transition:
    source = TripStatus(status)
    allowed, target, bump = _TRANSITIONS[event]
    if source not in allowed:
        raise InvalidTransition(source, event)
    if revision < 1:
        raise ValueError("revision starts at 1")
    return Transition(source=source, target=target, event=event, revision=revision + bump)


def allowed_events(status: TripStatus | str) -> list[TripEvent]:
    source = TripStatus(status)
    return [event for event, (sources, _, _) in _TRANSITIONS.items() if source in sources]
