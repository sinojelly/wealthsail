"""No real provider: deadline ownership, independent snapshots and exit proof."""

import asyncio
from importlib import import_module
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from tests.unit.test_daily_research_job import NOW, isolate, settings


def worker(monkeypatch):
    mod, state = isolate(monkeypatch)
    run, owner = uuid4(), uuid4()
    monkeypatch.setattr(mod, "claim_daily_run", AsyncMock(return_value={"run_id": run}))
    monkeypatch.setattr(mod, "create_model_guard", AsyncMock(return_value=owner))
    return mod, state, run, owner


@pytest.mark.asyncio
async def test_confirmed_cli_timeout_releases_only_its_guard_without_reinference(
    monkeypatch,
):
    mod, state, run, owner = worker(monkeypatch)
    received = []

    async def provider(*args, lifecycle=None, **kwargs):
        if lifecycle is not None:
            lifecycle.started()
            lifecycle.terminating("CLI_MODEL_TIMEOUT")
            lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=-9)
            received.append(lifecycle.snapshot())
        error = TimeoutError("PRIVATE provider body")
        error.timeout_origin = "CLI_MODEL_TIMEOUT"
        raise error

    mod.run_codex_research.side_effect = provider
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "UNCERTAIN"
    assert state["terminal"][0]["failure_reason_code"] == "CLI_MODEL_TIMEOUT"
    assert len(received) == 1
    kwargs = {}
    if mod.os.name == "nt":
        kwargs["proof"] = {
            "kind": "LOCAL_WINDOWS_JOB",
            "identityBinding": received[0]["identityBinding"],
            "completedAt": received[0]["completedAt"],
        }
    mod.release_model_guard.assert_awaited_once_with(ANY, run, owner, NOW, **kwargs)
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_report_without_exit_proof_does_not_release_occupation(
    monkeypatch,
):
    mod, _state, _run, _owner = worker(monkeypatch)
    monkeypatch.setattr(
        mod,
        "run_codex_research",
        AsyncMock(return_value={"summary": "public", "sources": []}),
    )
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "READY"
    mod.release_model_guard.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_invocation_exit_does_not_release_current_guard(monkeypatch):
    from backend.integrations.codex_research import ResearchResult
    from backend.integrations.research_lifecycle import ResearchLifecycle

    mod, _state, _run, _owner = worker(monkeypatch)
    foreign = ResearchLifecycle(
        {"runId": str(uuid4()), "ownerToken": str(uuid4()), "callId": str(uuid4())}
    )
    foreign.exited(reaped=True, descendants_confirmed=True, returncode=0)
    result = ResearchResult({"summary": "public", "sources": []})
    result.research_execution_state = foreign.snapshot()
    monkeypatch.setattr(mod, "run_codex_research", AsyncMock(return_value=result))
    await mod.sync_daily_research(object(), settings(), NOW)
    mod.release_model_guard.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_sampling_is_outside_model_deadline_and_elapsed(monkeypatch):
    mod, _state, _run, _owner = worker(monkeypatch)
    calls = []

    async def independent_snapshots(_settings, *, operation, **kwargs):
        await asyncio.sleep(0.025)
        value = await operation()
        await asyncio.sleep(0.025)
        return value

    async def provider(*args, lifecycle=None, **kwargs):
        calls.append(True)
        if lifecycle is not None:
            lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
        return {"summary": "public", "sources": []}

    monkeypatch.setattr(mod, "trace_inference", independent_snapshots)
    monkeypatch.setattr(
        mod,
        "research_deadlines",
        lambda seconds: {"workerSeconds": 0.01},
        raising=False,
    )
    mod.run_codex_research.side_effect = provider
    result = await mod.sync_daily_research(
        object(), settings(daily_research_timeout_seconds=0.01), NOW
    )
    assert result["status"] == "READY"
    assert len(calls) == 1
    saved = mod.archive_repository.enqueue_research_archive.await_args.args[6]
    assert saved["researchWorkload"]["modelElapsedMs"] < 20


@pytest.mark.asyncio
async def test_remaining_request_budget_is_checked_before_consuming_model_slot(
    monkeypatch,
):
    budget = import_module("backend.jobs.research_budget")
    mod, _state, _run, _owner = worker(monkeypatch)
    with budget.research_request_budget(1):
        result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "FAILED"
    assert result["errorClass"] == "ResearchExecutionBudgetInsufficient"
    mod.create_model_guard.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_timeout_exit_receipt_releases_guard_without_rewriting_failure(
    monkeypatch,
):
    from backend.integrations import codex_client
    from backend.integrations.research_lifecycle import ResearchLifecycle

    mod, state, _run, _owner = worker(monkeypatch)
    configured = settings(codex_runner_url="http://fixture")
    monkeypatch.setattr(
        codex_client, "preflight", AsyncMock(return_value={"version": "fixture"})
    )

    async def remote(*args, identity, trace_usage, **kwargs):
        assert trace_usage is False
        lifecycle = ResearchLifecycle(identity)
        lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=-9)
        error = TimeoutError()
        error.timeout_origin = "CLI_MODEL_TIMEOUT"
        error.research_execution_state = lifecycle.snapshot()
        raise error

    provider = AsyncMock(side_effect=remote)
    monkeypatch.setattr(codex_client, "research", provider)
    assert (await mod.sync_daily_research(object(), configured, NOW))[
        "status"
    ] == "UNCERTAIN"
    assert state["terminal"][0]["failure_reason_code"] == "CLI_MODEL_TIMEOUT"
    mod.release_model_guard.assert_awaited_once()
    provider.assert_awaited_once()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_outer_timeout_has_precise_origin_and_retains_unknown_guard(
    monkeypatch,
):
    mod, state, _run, _owner = worker(monkeypatch)
    monkeypatch.setattr(mod, "research_deadlines", lambda _: {"workerSeconds": 0.01})

    async def slow(*args, **kwargs):
        await asyncio.Event().wait()

    mod.run_codex_research.side_effect = slow
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "UNCERTAIN"
    assert state["terminal"][0]["failure_reason_code"] == "WORKER_OUTER_TIMEOUT"
    mod.release_model_guard.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_interruption_keeps_measured_tokens_and_confirmed_exit(
    monkeypatch,
):
    from backend.integrations.codex_research import ResearchResult

    mod, _state, _run, _owner = worker(monkeypatch)

    async def after_interrupted(_settings, *, operation, **kwargs):
        await operation()
        raise asyncio.CancelledError()

    mod.run_codex_research.return_value = ResearchResult(
        {"summary": "public", "sources": []},
        usage={
            "prompt_tokens": 123,
            "cached_input_tokens": 100,
            "completion_tokens": 12,
        },
    )
    mod.run_codex_research.side_effect = None
    monkeypatch.setattr(mod, "trace_inference", after_interrupted)
    with pytest.raises(asyncio.CancelledError):
        await mod.sync_daily_research(object(), settings(), NOW)
    saved = mod.archive_repository.enqueue_research_archive.await_args.args[6]
    assert saved["researchWorkload"]["inputTokens"] == 123
    assert saved["researchWorkload"]["cachedInputTokens"] == 100
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_before_operation_releases_proven_undispatched_guard(
    monkeypatch,
):
    mod, state, _run, _owner = worker(monkeypatch)

    async def before_cancelled(_settings, *, operation, **kwargs):
        # The operation callback was never called: no RPC or CLI could start.
        raise asyncio.CancelledError()

    monkeypatch.setattr(mod, "trace_inference", before_cancelled)
    with pytest.raises(asyncio.CancelledError):
        await mod.sync_daily_research(object(), settings(), NOW)
    assert state["terminal"][0]["status"] == "UNCERTAIN"
    mod.run_codex_research.assert_not_awaited()
    mod.release_model_guard.assert_awaited_once()


def test_dispatch_reserves_complete_source_verification_and_persistence():
    from backend.ai_usage.tracker import SNAPSHOT_TOTAL_BUDGET_SECONDS
    from backend.jobs.research_budget import model_budget_fits, research_request_budget

    with research_request_budget(960 + SNAPSHOT_TOTAL_BUDGET_SECONDS + 100):
        assert model_budget_fits(960) is False
    with research_request_budget(960 + SNAPSHOT_TOTAL_BUDGET_SECONDS + 250):
        assert model_budget_fits(960) is True
