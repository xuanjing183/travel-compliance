"""Business errors raised by services; the API layer renders them with the §1.1 envelope."""
from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def not_found(trip_id: str) -> ServiceError:
    return ServiceError(404, "TRIP_NOT_FOUND", "差旅申请不存在或不可见", {"trip_id": trip_id})


def stale_revision(requested: int, current: int) -> ServiceError:
    return ServiceError(409, "STALE_REVISION", "请求的 revision 不是当前版本",
                        {"requested_revision": requested, "current_revision": current})


def invalid_state(status: str, allowed: list[str]) -> ServiceError:
    return ServiceError(409, "INVALID_STATE", "当前状态不允许该操作",
                        {"status": status, "allowed_states": allowed})


def require_revision(value: Any, field: str) -> int:
    """422 format check for a revision number (bool is not an integer here)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ServiceError(422, "VALIDATION_ERROR", "revision 必须是正整数",
                           {"errors": [{"loc": ["body", field], "type": "int_type",
                                        "msg": "positive integer"}]})
    return value
