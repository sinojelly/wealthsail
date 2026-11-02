"""Formal manual execution shares validation and global model occupation."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.macro.daily_research import ResearchValidationError
from backend.macro.daily_pipeline_repository import (
    source_quality as real_source_quality,
)
from backend.macro.model_dispatch_repository import ResearchModelBusy
from tests.unit.test_daily_pipeline import valid_partial_bundle
from tests.unit.test_daily_research_job import NOW, isolate, settings


def manual_boundary(monkeypatch):
    from backend.macro import alpha_repository, daily_pipeline_repository

    mod, state = isolate(monkeypatch)
    request, batch = uuid4(), uuid4()
    bundle = {**valid_partial_bundle(NOW), "batchId": str(batch)}
    read = AsyncMock(return_value=bundle)
    monkeypatch.setattr(alpha_repository, "read_alpha_public", read)
    # Exercise the real quality gate even though legacy scheduled test fixtures
    # intentionally replace that dependency with a constant decision.
    monkeypatch.setattr(
        daily_pipeline_repository, "source_quality", real_source_quality
    )
    for name in ("lock_model_dispatch", "create_model_guard", "release_model_guard"):
        monkeypatch.setattr(mod, name, AsyncMock(return_value=uuid4()), raising=False)
    configured = settings(
        app_env="production",
        daily_research_enabled=False,
        daily_research_debug_enabled=False,
        daily_research_manual_enabled=True,
    )
    return mod, state, configured, request, batch, read


@pytest.mark.asyncio
async def test_production_manual_runs_when_automatic_and_debug_are_disabled(
    monkeypatch,
):
    mod, state, configured, request, batch, read = manual_boundary(monkeypatch)
    result = await mod.sync_daily_research(
        object(), configured, NOW, manual_request_id=request, source_batch_id=batch
    )
    assert result["status"] == "READY"
    mod.consumed.assert_not_awaited()
    assert read.await_args.kwargs == {
        "manual_request_id": request,
        "source_batch_id": batch,
    }
    assert mod.load_public_packet.await_args.kwargs == {
        "manual_request_id": request,
        "source_batch_id": batch,
    }
    assert mod.prepare_research_inputs.await_args.kwargs == {
        "manual_request_id": request,
        "source_batch_id": batch,
    }
    assert state["terminal"][0]["payload"]["inputQuality"]["allowed"] is True
    mod.release_model_guard.assert_awaited_once()
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["disabled", "bad_quality", "wrong_batch"])
async def test_invalid_manual_request_never_claims_or_dispatches_model(
    monkeypatch, problem
):
    mod, _state, configured, request, batch, read = manual_boundary(monkeypatch)
    if problem == "disabled":
        configured.daily_research_manual_enabled = False
    elif problem == "bad_quality":
        read.return_value["fundHistories"] = []
    else:
        read.return_value["batchId"] = str(uuid4())
    outcome = await mod.sync_daily_research(
        object(), configured, NOW, manual_request_id=request, source_batch_id=batch
    )
    assert outcome["status"] == ("DISABLED" if problem == "disabled" else "FAILED")
    mod.create_model_guard.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_manual_dispatch_waits_outside_transaction_without_consuming_run(
    monkeypatch,
):
    mod, state, configured, request, batch, _read = manual_boundary(monkeypatch)
    mod.lock_model_dispatch.side_effect = [ResearchModelBusy(), None]

    async def wait():
        assert state["active"] == 0
        assert state["consumed"] is False
        mod.run_codex_research.assert_not_awaited()

    monkeypatch.setattr(mod, "_wait_model_slot", wait, raising=False)
    result = await mod.sync_daily_research(
        object(), configured, NOW, manual_request_id=request, source_batch_id=batch
    )
    assert result["status"] == "READY"
    assert mod.lock_model_dispatch.await_count == 2
    mod.create_model_guard.assert_awaited_once()
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["validation", "persist"])
async def test_confirmed_model_return_releases_guard_even_if_later_business_fails(
    monkeypatch, failure
):
    mod, _state, configured, request, batch, _read = manual_boundary(monkeypatch)
    if failure == "validation":

        def invalid(*args, **kwargs):
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                rule_id="DIRECTION_EVIDENCE_INSUFFICIENT",
                item_index=0,
            )

        monkeypatch.setattr(mod, "validate_report", invalid)
        result = await mod.sync_daily_research(
            object(), configured, NOW, manual_request_id=request, source_batch_id=batch
        )
        assert result["status"] == "FAILED"
    else:
        monkeypatch.setattr(
            mod, "finish_daily_run", AsyncMock(side_effect=TimeoutError())
        )
        with pytest.raises(TimeoutError):
            await mod.sync_daily_research(
                object(),
                configured,
                NOW,
                manual_request_id=request,
                source_batch_id=batch,
            )
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_unconfirmed_model_exit_does_not_release_global_guard(
    monkeypatch, cancelled
):
    mod, state, configured, request, batch, _read = manual_boundary(monkeypatch)
    mod.run_codex_research.side_effect = (
        asyncio.CancelledError() if cancelled else TimeoutError()
    )
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await mod.sync_daily_research(
                object(),
                configured,
                NOW,
                manual_request_id=request,
                source_batch_id=batch,
            )
    else:
        result = await mod.sync_daily_research(
            object(), configured, NOW, manual_request_id=request, source_batch_id=batch
        )
        assert result["status"] == "UNCERTAIN"
    assert state["terminal"][0]["status"] == "UNCERTAIN"
    mod.release_model_guard.assert_not_awaited()
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_cancellation_in_usage_bookkeeping_after_model_return_releases_guard(
    monkeypatch, remote
):
    from backend.ai_usage import tracker
    from backend.integrations import codex_client

    mod, state, configured, request, batch, _read = manual_boundary(monkeypatch)

    async def cancelled_after_operation(_settings, *, operation, **kwargs):
        await operation()
        raise asyncio.CancelledError()

    if remote:
        configured.codex_runner_url = "http://fixture"
        monkeypatch.setattr(
            codex_client, "preflight", AsyncMock(return_value={"version": "fixture"})
        )
        monkeypatch.setattr(
            codex_client, "_research", AsyncMock(return_value={"summary": "public"})
        )
        monkeypatch.setattr(tracker, "trace_inference", cancelled_after_operation)
    else:
        monkeypatch.setattr(mod, "trace_inference", cancelled_after_operation)
    with pytest.raises(asyncio.CancelledError):
        await mod.sync_daily_research(
            object(), configured, NOW, manual_request_id=request, source_batch_id=batch
        )
    assert state["terminal"][0]["status"] == "UNCERTAIN"
    mod.release_model_guard.assert_awaited_once()
