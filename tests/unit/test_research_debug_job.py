"""Explicit development runs refresh real inputs once without publishing."""

import asyncio
from contextlib import asynccontextmanager
from importlib import import_module, util
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_daily_research_job import NOW


def module():
    assert util.find_spec("backend.jobs.research_debug"), (
        "debug research worker missing"
    )
    return import_module("backend.jobs.research_debug")


def settings(**changes):
    return SimpleNamespace(
        **{
            "app_env": "development",
            "daily_research_debug_enabled": True,
            "daily_research_enabled": True,
            **changes,
        }
    )


def isolate(monkeypatch):
    mod = module()
    request = {"id": uuid4(), "tenant_id": uuid4(), "user_id": uuid4()}

    @asynccontextmanager
    async def connection(_pool):
        yield object()

    monkeypatch.setattr(mod, "system_connection", connection)
    monkeypatch.setattr(mod, "_claim", AsyncMock(side_effect=[request, None]))
    monkeypatch.setattr(mod, "_finish", AsyncMock())
    monkeypatch.setattr(mod, "_operator_active", AsyncMock(return_value=True))
    monkeypatch.setattr(mod, "_stage", AsyncMock(return_value=True))
    monkeypatch.setattr(
        mod,
        "sync_alpha_public",
        AsyncMock(return_value={"status": "PARTIAL", "batchId": str(uuid4())}),
    )
    monkeypatch.setattr(
        mod,
        "sync_daily_research",
        AsyncMock(return_value={"status": "READY", "runId": str(uuid4())}),
    )
    monkeypatch.setattr(mod, "now_shanghai", lambda: NOW)
    return mod, request


@pytest.mark.asyncio
async def test_collection_stage_is_saved_before_collection_and_a_lost_request_costs_nothing(monkeypatch):
    mod, request = isolate(monkeypatch)

    async def collect(*args, **kwargs):
        mod._stage.assert_awaited_once_with(args[0], request["id"], "COLLECTING")
        return {"status": "PARTIAL", "batchId": str(uuid4())}

    mod.sync_alpha_public.side_effect = collect
    assert (await mod.sync_debug_refresh(object(), settings()))["status"] == "READY"
    mod, _ = isolate(monkeypatch)
    mod._stage.return_value = False
    assert (await mod.sync_debug_refresh(object(), settings()))["status"] == "FAILED"
    mod.sync_alpha_public.assert_not_awaited()
    mod.sync_daily_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_debug_consumes_request_once_and_binds_fresh_batch_to_one_analysis(
    monkeypatch,
):
    mod, request = isolate(monkeypatch)
    result = await mod.sync_debug_refresh(object(), settings())
    assert result["status"] == "READY"
    batch = mod.sync_alpha_public.return_value["batchId"]
    assert mod.sync_alpha_public.await_args.kwargs["debug_request_id"] == request["id"]
    assert mod.sync_daily_research.await_args.kwargs == {
        "debug_request_id": request["id"],
        "source_batch_id": batch,
    }
    assert (await mod.sync_debug_refresh(object(), settings()))["status"] == "IDLE"
    mod.sync_alpha_public.assert_awaited_once()
    mod.sync_daily_research.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"app_env": "production"},
        {"app_env": "staging"},
        {"daily_research_debug_enabled": False},
        {"daily_research_enabled": False},
    ],
)
async def test_disabled_or_non_development_never_claims_or_collects(
    monkeypatch, changes
):
    mod, _ = isolate(monkeypatch)
    assert (await mod.sync_debug_refresh(object(), settings(**changes)))[
        "status"
    ] == "DISABLED"
    mod._claim.assert_not_awaited()
    mod.sync_alpha_public.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_collection_or_revoked_operator_never_dispatches_model(
    monkeypatch,
):
    mod, _ = isolate(monkeypatch)
    mod.sync_alpha_public.return_value = {"status": "FAILED"}
    assert (await mod.sync_debug_refresh(object(), settings()))["status"] == "FAILED"
    mod.sync_daily_research.assert_not_awaited()
    mod, _ = isolate(monkeypatch)
    mod._operator_active.return_value = False
    assert (await mod.sync_debug_refresh(object(), settings()))["status"] == "FAILED"
    mod.sync_alpha_public.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_model_consumes_request_without_automatic_retry(monkeypatch):
    mod, _ = isolate(monkeypatch)
    mod.sync_daily_research.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await mod.sync_debug_refresh(object(), settings())
    assert mod._finish.await_args.kwargs["status"] == "UNCERTAIN"
    assert (await mod.sync_debug_refresh(object(), settings()))["status"] == "IDLE"


@pytest.mark.asyncio
async def test_membership_revocation_after_collection_does_not_dispatch(
    monkeypatch,
):
    mod, _ = isolate(monkeypatch)
    mod._operator_active.side_effect = [True, False]
    assert (await mod.sync_debug_refresh(object(), settings()))["status"] == "FAILED"
    mod.sync_daily_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_clock_regression_after_collection_does_not_dispatch(monkeypatch):
    from datetime import timedelta

    mod, _ = isolate(monkeypatch)
    clock = iter([NOW, NOW, NOW - timedelta(seconds=1), NOW])
    monkeypatch.setattr(mod, "now_shanghai", lambda: next(clock))
    assert (await mod.sync_debug_refresh(object(), settings()))["status"] == "FAILED"
    mod.sync_daily_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_cli_prints_original_command_key_for_interrupted_operator_replay(
    monkeypatch, capsys
):
    from backend.jobs import macro_worker

    mod = module()
    key = uuid4()
    pool = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(macro_worker, "validate_macro_settings", lambda value: value)
    monkeypatch.setattr(
        mod, "get_settings", lambda: settings(worker_database_url="test")
    )
    monkeypatch.setattr(mod, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(mod, "verify_runtime_login_boundary", AsyncMock())
    monkeypatch.setattr(
        mod,
        "enqueue_cli",
        AsyncMock(return_value={"id": str(uuid4()), "status": "QUEUED"}),
    )
    assert (
        await mod.main(
            [
                "--email",
                "local-owner@wealthsail.test",
                "--request-key",
                str(key),
                "--no-wait",
            ]
        )
        == 0
    )
    assert str(key) in capsys.readouterr().out
