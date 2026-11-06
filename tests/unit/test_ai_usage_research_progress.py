"""Live telemetry is identity-bound, bounded and independent of inference."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.ai_usage import repository, tracker
from backend.integrations.research_execution import ExecutionObserver


def progress():
    observer = ExecutionObserver()
    observer.feed(b'{"type":"thread.started"}\n')
    return observer.progress()


@pytest.mark.asyncio
async def test_live_progress_is_run_and_call_bound_throttled_without_changing_unknown_usage(
    monkeypatch,
):
    assert hasattr(tracker, "research_progress_run")
    assert hasattr(tracker, "record_research_progress")
    persisted = []

    async def persist(pool, tenant, user, event, data):
        persisted.append((event, deepcopy(data)))

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(
        tracker, "account_snapshot", AsyncMock(return_value={"status": "UNAVAILABLE"})
    )
    run_id = uuid4()

    async def operation():
        assert await tracker.record_research_progress(uuid4(), progress()) is False
        other = uuid4()
        with tracker.research_progress_run(other):
            assert await tracker.record_research_progress(other, progress()) is False
        assert (
            await tracker.record_research_progress(
                run_id, {**progress(), "raw": "PRIVATE"}
            )
            is True
        )
        assert await tracker.record_research_progress(run_id, progress()) is False
        return SimpleNamespace(
            usage={},
            model="codex",
            execution_progress={
                **progress(),
                "phase": "TURN_COMPLETED",
                "modelCompleted": True,
            },
        )

    async with tracker.usage_task(
        object(),
        object(),
        tenant_id=None,
        user_id=None,
        task_type="daily_research",
        task_name="daily",
    ):
        with tracker.research_progress_run(run_id):
            await tracker.trace_inference(
                object(), model="codex", call_type="research", operation=operation
            )
        assert await tracker.record_research_progress(run_id, progress()) is False
    events = [row for event, row in persisted if event == "call_progress"]
    assert len(events) == 1 and events[0]["runId"] == str(run_id)
    starts = [row for event, row in persisted if event == "start_call"]
    assert (
        events[0]["id"] == starts[0]["id"]
        and events[0]["taskId"] == starts[0]["taskId"]
    )
    finished = next(row for event, row in persisted if event == "finish_call")
    assert finished["status"] == "SUCCEEDED"
    assert finished["researchProgress"]["modelCompleted"] is True
    assert finished["tokens"]["inputTokens"] is None
    assert "PRIVATE" not in str(events)


@pytest.mark.asyncio
async def test_failed_progress_write_cannot_fail_completed_model(monkeypatch):
    assert hasattr(tracker, "research_progress_run")
    calls = []

    async def persist(pool, tenant, user, event, data):
        calls.append(event)
        if event == "call_progress":
            raise OSError("PRIVATE")

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(tracker, "account_snapshot", AsyncMock(return_value={}))
    run_id = uuid4()

    async def operation():
        assert await tracker.record_research_progress(run_id, progress()) is False
        return SimpleNamespace(usage={"prompt_tokens": 5}, model="codex")

    async with tracker.usage_task(
        object(),
        object(),
        tenant_id=None,
        user_id=None,
        task_type="daily_research",
        task_name="daily",
    ):
        with tracker.research_progress_run(run_id):
            result = await tracker.trace_inference(
                object(), model="codex", call_type="research", operation=operation
            )
    assert result.usage == {"prompt_tokens": 5}
    assert calls.count("call_progress") == 1 and calls[-1] == "finish_task"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_stalled_or_cancelled_progress_monitor_does_not_cancel_model(
    monkeypatch, cancel
):
    assert hasattr(tracker, "research_progress_run")
    writing = asyncio.Event()
    statuses = []

    async def persist(pool, tenant, user, event, data):
        if event == "call_progress":
            writing.set()
            await asyncio.Event().wait()
        if event == "finish_call":
            statuses.append(data["status"])

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(tracker, "account_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(tracker, "PROGRESS_WRITE_TIMEOUT_SECONDS", 0.01)
    identity = uuid4()

    async def operation():
        monitor = asyncio.create_task(
            tracker.record_research_progress(identity, progress())
        )
        await asyncio.wait_for(writing.wait(), 1)
        if cancel:
            monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        return SimpleNamespace(usage={})

    async with tracker.usage_task(
        object(),
        object(),
        tenant_id=None,
        user_id=None,
        task_type="daily_research",
        task_name="daily",
    ):
        with tracker.research_progress_run(identity):
            await tracker.trace_inference(
                object(), model="codex", call_type="research", operation=operation
            )
    assert statuses == ["SUCCEEDED"]


@pytest.mark.asyncio
async def test_concurrent_calls_never_share_progress_run_or_call_identity(monkeypatch):
    assert hasattr(tracker, "research_progress_run")
    rows, ready = [], asyncio.Event()

    async def persist(pool, tenant, user, event, data):
        if event == "call_progress":
            rows.append(deepcopy(data))

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(tracker, "account_snapshot", AsyncMock(return_value={}))

    async def run_one(identity):
        async def operation():
            await ready.wait()
            await tracker.record_research_progress(identity, progress())
            return SimpleNamespace(usage={})

        async with tracker.usage_task(
            object(),
            object(),
            tenant_id=None,
            user_id=None,
            task_type="daily_research",
            task_name="daily",
        ):
            with tracker.research_progress_run(identity):
                await tracker.trace_inference(
                    object(), model="codex", call_type="research", operation=operation
                )

    identities = [uuid4(), uuid4()]
    tasks = [asyncio.create_task(run_one(identity)) for identity in identities]
    ready.set()
    await asyncio.gather(*tasks)
    assert {row["runId"] for row in rows} == {str(value) for value in identities}
    assert (
        len({row["id"] for row in rows}) == 2
        and len({row["taskId"] for row in rows}) == 2
    )
