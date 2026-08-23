"""Bounded, fail-closed Tool Calling loop for the replaceable model edge.

The loop deliberately owns no financial write capability. A caller injects a
tenant-scoped :class:`ToolContext` and an explicit registry of read-only tools.
Model arguments can never select the tenant, user, or correlation identity.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ValidationError


class ToolLoopStatus(str, Enum):
    """Stable outcome categories consumed by jobs, reports, and telemetry."""

    SUCCESS = "SUCCESS"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    TOOL_ERROR = "TOOL_ERROR"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True)
class ToolContext:
    """Trusted identity supplied by the authenticated application boundary."""

    tenant_id: str
    user_id: str
    correlation_id: str


@dataclass(frozen=True)
class ToolCall:
    """Provider-neutral representation of one model-selected tool call."""

    id: str
    name: str
    arguments_json: str

    def as_message_value(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments_json},
        }


@dataclass(frozen=True)
class ModelResponse:
    """Provider adapter output after normalizing the model SDK response."""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ToolDefinition:
    """A typed, explicitly registered read-only tool."""

    arguments_schema: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], Any]


@dataclass(frozen=True)
class LoopBudget:
    """Independent deterministic bounds for one model orchestration run."""

    max_rounds: int = 5
    max_schema_repairs: int = 2
    max_total_tool_calls: int = 10
    max_model_attempts: int = 3
    per_model_timeout_seconds: float = 15.0
    total_deadline_seconds: float = 60.0
    max_model_response_bytes: int = 256 * 1024
    max_tool_result_bytes: int = 64 * 1024
    retry_backoff_seconds: float = 1.0

    def __post_init__(self) -> None:
        positive_fields = (
            self.max_rounds,
            self.max_total_tool_calls,
            self.max_model_attempts,
            self.per_model_timeout_seconds,
            self.total_deadline_seconds,
            self.max_model_response_bytes,
            self.max_tool_result_bytes,
        )
        if any(value <= 0 for value in positive_fields):
            raise ValueError("loop limits and timeouts must be positive")
        if self.max_schema_repairs < 0 or self.retry_backoff_seconds < 0:
            raise ValueError("repair and retry limits cannot be negative")


@dataclass(frozen=True)
class ToolLoopResult:
    """A type-stable outcome; callers never infer failure from free-form text."""

    status: ToolLoopStatus
    reason_code: str
    data: dict[str, Any] | None = None


class ModelCallError(RuntimeError):
    """Normalized provider error with explicit retry semantics."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class ToolAbort(RuntimeError):
    """Deterministic tool abstention that must retain its business category."""

    _ALLOWED_STATUSES = {
        ToolLoopStatus.DATA_UNAVAILABLE,
        ToolLoopStatus.ABSTAIN,
    }

    def __init__(self, status: ToolLoopStatus, reason_code: str) -> None:
        if status not in self._ALLOWED_STATUSES:
            raise ValueError("tool abort status must be DATA_UNAVAILABLE or ABSTAIN")
        if not reason_code:
            raise ValueError("tool abort reason_code is required")
        super().__init__(reason_code)
        self.status = status
        self.reason_code = reason_code


def _result(status: ToolLoopStatus, reason_code: str) -> ToolLoopResult:
    return ToolLoopResult(status=status, reason_code=reason_code)


def execute_tool_loop(
    *,
    system_prompt: str,
    user_context: Mapping[str, Any],
    tool_context: ToolContext,
    tools: Mapping[str, ToolDefinition],
    output_schema: type[BaseModel],
    model_caller: Callable[..., ModelResponse],
    budget: LoopBudget | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> ToolLoopResult:
    """Execute a bounded provider-neutral loop and fail closed on every branch."""

    limits = budget or LoopBudget()
    started_at = monotonic()

    def deadline_exceeded() -> bool:
        return monotonic() - started_at >= limits.total_deadline_seconds

    schema_repairs = 0
    total_tool_calls = 0
    messages: list[dict[str, object]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(dict(user_context), ensure_ascii=False),
        },
    ]

    for _round in range(limits.max_rounds):
        if deadline_exceeded():
            return _result(ToolLoopStatus.DEADLINE_EXCEEDED, "TOTAL_DEADLINE")

        response: ModelResponse | None = None
        for attempt in range(limits.max_model_attempts):
            if deadline_exceeded():
                return _result(ToolLoopStatus.DEADLINE_EXCEEDED, "TOTAL_DEADLINE")
            try:
                response = model_caller(
                    messages=list(messages),
                    timeout=limits.per_model_timeout_seconds,
                )
                if deadline_exceeded():
                    return _result(ToolLoopStatus.DEADLINE_EXCEEDED, "TOTAL_DEADLINE")
                break
            except ModelCallError as error:
                final_attempt = attempt + 1 >= limits.max_model_attempts
                if not error.retryable:
                    return _result(ToolLoopStatus.MODEL_UNAVAILABLE, "MODEL_NON_RETRYABLE")
                if final_attempt:
                    return _result(ToolLoopStatus.MODEL_UNAVAILABLE, "MODEL_RETRY_EXHAUSTED")
                delay = (
                    error.retry_after
                    if error.retry_after is not None
                    else limits.retry_backoff_seconds * (2**attempt)
                )
                remaining = limits.total_deadline_seconds - (monotonic() - started_at)
                if delay >= remaining:
                    return _result(ToolLoopStatus.DEADLINE_EXCEEDED, "TOTAL_DEADLINE")
                sleeper(delay)
            except Exception:
                return _result(ToolLoopStatus.MODEL_UNAVAILABLE, "MODEL_CALL_FAILED")

        if response is None:
            return _result(ToolLoopStatus.MODEL_UNAVAILABLE, "MODEL_EMPTY_RESPONSE")
        if not isinstance(response, ModelResponse):
            return _result(ToolLoopStatus.MODEL_UNAVAILABLE, "MODEL_ADAPTER_INVALID")

        normalized_response = {
            "content": response.content,
            "tool_calls": [call.as_message_value() for call in response.tool_calls],
        }
        response_bytes = len(
            json.dumps(normalized_response, ensure_ascii=False).encode("utf-8")
        )
        if response_bytes > limits.max_model_response_bytes:
            return _result(ToolLoopStatus.BUDGET_EXCEEDED, "MODEL_RESPONSE_TOO_LARGE")

        if response.tool_calls:
            if total_tool_calls + len(response.tool_calls) > limits.max_total_tool_calls:
                return _result(ToolLoopStatus.BUDGET_EXCEEDED, "TOOL_CALL_LIMIT")

            # One assistant message owns the complete tool-call set. Splitting
            # it per tool produces an invalid history for common model APIs.
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [call.as_message_value() for call in response.tool_calls],
                }
            )

            for call in response.tool_calls:
                definition = tools.get(call.name)
                if definition is None:
                    return _result(ToolLoopStatus.TOOL_ERROR, "TOOL_NOT_ALLOWED")
                try:
                    raw_arguments = json.loads(call.arguments_json)
                    arguments = definition.arguments_schema.model_validate(raw_arguments)
                except (json.JSONDecodeError, ValidationError, TypeError):
                    return _result(ToolLoopStatus.TOOL_ERROR, "TOOL_ARGUMENTS_INVALID")

                try:
                    value = definition.handler(tool_context, arguments)
                    serialized = json.dumps(value, ensure_ascii=False)
                except ToolAbort as error:
                    return _result(error.status, error.reason_code)
                except Exception:
                    return _result(ToolLoopStatus.TOOL_ERROR, "TOOL_EXECUTION_FAILED")

                if deadline_exceeded():
                    return _result(ToolLoopStatus.DEADLINE_EXCEEDED, "TOTAL_DEADLINE")
                if len(serialized.encode("utf-8")) > limits.max_tool_result_bytes:
                    return _result(ToolLoopStatus.BUDGET_EXCEEDED, "TOOL_RESULT_TOO_LARGE")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": serialized,
                    }
                )
                total_tool_calls += 1
            continue

        try:
            validated = output_schema.model_validate_json(response.content)
        except ValidationError as error:
            if schema_repairs >= limits.max_schema_repairs:
                return _result(ToolLoopStatus.SCHEMA_INVALID, "SCHEMA_REPAIR_LIMIT")
            schema_repairs += 1
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The response failed the required JSON schema. "
                        f"Correct only these errors: {error.errors(include_input=False)}"
                    ),
                }
            )
            continue

        return ToolLoopResult(
            status=ToolLoopStatus.SUCCESS,
            reason_code="VALIDATED",
            data=validated.model_dump(),
        )

    return _result(ToolLoopStatus.BUDGET_EXCEEDED, "ROUND_LIMIT")
