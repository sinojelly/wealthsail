"""Behavior tests for the bounded, tenant-scoped model tool loop."""

from __future__ import annotations

import json

from pydantic import BaseModel

from modules.agent.tool_loop import (
    LoopBudget,
    ModelCallError,
    ModelResponse,
    ToolAbort,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolLoopStatus,
    execute_tool_loop,
)


class QuoteArgs(BaseModel):
    symbol: str


class ResearchOutput(BaseModel):
    summary: str


def test_multiple_tool_calls_use_one_assistant_message_and_trusted_context() -> None:
    """Splitting one model response into duplicate assistant messages breaks tool protocols."""

    observed_messages: list[list[dict[str, object]]] = []
    observed_contexts: list[ToolContext] = []
    responses = iter(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall("call-1", "quote", json.dumps({"symbol": "600519.SH"})),
                    ToolCall("call-2", "quote", json.dumps({"symbol": "510300.SH"})),
                )
            ),
            ModelResponse(content='{"summary":"two fresh quotes"}'),
        ]
    )

    def model_caller(*, messages: list[dict[str, object]], timeout: float) -> ModelResponse:
        assert timeout == 15
        observed_messages.append(list(messages))
        return next(responses)

    def quote_handler(context: ToolContext, arguments: QuoteArgs) -> dict[str, str]:
        observed_contexts.append(context)
        return {"symbol": arguments.symbol, "quality": "FRESH"}

    context = ToolContext(tenant_id="tenant-a", user_id="user-a", correlation_id="corr-1")
    result = execute_tool_loop(
        system_prompt="Read-only research",
        user_context={"question": "compare"},
        tool_context=context,
        tools={"quote": ToolDefinition(QuoteArgs, quote_handler)},
        output_schema=ResearchOutput,
        model_caller=model_caller,
    )

    assert result.status is ToolLoopStatus.SUCCESS
    assert result.data == {"summary": "two fresh quotes"}
    assert observed_contexts == [context, context]
    second_call_messages = observed_messages[1]
    assistant_messages = [message for message in second_call_messages if message["role"] == "assistant"]
    tool_messages = [message for message in second_call_messages if message["role"] == "tool"]
    assert len(assistant_messages) == 1
    assert len(assistant_messages[0]["tool_calls"]) == 2
    assert len(tool_messages) == 2


def test_schema_repairs_have_an_independent_hard_limit() -> None:
    """A schema-repair loop must not consume every general tool round."""

    calls = 0

    def model_caller(*, messages: list[dict[str, object]], timeout: float) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(content='{"wrong":"shape"}')

    result = execute_tool_loop(
        system_prompt="Return structured research",
        user_context={},
        tool_context=ToolContext("tenant-a", "user-a", "corr-2"),
        tools={},
        output_schema=ResearchOutput,
        model_caller=model_caller,
        budget=LoopBudget(max_rounds=10, max_schema_repairs=2),
    )

    assert calls == 3  # initial response plus exactly two repair attempts
    assert result.status is ToolLoopStatus.SCHEMA_INVALID
    assert result.reason_code == "SCHEMA_REPAIR_LIMIT"


def test_unknown_tool_fails_closed() -> None:
    """An unregistered model-selected tool cannot become a normal tool result."""

    result = execute_tool_loop(
        system_prompt="Read-only research",
        user_context={},
        tool_context=ToolContext("tenant-a", "user-a", "corr-3"),
        tools={},
        output_schema=ResearchOutput,
        model_caller=lambda **_: ModelResponse(
            tool_calls=(ToolCall("call-x", "write_ledger", "{}"),)
        ),
    )

    assert result.status is ToolLoopStatus.TOOL_ERROR
    assert result.reason_code == "TOOL_NOT_ALLOWED"


def test_retryable_model_error_uses_bounded_retry_then_model_unavailable() -> None:
    """Retry exhaustion is a model failure, not a market-data quality mutation."""

    calls = 0
    delays: list[float] = []

    def model_caller(**_: object) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise ModelCallError("rate limited", retryable=True, retry_after=0.25)

    result = execute_tool_loop(
        system_prompt="Read-only research",
        user_context={},
        tool_context=ToolContext("tenant-a", "user-a", "corr-4"),
        tools={},
        output_schema=ResearchOutput,
        model_caller=model_caller,
        budget=LoopBudget(max_model_attempts=3),
        sleeper=delays.append,
    )

    assert calls == 3
    assert delays == [0.25, 0.25]
    assert result.status is ToolLoopStatus.MODEL_UNAVAILABLE
    assert result.reason_code == "MODEL_RETRY_EXHAUSTED"


def test_retry_backoff_cannot_cross_total_deadline() -> None:
    """Retry-After must not make the loop issue a model call after its deadline."""

    clock = [100.0]
    calls = 0

    def model_caller(**_: object) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelCallError("rate limited", retryable=True, retry_after=2.0)
        return ModelResponse(content='{"summary":"too late"}')

    def advance(seconds: float) -> None:
        clock[0] += seconds

    result = execute_tool_loop(
        system_prompt="Read-only research",
        user_context={},
        tool_context=ToolContext("tenant-a", "user-a", "corr-5"),
        tools={},
        output_schema=ResearchOutput,
        model_caller=model_caller,
        budget=LoopBudget(total_deadline_seconds=1.0),
        sleeper=advance,
        monotonic=lambda: clock[0],
    )

    assert calls == 1
    assert result.status is ToolLoopStatus.DEADLINE_EXCEEDED
    assert result.reason_code == "TOTAL_DEADLINE"


def test_oversized_model_response_fails_before_schema_processing() -> None:
    """A provider response has its own byte budget, independent of tool output."""

    result = execute_tool_loop(
        system_prompt="Read-only research",
        user_context={},
        tool_context=ToolContext("tenant-a", "user-a", "corr-6"),
        tools={},
        output_schema=ResearchOutput,
        model_caller=lambda **_: ModelResponse(content='{"summary":"oversized"}'),
        budget=LoopBudget(max_model_response_bytes=8),
    )

    assert result.status is ToolLoopStatus.BUDGET_EXCEEDED
    assert result.reason_code == "MODEL_RESPONSE_TOO_LARGE"


def test_deterministic_tool_abstention_is_not_reclassified_as_tool_error() -> None:
    """Missing market data remains a data outcome, not a generic tool failure."""

    def unavailable_quote(_: ToolContext, __: QuoteArgs) -> dict[str, str]:
        raise ToolAbort(ToolLoopStatus.DATA_UNAVAILABLE, "QUOTE_STALE")

    result = execute_tool_loop(
        system_prompt="Read-only research",
        user_context={},
        tool_context=ToolContext("tenant-a", "user-a", "corr-7"),
        tools={"quote": ToolDefinition(QuoteArgs, unavailable_quote)},
        output_schema=ResearchOutput,
        model_caller=lambda **_: ModelResponse(
            tool_calls=(ToolCall("call-7", "quote", '{"symbol":"600519.SH"}'),)
        ),
    )

    assert result.status is ToolLoopStatus.DATA_UNAVAILABLE
    assert result.reason_code == "QUOTE_STALE"


def test_invalid_tool_arguments_fail_before_handler_execution() -> None:
    """Schema-invalid arguments never reach even a read-only tool handler."""

    executed = False

    def quote(_: ToolContext, __: QuoteArgs) -> dict[str, str]:
        nonlocal executed
        executed = True
        return {"quality": "FRESH"}

    result = execute_tool_loop(
        system_prompt="Read-only research",
        user_context={},
        tool_context=ToolContext("tenant-a", "user-a", "corr-8"),
        tools={"quote": ToolDefinition(QuoteArgs, quote)},
        output_schema=ResearchOutput,
        model_caller=lambda **_: ModelResponse(
            tool_calls=(ToolCall("call-8", "quote", '{"wrong":"field"}'),)
        ),
    )

    assert executed is False
    assert result.status is ToolLoopStatus.TOOL_ERROR
    assert result.reason_code == "TOOL_ARGUMENTS_INVALID"


def test_oversized_tool_result_fails_before_returning_to_model() -> None:
    """Large provider payloads cannot expand the next model context without bound."""

    model_calls = 0

    def model_caller(**_: object) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        return ModelResponse(
            tool_calls=(ToolCall("call-9", "quote", '{"symbol":"600519.SH"}'),)
        )

    result = execute_tool_loop(
        system_prompt="Read-only research",
        user_context={},
        tool_context=ToolContext("tenant-a", "user-a", "corr-9"),
        tools={
            "quote": ToolDefinition(
                QuoteArgs,
                lambda *_: {"payload": "x" * 128},
            )
        },
        output_schema=ResearchOutput,
        model_caller=model_caller,
        budget=LoopBudget(max_tool_result_bytes=16),
    )

    assert model_calls == 1
    assert result.status is ToolLoopStatus.BUDGET_EXCEEDED
    assert result.reason_code == "TOOL_RESULT_TOO_LARGE"
