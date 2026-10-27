"""Independent, bounded quota observations never turn unknown usage into zero."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from time import monotonic

import pytest

from backend.ai_usage import repository, tracker
from backend.ai_usage.calculation import quota_delta
from backend.integrations import codex_client


def snapshot(*, observed_at=None, observation_id="before", **changes):
    return {
        "status": "READY",
        "accountFingerprint": "fixture-account",
        "authGeneration": "fixture-auth",
        "observationId": observation_id,
        "planType": "plus",
        "observedAt": (observed_at or datetime.now(timezone.utc)).isoformat(),
        "windows": [
            {
                "key": "codex:primary",
                "label": "5小时",
                "usedPercent": 20,
                "windowMinutes": 300,
                "resetsAt": 1999999999,
            }
        ],
        "credits": {"balance": "12.50", "unlimited": False},
        **changes,
    }


@pytest.mark.parametrize("same", ["observedAt", "observationId"])
def test_reused_observation_is_unknown_even_when_balance_did_not_change(same):
    before = snapshot(observed_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    after = snapshot(observation_id="after")
    after[same] = before[same]
    result = quota_delta(before, after)
    assert result["windows"][0]["status"] == "UNKNOWN"
    assert result["windows"][0]["consumedPercent"] is None
    assert result["credits"] == {"consumed": None, "status": "UNKNOWN"}


def test_independent_fresh_observations_can_measure_a_real_zero():
    before = snapshot(observed_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    after = snapshot(observation_id="after")
    result = quota_delta(before, after)
    assert result["windows"][0]["consumedPercent"] == 0
    assert result["credits"] == {"consumed": "0.00", "status": "ESTIMATED"}


@pytest.mark.parametrize("status", ["STALE", "UNAVAILABLE", "UNAUTHENTICATED"])
def test_non_live_observation_is_not_a_quota_measurement(status):
    before = snapshot(observed_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    after = snapshot(observation_id="after", status=status)
    result = quota_delta(before, after)
    assert result["windows"][0]["consumedPercent"] is None
    assert result["credits"]["consumed"] is None


def test_same_account_after_reauthentication_has_no_attributable_delta():
    before = snapshot(observed_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    after = snapshot(observation_id="after", authGeneration="new-login")
    result = quota_delta(before, after)
    assert result["windows"][0]["status"] == "ACCOUNT_CHANGED"
    assert result["credits"] == {"consumed": None, "status": "ACCOUNT_CHANGED"}


@pytest.mark.asyncio
async def test_tracker_requests_new_live_observations_and_keeps_only_safe_fields(
    monkeypatch,
):
    requests = []

    async def account(_settings, *, fresh=False):
        requests.append(fresh)
        return snapshot(email="private@example.test", raw={"token": "secret"})

    monkeypatch.setattr(codex_client, "account", account)
    result = await tracker.account_snapshot(SimpleNamespace())
    assert requests == [True]
    assert result["status"] == "READY"
    assert result["observationId"] == "before"
    assert result["authGeneration"] == "fixture-auth"
    assert "email" not in result and "raw" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["stale", "old", "future", "missing_time", "missing_id"]
)
async def test_tracker_rejects_stale_or_invalid_observation_time(monkeypatch, problem):
    raw = snapshot()
    if problem == "stale":
        raw["status"] = "STALE"
    elif problem == "old":
        raw["observedAt"] = (
            datetime.now(timezone.utc) - timedelta(minutes=3)
        ).isoformat()
    elif problem == "future":
        raw["observedAt"] = (
            datetime.now(timezone.utc) + timedelta(minutes=3)
        ).isoformat()
    elif problem == "missing_time":
        raw.pop("observedAt")
    else:
        raw.pop("observationId")

    async def account(_settings, **_kwargs):
        return raw

    monkeypatch.setattr(codex_client, "account", account)
    assert await tracker.account_snapshot(SimpleNamespace()) == {}


@pytest.mark.asyncio
async def test_snapshot_budget_accepts_normal_twelve_second_queries(monkeypatch):
    # A virtual provider takes twelve seconds. A lower telemetry budget must fail
    # without making this test wait for the real NAS latency.
    real_timeout = asyncio.timeout
    budgets = []

    def timeout(seconds):
        budgets.append(seconds)
        return real_timeout(0 if seconds < 12 else 1)

    async def account(_settings, **_kwargs):
        await asyncio.sleep(0)
        return snapshot()

    monkeypatch.setattr(tracker.asyncio, "timeout", timeout)
    monkeypatch.setattr(codex_client, "account", account)
    assert (await tracker.account_snapshot(SimpleNamespace()))["status"] == "READY"
    assert 12 <= budgets[0] <= 26


@pytest.mark.asyncio
async def test_snapshot_timeout_is_diagnosed_without_private_error_text(
    monkeypatch,
    caplog,
):
    async def account(_settings, **_kwargs):
        raise TimeoutError("private@example.test token secret provider response")

    monkeypatch.setattr(codex_client, "account", account)
    assert await tracker.account_snapshot(SimpleNamespace(), stage="before") == {}
    assert "stage=before" in caplog.text
    assert "error_class=TimeoutError" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "private@example.test" not in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,error_class",
    [
        ({"status": "UNAVAILABLE"}, "AccountSnapshotUnavailable"),
        ({"observedAt": "not-a-timestamp"}, "AccountSnapshotNotFresh"),
    ],
)
async def test_snapshot_diagnostics_distinguish_unavailable_and_invalid_observations(
    monkeypatch,
    caplog,
    changes,
    error_class,
):
    async def account(_settings, **_kwargs):
        return snapshot(**changes)

    monkeypatch.setattr(codex_client, "account", account)
    assert await tracker.account_snapshot(SimpleNamespace(), stage="after") == {}
    assert "stage=after" in caplog.text
    assert f"error_class={error_class}" in caplog.text
    assert "elapsed_ms=" in caplog.text


@pytest.mark.asyncio
async def test_hanging_snapshot_cannot_erase_successful_inference_tokens(monkeypatch):
    events = []

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data)))

    async def account(_settings, **_kwargs):
        await asyncio.sleep(1)
        raise AssertionError("quota deadline did not interrupt provider")

    async def infer():
        return {"usage": {"input_tokens": 100, "output_tokens": 20}}

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(codex_client, "account", account)
    monkeypatch.setattr(tracker, "SNAPSHOT_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(tracker, "SNAPSHOT_TOTAL_BUDGET_SECONDS", 0.02, raising=False)
    started = monotonic()
    async with tracker.usage_task(
        object(),
        SimpleNamespace(),
        tenant_id=None,
        user_id=None,
        task_type="TEST",
        task_name="fixture",
    ):
        await tracker.trace_inference(
            SimpleNamespace(),
            model="fixture",
            call_type="completion",
            operation=infer,
        )
    record = next(data for event, data in events if event == "finish_call")
    assert record["status"] == "SUCCEEDED"
    assert monotonic() - started < 0.5
    assert record["tokens"]["totalTokens"] == 120
    assert record["before"] == record["after"] == {}
    assert quota_delta(record["before"], record["after"])["credits"]["consumed"] is None


@pytest.mark.asyncio
async def test_before_and_after_share_a_finite_telemetry_budget(monkeypatch):
    events, requests = [], []

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data)))

    async def account(_settings, *, fresh=False):
        requests.append(fresh)
        await asyncio.sleep(0.15)
        return snapshot(observation_id=str(len(requests)))

    async def infer():
        return {"usage": {"input_tokens": 100, "output_tokens": 20}}

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(codex_client, "account", account)
    monkeypatch.setattr(tracker, "SNAPSHOT_TIMEOUT_SECONDS", 0.2, raising=False)
    monkeypatch.setattr(tracker, "SNAPSHOT_TOTAL_BUDGET_SECONDS", 0.25, raising=False)
    async with tracker.usage_task(
        object(),
        SimpleNamespace(),
        tenant_id=None,
        user_id=None,
        task_type="TEST",
        task_name="fixture",
    ):
        await tracker.trace_inference(
            SimpleNamespace(),
            model="fixture",
            call_type="completion",
            operation=infer,
        )
    record = next(data for event, data in events if event == "finish_call")
    assert record["before"]["status"] == "READY"
    assert record["after"] == {}
    assert record["tokens"]["totalTokens"] == 120
    assert quota_delta(record["before"], record["after"])["credits"]["consumed"] is None


@pytest.mark.asyncio
async def test_inference_gets_two_independent_observations_not_page_cache(monkeypatch):
    events, requests = [], []

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data)))

    async def account(_settings, *, fresh=False):
        requests.append(fresh)
        return snapshot(observation_id=str(len(requests)))

    async def infer():
        return {"usage": {"input_tokens": 100, "output_tokens": 20}}

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(codex_client, "account", account)
    async with tracker.usage_task(
        object(),
        SimpleNamespace(),
        tenant_id=None,
        user_id=None,
        task_type="TEST",
        task_name="fixture",
    ):
        await tracker.trace_inference(
            SimpleNamespace(),
            model="fixture",
            call_type="completion",
            operation=infer,
        )
    record = next(data for event, data in events if event == "finish_call")
    assert requests == [True, True]
    assert record["before"]["observationId"] != record["after"]["observationId"]
    assert record["tokens"]["totalTokens"] == 120


@pytest.mark.asyncio
async def test_cancelling_after_snapshot_keeps_tokens_and_propagates_cancellation(
    monkeypatch,
):
    events, requests = [], []

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data)))

    async def account(_settings, *, fresh=False):
        requests.append(fresh)
        if len(requests) == 2:
            raise asyncio.CancelledError()
        return snapshot()

    async def infer():
        return {"usage": {"input_tokens": 100, "output_tokens": 20}}

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(codex_client, "account", account)
    with pytest.raises(asyncio.CancelledError):
        async with tracker.usage_task(
            object(),
            SimpleNamespace(),
            tenant_id=None,
            user_id=None,
            task_type="TEST",
            task_name="fixture",
        ):
            await tracker.trace_inference(
                SimpleNamespace(),
                model="fixture",
                call_type="completion",
                operation=infer,
            )
    record = next(data for event, data in events if event == "finish_call")
    assert record["status"] == "SUCCEEDED"
    assert record["tokens"]["totalTokens"] == 120
    assert record["after"] == {}
    assert events[-1][1]["businessStatus"] == "CANCELLED"
