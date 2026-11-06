"""The Worker connects independent live observations to the original usage call."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from backend.ai_usage import repository, tracker
from backend.integrations.codex_research import ResearchResult
from backend.integrations.research_execution import ExecutionObserver
from tests.unit.test_daily_research_job import NOW, settings
from tests.unit.test_research_timeout_reliability import worker


def observation():
    observer = ExecutionObserver()
    observer.feed(b'{"type":"thread.started"}\n')
    observer.feed(
        b'{"type":"item.completed","item":{"id":"s1","type":"web_search","action":{"type":"search","query":"public facts"}}}\n'
    )
    return observer


def usage_fixture(monkeypatch):
    events, observed = [], asyncio.Event()

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data), asyncio.current_task()))
        if event == "call_progress":
            observed.set()

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(tracker, "account_snapshot", AsyncMock(return_value={}))
    return events, observed


@pytest.mark.asyncio
async def test_local_progress_is_independent_bound_and_joined_before_after_snapshot(
    monkeypatch,
):
    mod, _state, run, _owner = worker(monkeypatch)
    events, observed = usage_fixture(monkeypatch)
    monkeypatch.setattr(mod, "RESEARCH_PROGRESS_SECONDS", 0.001, raising=False)
    owner_task = asyncio.current_task()
    snapshots = 0

    async def account(*args, **kwargs):
        nonlocal snapshots
        snapshots += 1
        if snapshots == 2:
            monitor = next(
                task for event, _, task in events if event == "call_progress"
            )
            assert monitor.done(), (
                "progress monitor must finish before after-account sampling"
            )
        return {}

    monkeypatch.setattr(tracker, "account_snapshot", account)

    async def provider(*args, lifecycle, **kwargs):
        lifecycle.progress(observation().progress())
        async with asyncio.timeout(0.5):
            await observed.wait()
        lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
        return ResearchResult({"summary": "public", "sources": []})

    mod.run_codex_research.side_effect = provider
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "READY"
    update = next(row for event, row, _ in events if event == "call_progress")
    start = next(row for event, row, _ in events if event == "start_call")
    monitor = next(task for event, _, task in events if event == "call_progress")
    assert update["runId"] == str(run) == start["researchRunId"]
    assert update["id"] == start["id"] and update["taskId"] == start["taskId"]
    assert monitor is not owner_task and monitor.done()
    saved = mod.archive_repository.enqueue_research_archive.await_args.args[6][
        "researchWorkload"
    ]
    assert saved["executionProgress"]["observedSearchQueries"] == 1
    assert saved["modelExecution"]["exitConfirmed"] is True
    assert saved["modelExecution"]["dispatchStarted"] is True
    assert "identityBinding" not in saved["modelExecution"]


@pytest.mark.asyncio
async def test_remote_progress_stays_on_single_original_model_and_usage_call(
    monkeypatch,
):
    from backend.integrations import codex_client
    from backend.integrations.research_lifecycle import ResearchLifecycle

    mod, _state, run, _owner = worker(monkeypatch)
    events, _ = usage_fixture(monkeypatch)
    monkeypatch.setattr(
        codex_client, "preflight", AsyncMock(return_value={"version": "fixture"})
    )

    async def remote(*args, identity, on_progress, on_exit, trace_usage, **kwargs):
        assert trace_usage is False
        await asyncio.create_task(on_progress(observation().progress()))
        lifecycle = ResearchLifecycle(identity)
        lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
        on_exit(lifecycle.snapshot())
        result = ResearchResult({"summary": "public", "sources": []})
        result.research_execution_state = lifecycle.snapshot()
        return result

    provider = AsyncMock(side_effect=remote)
    monkeypatch.setattr(codex_client, "research", provider)
    assert (
        await mod.sync_daily_research(
            object(), settings(codex_runner_url="http://fixture"), NOW
        )
    )["status"] == "READY"
    provider.assert_awaited_once()
    mod.run_codex_research.assert_not_awaited()
    assert sum(event == "start_call" for event, _, _ in events) == 1
    assert next(row for event, row, _ in events if event == "call_progress")[
        "runId"
    ] == str(run)


@pytest.mark.asyncio
async def test_pending_cancel_ack_is_visible_without_becoming_exit_proof(monkeypatch):
    from backend.integrations import codex_client
    from backend.integrations.research_lifecycle import ResearchLifecycle

    mod, _state, _run, _owner = worker(monkeypatch)
    usage_fixture(monkeypatch)
    monkeypatch.setattr(
        codex_client, "preflight", AsyncMock(return_value={"version": "fixture"})
    )

    async def remote(*args, identity, on_exit, **kwargs):
        lifecycle = ResearchLifecycle(identity)
        lifecycle.started()
        lifecycle.terminating("WORKER_RPC_TIMEOUT")
        on_exit(lifecycle.snapshot())
        error = TimeoutError()
        error.timeout_origin = "WORKER_RPC_TIMEOUT"
        raise error

    provider = AsyncMock(side_effect=remote)
    monkeypatch.setattr(codex_client, "research", provider)
    assert (
        await mod.sync_daily_research(
            object(), settings(codex_runner_url="http://fixture"), NOW
        )
    )["status"] == "UNCERTAIN"
    saved = mod.archive_repository.enqueue_research_archive.await_args.args[6][
        "researchWorkload"
    ]
    assert saved["modelExecution"]["state"] == "TERMINATING"
    assert saved["modelExecution"]["terminationRequested"] is True
    assert saved["modelExecution"]["exitConfirmed"] is False
    mod.release_model_guard.assert_not_awaited()
    provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("after_snapshot_cancel", [False, True])
async def test_failure_and_account_interruption_preserve_observed_work(
    monkeypatch, after_snapshot_cancel
):
    mod, _state, _run, _owner = worker(monkeypatch)
    usage_fixture(monkeypatch)
    observer = observation()
    observer.feed(b'{"type":"turn.completed"}\n')

    async def provider(*args, lifecycle, **kwargs):
        lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
        if after_snapshot_cancel:
            return ResearchResult(
                {"summary": "public", "sources": []},
                execution_statistics=observer.statistics(),
                execution_progress=observer.progress(),
            )
        error = TimeoutError()
        error.execution_statistics, error.execution_progress = (
            observer.statistics(),
            observer.progress(),
        )
        raise error

    if after_snapshot_cancel:

        async def trace(_settings, *, operation, **kwargs):
            await operation()
            raise asyncio.CancelledError()

        monkeypatch.setattr(mod, "trace_inference", trace)
    mod.run_codex_research.side_effect = provider
    if after_snapshot_cancel:
        with pytest.raises(asyncio.CancelledError):
            await mod.sync_daily_research(object(), settings(), NOW)
    else:
        assert (await mod.sync_daily_research(object(), settings(), NOW))[
            "status"
        ] == "UNCERTAIN"
    saved = mod.archive_repository.enqueue_research_archive.await_args.args[6][
        "researchWorkload"
    ]
    assert saved["executionStatistics"]["distinctSearchQueries"] == 1
    assert saved["executionProgress"]["modelCompleted"] is True
    assert saved["modelExecution"]["exitConfirmed"] is True
    assert saved["modelElapsedScope"] == "MODEL_OPERATION"
    mod.run_codex_research.assert_awaited_once()
