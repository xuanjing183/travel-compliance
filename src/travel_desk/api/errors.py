"""Uniform error envelope: {"error": {code, message, details, trace_id}} (contract §6.1/§6.2)."""
from __future__ import annotations

import logging
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from travel_desk.services.errors import ServiceError

logger = logging.getLogger("travel_desk.api")

_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def get_trace_id(request: Request) -> str:
    trace_id = getattr(request.state, "trace_id", None)
    if not trace_id:
        trace_id = new_trace_id(request.headers.get("X-Trace-Id"))
        request.state.trace_id = trace_id
    return trace_id


def new_trace_id(client_value: str | None = None) -> str:
    """Accept a client-supplied trace id only if it is short and log-safe."""
    if client_value and _TRACE_ID_RE.match(client_value):
        return client_value
    return uuid.uuid4().hex


def error_response(request: Request, status_code: int, code: str, message: str,
                   details: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {},
                           "trace_id": get_trace_id(request)}},
    )


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError | ServiceError)
    return error_response(request, exc.status_code, exc.code, exc.message, exc.details)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": [str(part) for part in err.get("loc", ())], "type": err.get("type"), "msg": err.get("msg")}
        for err in exc.errors()
    ]
    # Unparseable JSON is a 400 (§6.2); a well-formed body with invalid fields is a 422.
    if any(err["type"] == "json_invalid" for err in errors):
        return error_response(request, 400, "INVALID_JSON", "请求体不是合法 JSON", {"errors": errors})
    return error_response(request, 422, "VALIDATION_ERROR", "请求字段格式不合法", {"errors": errors})


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else code
    return error_response(request, exc.status_code, code, message)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log type and trace id only; exception text could carry provider payloads.
    logger.exception("unhandled error trace_id=%s type=%s", get_trace_id(request), type(exc).__name__)
    return error_response(request, 500, "INTERNAL_ERROR", "服务器内部错误", {})


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(ServiceError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
