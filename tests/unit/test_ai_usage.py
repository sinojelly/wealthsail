from copy import deepcopy
from importlib import import_module, util
from uuid import uuid4

import pytest


def calculation():
    assert util.find_spec("backend.ai_usage.calculation"), (
        "AI usage calculation missing"
    )
    return import_module("backend.ai_usage.calculation")


def snapshot(**changes):
    return {
        "accountFingerprint": "account-one",
        "planType": "plus",
        "observedAt": "2026-10-27T02:00:00+00:00",
        "windows": [
            {
                "key": "codex:primary",
                "label": "5小时",
                "usedPercent": 20,
                "windowMinutes": 300,
                "resetsAt": 1793070000,
            }
        ],
        "credits": {"balance": "12.50", "unlimited": False, "hasCredits": True},
        **changes,
    }


def test_quota_uses_percentage_points_and_decimal_credits_not_token_or_currency_cost():
    before = snapshot()
    after = deepcopy(before)
    after["observedAt"] = "2026-10-27T02:01:00+00:00"
    after["windows"][0]["usedPercent"] = 22.5
    after["credits"]["balance"] = "12.40"
    result = calculation().quota_delta(before, after)
    assert result["windows"] == [
        {
            "key": "codex:primary",
            "label": "5小时",
            "consumedPercent": 2.5,
            "status": "ESTIMATED",
        }
    ]
    assert result["credits"] == {"consumed": "0.10", "status": "ESTIMATED"}


@pytest.mark.parametrize(
    "problem,status",
    [
        ("account", "ACCOUNT_CHANGED"),
        ("plan", "ACCOUNT_CHANGED"),
        ("month", "PERIOD_CHANGED"),
        ("reset", "PERIOD_CHANGED"),
        ("expired", "PERIOD_CHANGED"),
        ("decrease", "RESET_OR_TOPUP"),
        ("nan", "UNKNOWN"),
        ("negative", "UNKNOWN"),
        ("window", "PERIOD_CHANGED"),
        ("missing", "UNKNOWN"),
        ("reversed", "UNKNOWN"),
        ("concurrent", "CONCURRENT"),
    ],
)
def test_invalid_or_unattributable_quota_is_unknown_instead_of_illegal_consumption(
    problem, status
):
    before = snapshot()
    after = deepcopy(before)
    after["observedAt"] = "2026-10-27T02:01:00+00:00"
    row = after["windows"][0]
    if problem == "account":
        after["accountFingerprint"] = "another"
    elif problem == "plan":
        after["planType"] = "pro"
    elif problem == "month":
        after["observedAt"] = "2026-11-01T01:00:00+00:00"
    elif problem == "reset":
        row["resetsAt"] += 300
    elif problem == "expired":
        before["windows"][0]["resetsAt"] = row["resetsAt"] = 1793066400
    elif problem == "decrease":
        row["usedPercent"] = 1
    elif problem == "nan":
        row["usedPercent"] = "NaN"
    elif problem == "negative":
        row["usedPercent"] = -1
    elif problem == "window":
        row["windowMinutes"] = 100
    elif problem == "missing":
        after["windows"] = []
    elif problem == "reversed":
        after["observedAt"] = "2026-10-27T01:59:00+00:00"
    result = calculation().quota_delta(
        before, after, concurrent=problem == "concurrent"
    )
    assert result["windows"][0]["consumedPercent"] is None
    assert result["windows"][0]["status"] == status


@pytest.mark.parametrize(
    "credits",
    [
        {"balance": "13", "unlimited": False},
        {"balance": "-1"},
        {"balance": "NaN"},
        {"balance": "Infinity"},
        {"balance": "10", "unlimited": True},
        None,
    ],
)
def test_credit_reset_topup_unlimited_or_invalid_values_are_not_charged(credits):
    after = snapshot(credits=credits)
    assert calculation().quota_delta(snapshot(), after)["credits"]["consumed"] is None


def test_cached_tokens_are_part_of_input_and_missing_tokens_remain_unknown():
    result = calculation().tokens(
        {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20}
    )
    assert result == {
        "inputTokens": 100,
        "cachedTokens": 80,
        "outputTokens": 20,
        "totalTokens": 120,
    }
    assert calculation().tokens({}) == {
        "inputTokens": None,
        "cachedTokens": None,
        "outputTokens": None,
        "totalTokens": None,
    }
    assert (
        calculation().tokens({"input_tokens": -1, "cached_input_tokens": True})[
            "inputTokens"
        ]
        is None
    )


def test_task_aggregation_keeps_each_inference_and_separates_business_failure():
    call = {
        "id": "one",
        "model": "test-model",
        "status": "SUCCEEDED",
        "inputTokens": 100,
        "cachedTokens": 80,
        "outputTokens": 20,
        "totalTokens": 120,
        "quota": {
            "windows": [
                {
                    "key": "codex:primary",
                    "label": "5小时",
                    "consumedPercent": 1,
                    "status": "ESTIMATED",
                }
            ],
            "credits": {"consumed": "0.10", "status": "ESTIMATED"},
        },
    }
    second = {
        **deepcopy(call),
        "id": "two",
        "status": "FAILED",
        "outputTokens": None,
        "totalTokens": None,
    }
    result = calculation().aggregate_task(
        {"id": "task", "businessStatus": "FAILED"}, [call, second]
    )
    assert result["businessStatus"] == "FAILED"
    assert result["inferenceStatus"] == "PARTIAL"
    assert result["inputTokens"] == 200 and result["cachedTokens"] == 160
    assert result["outputTokens"] is None and result["totalTokens"] is None
    assert result["callCount"] == 2 and result["calls"] == [call, second]


def test_unmeasured_call_does_not_make_task_quota_look_complete():
    measured = {
        "id": "one",
        "model": "codex",
        "status": "SUCCEEDED",
        "quota": {
            "windows": [
                {
                    "key": "p",
                    "label": "5小时",
                    "consumedPercent": 1,
                    "status": "ESTIMATED",
                }
            ],
            "credits": {"consumed": "1", "status": "ESTIMATED"},
        },
    }
    unknown = {
        "id": "two",
        "model": "codex",
        "status": "FAILED",
        "quota": {"windows": [], "credits": {"consumed": None, "status": "UNKNOWN"}},
    }
    result = calculation().aggregate_task(
        {"id": "task", "businessStatus": "FAILED"}, [measured, unknown]
    )
    assert result["quota"]["windows"][0]["consumedPercent"] is None
    assert result["quota"]["credits"]["consumed"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "business_failure",
        "inference_failure",
        "timeout",
        "cancelled",
        "snapshot_failure",
    ],
)
async def test_tracker_preserves_actual_inferences_even_when_business_fails(
    monkeypatch, outcome
):
    import asyncio
    from types import SimpleNamespace
    from backend.integrations import codex_client

    assert util.find_spec("backend.ai_usage.tracker"), "AI usage tracker missing"
    tracker = import_module("backend.ai_usage.tracker")
    repo = import_module("backend.ai_usage.repository")
    events = []

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data)))

    async def account(_settings, **_kwargs):
        if outcome == "snapshot_failure":
            raise OSError("not saved in usage")
        return snapshot(
            status="READY",
            observedAt=tracker.now().isoformat(),
            observationId=str(uuid4()),
            authGeneration="fixture-auth",
        )

    monkeypatch.setattr(repo, "persist", persist)
    monkeypatch.setattr(codex_client, "account", account, raising=False)

    async def inference():
        if outcome == "inference_failure":
            raise ValueError("private prompt must not be recorded")
        if outcome == "timeout":
            raise TimeoutError()
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return {
            "content": "answer",
            "model": "test-model",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "output_tokens": 20,
            },
        }

    try:
        async with tracker.usage_task(
            object(),
            SimpleNamespace(),
            tenant_id="tenant",
            user_id="user",
            task_type="REPORT",
            task_name="标准报告",
        ):
            await tracker.trace_inference(
                SimpleNamespace(),
                model="test-model",
                call_type="completion",
                operation=inference,
            )
            if outcome == "business_failure":
                raise ValueError("business validation failed")
            if outcome == "success":
                await tracker.trace_inference(
                    SimpleNamespace(),
                    model="test-model",
                    call_type="completion",
                    operation=inference,
                )
    except (ValueError, TimeoutError, asyncio.CancelledError):
        pass

    calls = [data for event, data in events if event == "finish_call"]
    task = [data for event, data in events if event == "finish_task"][-1]
    assert len(calls) == (2 if outcome == "success" else 1)
    assert calls[0]["status"] == (
        "FAILED"
        if outcome == "inference_failure"
        else "UNCERTAIN"
        if outcome in {"timeout", "cancelled"}
        else "SUCCEEDED"
    )
    assert task["businessStatus"] == (
        "CANCELLED"
        if outcome == "cancelled"
        else "FAILED"
        if outcome in {"business_failure", "inference_failure", "timeout"}
        else "SUCCEEDED"
    )
    assert "private prompt" not in str(events) and "answer" not in str(events)
    if outcome in {"success", "business_failure", "snapshot_failure"}:
        assert calls[0]["tokens"]["totalTokens"] == 120


@pytest.mark.asyncio
async def test_tracking_is_not_a_reason_for_business_failure(monkeypatch):
    from types import SimpleNamespace

    assert util.find_spec("backend.ai_usage.tracker"), "AI usage tracker missing"
    tracker = import_module("backend.ai_usage.tracker")
    repo = import_module("backend.ai_usage.repository")

    async def broken(*_args):
        raise OSError("storage unavailable")

    monkeypatch.setattr(repo, "persist", broken)

    async def inference():
        return {"content": "ok"}

    async with tracker.usage_task(
        object(),
        SimpleNamespace(),
        tenant_id=None,
        user_id=None,
        task_type="DAILY_RESEARCH",
        task_name="公开研究",
    ):
        assert await tracker.trace_inference(
            SimpleNamespace(), model=None, call_type="research", operation=inference
        ) == {"content": "ok"}


def test_same_task_across_quota_periods_does_not_add_incomparable_balances():
    calls = []
    for index, reset in enumerate([1793070000, 1793080000]):
        before = snapshot()
        before["windows"][0]["resetsAt"] = reset
        after = deepcopy(before)
        after["windows"][0]["usedPercent"] = 21
        after["credits"]["balance"] = "12.40"
        if index:
            before["observedAt"] = after["observedAt"] = "2026-11-01T00:00:00Z"
        calls.append(
            {
                "id": str(index),
                "model": "codex",
                "status": "SUCCEEDED",
                "quota": calculation().quota_delta(before, after),
            }
        )
    result = calculation().aggregate_task(
        {"id": "task", "businessStatus": "SUCCEEDED"}, calls
    )
    assert result["quota"]["windows"][0]["consumedPercent"] is None
    assert result["quota"]["credits"]["consumed"] is None


def test_unavailable_account_windows_remain_unknown():
    assert (
        calculation().quota_delta({"windows": None}, {"windows": None})["windows"] == []
    )


@pytest.mark.asyncio
async def test_cancel_during_before_snapshot_still_finishes_the_call(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    tracker = import_module("backend.ai_usage.tracker")
    repo = import_module("backend.ai_usage.repository")
    events = []
    first = True

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data)))

    async def account(_settings, **_kwargs):
        nonlocal first
        if first:
            first = False
            raise asyncio.CancelledError()
        return {}

    monkeypatch.setattr(repo, "persist", persist)
    monkeypatch.setattr(tracker, "account_snapshot", account)
    async with tracker.usage_task(
        object(),
        SimpleNamespace(),
        tenant_id=None,
        user_id=None,
        task_type="CHAT",
        task_name="对话",
    ):
        with pytest.raises(asyncio.CancelledError):
            await tracker.trace_inference(
                SimpleNamespace(), model=None, call_type="completion", operation=None
            )
    finished = [data for event, data in events if event == "finish_call"]
    assert len(finished) == 1 and finished[0]["status"] == "UNCERTAIN"


@pytest.mark.asyncio
async def test_closing_stream_after_done_retains_success_and_measured_tokens(
    monkeypatch,
):
    from types import SimpleNamespace

    tracker = import_module("backend.ai_usage.tracker")
    events = []

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data)))

    async def account(_settings, **_kwargs):
        return {}

    async def stream():
        yield {"done": True, "usage": {"input_tokens": 100, "output_tokens": 20}}

    monkeypatch.setattr(
        import_module("backend.ai_usage.repository"), "persist", persist
    )
    monkeypatch.setattr(tracker, "account_snapshot", account)
    async with tracker.usage_task(
        object(),
        SimpleNamespace(),
        tenant_id=None,
        user_id=None,
        task_type="CHAT",
        task_name="对话",
    ):
        source = tracker.trace_stream(
            SimpleNamespace(), model=None, operation_iterator=stream()
        )
        assert (await source.__anext__())["done"] is True
        await source.aclose()
    call = [data for event, data in events if event == "finish_call"][0]
    assert call["status"] == "SUCCEEDED"
    assert call["tokens"]["totalTokens"] == 120
    assert events[-1][1]["businessStatus"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_done_stream_cleanup_does_not_erase_business_cancellation(monkeypatch):
    from types import SimpleNamespace

    tracker = import_module("backend.ai_usage.tracker")
    events = []

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data)))

    async def account(_settings, **_kwargs):
        return {}

    async def stream():
        yield {"done": True, "usage": {"input_tokens": 100, "output_tokens": 20}}

    monkeypatch.setattr(
        import_module("backend.ai_usage.repository"), "persist", persist
    )
    monkeypatch.setattr(tracker, "account_snapshot", account)
    async with tracker.usage_task(
        object(),
        SimpleNamespace(),
        tenant_id=None,
        user_id=None,
        task_type="CHAT",
        task_name="对话",
    ):
        source = tracker.trace_stream(
            SimpleNamespace(), model=None, operation_iterator=stream()
        )
        await source.__anext__()
        tracker.mark_business("CANCELLED")
        await source.aclose()
    assert events[-1][1]["businessStatus"] == "CANCELLED"
