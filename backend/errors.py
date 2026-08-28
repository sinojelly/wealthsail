"""WealthSail unified error envelope and global exception mapping.

Every API response — success or failure — follows the v2.2 envelope contract:

    success: {"data": ..., "meta": {"requestId", "asOf", "qualityStatus", "schemaVersion"}}
    error:   {"error": {"code", "message", "retryable", "fieldErrors"}, "requestId"}

Error codes are converged to: VALIDATION_ERROR, AUTH_REQUIRED, FORBIDDEN,
NOT_FOUND, VERSION_CONFLICT, IDEMPOTENCY_CONFLICT, RATE_LIMITED,
DATA_UNAVAILABLE, INTERNAL_ERROR.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from modules import errors as domain_errors

logger = logging.getLogger("wealthsail.errors")

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Error codes → HTTP status
# ---------------------------------------------------------------------------
ERROR_HTTP_STATUS: dict[str, int] = {
    "VALIDATION_ERROR": 400,
    "AUTH_REQUIRED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "RATE_LIMITED": 429,
    "DATA_UNAVAILABLE": 503,
    "INTERNAL_ERROR": 500,
}

# Canonical retryability per code (5xx-class codes are retryable by default).
ERROR_RETRYABLE: dict[str, bool] = {
    "VALIDATION_ERROR": False,
    "AUTH_REQUIRED": False,
    "FORBIDDEN": False,
    "NOT_FOUND": False,
    "VERSION_CONFLICT": False,
    "IDEMPOTENCY_CONFLICT": False,
    "RATE_LIMITED": True,
    "DATA_UNAVAILABLE": True,
    "INTERNAL_ERROR": True,
}

VALID_ERROR_CODES = frozenset(ERROR_HTTP_STATUS)


class AppError(Exception):
    """Base application error carrying the converged error-code contract."""

    code = "INTERNAL_ERROR"
    retryable = False
    http_status = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        field_errors: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            if code not in VALID_ERROR_CODES:
                raise ValueError(f"unknown error code: {code}")
            self.code = code
            self.http_status = ERROR_HTTP_STATUS[code]
        if retryable is not None:
            self.retryable = retryable
        else:
            self.retryable = ERROR_RETRYABLE.get(self.code, False)
        self.field_errors = field_errors or []


class ValidationError_(AppError):
    code = "VALIDATION_ERROR"
    http_status = 400


class AuthRequiredError(AppError):
    code = "AUTH_REQUIRED"
    http_status = 401


class ForbiddenError(AppError):
    code = "FORBIDDEN"
    http_status = 403


class NotFoundError(AppError):
    code = "NOT_FOUND"
    http_status = 404


class VersionConflictError(AppError):
    code = "VERSION_CONFLICT"
    http_status = 409


class IdempotencyConflictError(AppError):
    code = "IDEMPOTENCY_CONFLICT"
    http_status = 409


class RateLimitedError(AppError):
    code = "RATE_LIMITED"
    http_status = 429
    retryable = True


class DataUnavailableError(AppError):
    code = "DATA_UNAVAILABLE"
    http_status = 503
    retryable = True


class InternalError(AppError):
    code = "INTERNAL_ERROR"
    http_status = 500


# ---------------------------------------------------------------------------
# Money-safe JSON rendering
# ---------------------------------------------------------------------------
def _money_default(value: Any) -> Any:
    """json.dumps default: Decimal → str, datetime → RFC3339, UUID → str.

    Financial precision is non-negotiable: Decimal is serialized as a decimal
    string, never coerced through float.
    """
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class MoneyJSONResponse(JSONResponse):
    """JSONResponse that renders Decimal/datetime/UUID safely."""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
            default=_money_default,
        ).encode("utf-8")


# ---------------------------------------------------------------------------
# Success envelope
# ---------------------------------------------------------------------------
def ok(
    data: Any,
    *,
    request_id: str,
    as_of: datetime,
    quality_status: str = "FRESH",
) -> dict[str, Any]:
    """Build the standard success envelope."""
    return {
        "data": data,
        "meta": {
            "requestId": request_id,
            "asOf": as_of,
            "qualityStatus": quality_status,
            "schemaVersion": SCHEMA_VERSION,
        },
    }


def request_id_of(request: Request) -> str:
    """Request id: honor an inbound correlation header, else mint one."""
    incoming = (
        request.headers.get("X-Request-Id")
        or request.headers.get("X-Correlation-Id")
        or ""
    ).strip()
    if incoming and len(incoming) <= 128:
        return incoming
    return "req_" + uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Exception → envelope handlers
# ---------------------------------------------------------------------------
def _error_payload(
    *,
    code: str,
    message: str,
    retryable: bool,
    field_errors: list[dict[str, str]],
    request_id: str,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "fieldErrors": field_errors,
        },
        "requestId": request_id,
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return MoneyJSONResponse(
            status_code=exc.http_status,
            content=_error_payload(
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                field_errors=exc.field_errors,
                request_id=request_id_of(request),
            ),
        )

    @app.exception_handler(domain_errors.WealthSailError)
    async def _domain_error_handler(
        request: Request, exc: domain_errors.WealthSailError
    ) -> JSONResponse:
        mapping: list[tuple[type[Exception], str]] = [
            (domain_errors.ValidationError, "VALIDATION_ERROR"),
            (domain_errors.AuthRequiredError, "AUTH_REQUIRED"),
            (domain_errors.ForbiddenError, "FORBIDDEN"),
            (domain_errors.NotFoundError, "NOT_FOUND"),
            (domain_errors.ConflictError, "IDEMPOTENCY_CONFLICT"),
            (domain_errors.DataUnavailableError, "DATA_UNAVAILABLE"),
        ]
        code = next((value for cls, value in mapping if isinstance(exc, cls)), "INTERNAL_ERROR")
        return MoneyJSONResponse(
            status_code=ERROR_HTTP_STATUS[code],
            content=_error_payload(
                code=code,
                message=exc.message,
                retryable=ERROR_RETRYABLE[code],
                field_errors=[],
                request_id=request_id_of(request),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        field_errors: list[dict[str, str]] = []
        for err in exc.errors():
            field_errors.append(
                {
                    "field": ".".join(str(part) for part in err.get("loc", ())),
                    "message": err.get("msg", "invalid value"),
                }
            )
        return MoneyJSONResponse(
            status_code=400,
            content=_error_payload(
                code="VALIDATION_ERROR",
                message="请求参数校验失败",
                retryable=False,
                field_errors=field_errors,
                request_id=request_id_of(request),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "INTERNAL_ERROR"
        message = str(exc.detail)
        if exc.status_code == 401:
            code = "AUTH_REQUIRED"
        elif exc.status_code == 403:
            code = "FORBIDDEN"
        elif exc.status_code == 404:
            code = "NOT_FOUND"
        elif exc.status_code == 405:
            code = "VALIDATION_ERROR"
            message = "请求方法不受支持"
        elif exc.status_code == 429:
            code = "RATE_LIMITED"
        return MoneyJSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=_error_payload(
                code=code,
                message=message,
                retryable=exc.status_code >= 500,
                field_errors=[],
                request_id=request_id_of(request),
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return MoneyJSONResponse(
            status_code=500,
            content=_error_payload(
                code="INTERNAL_ERROR",
                message="服务内部错误，请稍后重试",
                retryable=True,
                field_errors=[],
                request_id=request_id_of(request),
            ),
        )


# Re-export for lint convenience (validation class shadows builtin intent).
__all__ = [
    "AppError",
    "ValidationError_",
    "AuthRequiredError",
    "ForbiddenError",
    "NotFoundError",
    "VersionConflictError",
    "IdempotencyConflictError",
    "RateLimitedError",
    "DataUnavailableError",
    "InternalError",
    "MoneyJSONResponse",
    "ok",
    "request_id_of",
    "register_exception_handlers",
    "jsonable_encoder",
]
