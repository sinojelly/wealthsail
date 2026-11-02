"""Production manual research is an explicit website-only opt-in."""

import asyncio
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.dependencies import get_settings_dep
from test_research_debug_api import client
from test_research_debug_job import isolate, settings


def modes():
    return import_module("backend.macro.research_modes")


def test_manual_research_switch_defaults_off():
    assert Settings(_env_file=None).daily_research_manual_enabled is False


@pytest.mark.parametrize("env", ["development", "test", "staging", "production"])
@pytest.mark.parametrize("daily", [False, True])
def test_manual_gate_depends_on_its_explicit_switch_not_automatic_schedule(env, daily):
    value = settings(
        app_env=env, daily_research_enabled=daily, daily_research_manual_enabled=True
    )
    assert modes().research_request_mode_allowed(value, "MANUAL") is True
    value.daily_research_manual_enabled = False
    assert modes().research_request_mode_allowed(value, "MANUAL") is False


@pytest.mark.parametrize(
    "env,debug,daily,allowed",
    [
        ("development", True, True, True),
        ("test", True, True, True),
        ("production", True, True, False),
        ("staging", True, True, False),
        ("development", False, True, False),
        ("development", True, False, False),
    ],
)
def test_debug_gate_keeps_its_existing_development_contract(env, debug, daily, allowed):
    value = settings(
        app_env=env, daily_research_debug_enabled=debug, daily_research_enabled=daily
    )
    assert modes().research_request_mode_allowed(value, "DEBUG") is allowed


def test_preferred_mode_prioritizes_manual_and_never_accepts_unknown_modes():
    value = settings(daily_research_manual_enabled=True)
    assert modes().preferred_research_request_mode(value) == "MANUAL"
    value.daily_research_manual_enabled = False
    assert modes().preferred_research_request_mode(value) == "DEBUG"
    value.daily_research_debug_enabled = False
    assert modes().preferred_research_request_mode(value) is None
    for invalid in (None, "production", "debug", [], True):
        assert modes().research_request_mode_allowed(value, invalid) is False


def production_client(monkeypatch, *, enabled=True, role="OWNER", active=True):
    api, module = client(
        monkeypatch, env="production", role=role, active=active, daily=False
    )
    value = Settings(
        _env_file=None,
        app_env="production",
        daily_research_enabled=False,
        daily_research_debug_enabled=False,
        daily_research_manual_enabled=enabled,
    )
    api.app.dependency_overrides[get_settings_dep] = lambda: value
    return api, module


def test_production_owner_can_enqueue_manual_while_automatic_schedule_is_off(
    monkeypatch,
):
    api, module = production_client(monkeypatch)
    queue = AsyncMock(
        return_value={"id": str(uuid4()), "status": "QUEUED", "requestMode": "MANUAL"}
    )
    monkeypatch.setattr(module.repository, "enqueue_refresh", queue)
    response = api.get("/api/v1/investment/daily-research/refresh")
    assert response.json()["data"] == {"enabled": True, "request": None}
    response = api.post(
        "/api/v1/investment/daily-research/refresh", json={"commandKey": str(uuid4())}
    )
    assert response.status_code == 202
    assert queue.call_args.kwargs == {"request_mode": "MANUAL"}


@pytest.mark.parametrize(
    "options", [{"enabled": False}, {"role": "VIEWER"}, {"active": False}]
)
def test_production_manual_keeps_switch_and_live_operator_authorization(
    monkeypatch, options
):
    api, module = production_client(monkeypatch, **options)
    queue = AsyncMock()
    monkeypatch.setattr(module.repository, "enqueue_refresh", queue)
    assert (
        api.get("/api/v1/investment/daily-research/refresh").json()["data"]["enabled"]
        is False
    )
    assert (
        api.post(
            "/api/v1/investment/daily-research/refresh",
            json={"commandKey": str(uuid4())},
        ).status_code
        == 403
    )
    queue.assert_not_awaited()


def test_production_cannot_read_or_replay_saved_debug_requests(monkeypatch):
    api, module = production_client(monkeypatch)
    old = {"id": str(uuid4()), "status": "READY", "requestMode": "DEBUG"}
    monkeypatch.setattr(
        module.repository, "latest_refresh", AsyncMock(return_value=old)
    )
    monkeypatch.setattr(module.repository, "read_refresh", AsyncMock(return_value=old))
    monkeypatch.setattr(
        module.repository, "enqueue_refresh", AsyncMock(return_value=old)
    )
    capability = api.get("/api/v1/investment/daily-research/refresh").json()["data"]
    assert capability == {"enabled": True, "request": None}
    assert module.repository.latest_refresh.await_args.kwargs == {
        "allowed_modes": ["MANUAL"]
    }
    assert (
        api.get(f"/api/v1/investment/daily-research/refresh/{old['id']}").status_code
        == 403
    )
    assert (
        api.post(
            "/api/v1/investment/daily-research/refresh",
            json={"commandKey": str(uuid4())},
        ).status_code
        == 403
    )


def test_production_reads_only_its_manual_request_with_safe_diagnostics(monkeypatch):
    api, module = production_client(monkeypatch)
    value = {
        "id": str(uuid4()),
        "status": "FAILED",
        "requestMode": "MANUAL",
        "failureStage": "REPORT_VALIDATION",
        "failureReasonCode": "DIRECTION_VALIDATION_FAILED",
        "failureRuleId": "INDUSTRY_REVIEW_COVERAGE_MISSING",
        "failureItemIndex": None,
    }
    monkeypatch.setattr(
        module.repository, "read_refresh", AsyncMock(return_value=value)
    )
    response = api.get(f"/api/v1/investment/daily-research/refresh/{value['id']}")
    assert response.status_code == 200
    assert response.json()["data"] == value


def test_production_command_cannot_supply_its_own_request_mode(monkeypatch):
    api, module = production_client(monkeypatch)
    queue = AsyncMock()
    monkeypatch.setattr(module.repository, "enqueue_refresh", queue)
    response = api.post(
        "/api/v1/investment/daily-research/refresh",
        json={
            "commandKey": str(uuid4()),
            "requestMode": "MANUAL",
        },
    )
    assert response.status_code == 400
    queue.assert_not_awaited()


def manual_settings(**changes):
    return settings(
        app_env="production",
        daily_research_enabled=False,
        daily_research_debug_enabled=False,
        daily_research_manual_enabled=True,
        **changes,
    )


@pytest.mark.asyncio
async def test_consumer_freezes_manual_mode_and_binds_fresh_batch(monkeypatch):
    module, request = isolate(monkeypatch)
    request["request_mode"] = "MANUAL"
    result = await module.sync_debug_refresh(object(), manual_settings())
    assert result["status"] == "READY"
    assert module._claim.await_args.kwargs == {"allowed_modes": ["MANUAL"]}
    assert module.sync_alpha_public.await_args.kwargs == {
        "manual_request_id": request["id"]
    }
    assert module.sync_daily_research.await_args.kwargs == {
        "manual_request_id": request["id"],
        "source_batch_id": module.sync_alpha_public.return_value["batchId"],
    }


@pytest.mark.asyncio
async def test_consumer_rechecks_saved_request_mode_before_collecting(monkeypatch):
    module, request = isolate(monkeypatch)
    request["request_mode"] = "DEBUG"
    result = await module.sync_debug_refresh(object(), manual_settings())
    assert result["status"] == "FAILED"
    module.sync_alpha_public.assert_not_awaited()
    module.sync_daily_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_failure_preserves_safe_research_error_class(monkeypatch):
    module, request = isolate(monkeypatch)
    request["request_mode"] = "MANUAL"
    module.sync_daily_research.return_value = {
        "status": "FAILED",
        "runId": str(uuid4()),
        "errorClass": "ResearchValidationError",
    }
    await module.sync_debug_refresh(object(), manual_settings())
    assert module._finish.await_args.kwargs["error_class"] == "ResearchValidationError"


@pytest.mark.asyncio
async def test_manual_operator_revocation_after_collection_prevents_model(monkeypatch):
    module, request = isolate(monkeypatch)
    request["request_mode"] = "MANUAL"
    module._operator_active.side_effect = [True, False]
    result = await module.sync_debug_refresh(object(), manual_settings())
    assert result["status"] == "FAILED"
    assert module._finish.await_args.kwargs["error_class"] == "PermissionError"
    module.sync_alpha_public.assert_awaited_once()
    module.sync_daily_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_cancel_is_uncertain_and_never_retries_the_request(monkeypatch):
    module, request = isolate(monkeypatch)
    request["request_mode"] = "MANUAL"
    module.sync_daily_research.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await module.sync_debug_refresh(object(), manual_settings())
    assert module._finish.await_args.kwargs["status"] == "UNCERTAIN"
    assert (await module.sync_debug_refresh(object(), manual_settings()))[
        "status"
    ] == "IDLE"
    module.sync_daily_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_consumer_total_timeout_finishes_uncertain_without_model(
    monkeypatch,
):
    module, request = isolate(monkeypatch)
    request["request_mode"] = "MANUAL"
    monkeypatch.setattr(module, "REQUEST_TIMEOUT_SECONDS", 0.001, raising=False)

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    module.sync_alpha_public.side_effect = blocked
    result = await module.sync_debug_refresh(object(), manual_settings())
    assert result["status"] == "UNCERTAIN"
    assert module._finish.await_args.kwargs["error_class"] == "TimeoutError"
    module.sync_daily_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_cli_remains_development_debug_only_with_production_manual_enabled(
    monkeypatch,
):
    from backend.jobs import macro_worker, research_debug

    monkeypatch.setattr(research_debug, "get_settings", manual_settings)
    monkeypatch.setattr(macro_worker, "validate_macro_settings", lambda value: value)
    pool = AsyncMock()
    monkeypatch.setattr(research_debug, "create_pool", pool)
    with pytest.raises(ValueError):
        await research_debug.main(["--email", "operator@example.test", "--no-wait"])
    pool.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_starts_manual_consumer_without_automatic_daily_loop(monkeypatch):
    from backend.jobs import alpha_runtime, research_debug, daily_research

    seen = []

    async def manual(pool, settings, *, stop_event):
        seen.append("manual")
        await stop_event.wait()

    daily = AsyncMock()
    monkeypatch.setattr(research_debug, "run_debug_research_service", manual)
    monkeypatch.setattr(daily_research, "run_daily_research_service", daily)
    value = manual_settings()
    value.alpha_public_enabled = value.alpha_opportunity_enabled = False
    stop = asyncio.Event()
    task = asyncio.create_task(
        alpha_runtime.run_alpha_runtime(object(), value, stop_event=stop)
    )
    for _ in range(5):
        await asyncio.sleep(0)
    stop.set()
    await task
    assert seen == ["manual"]
    daily.assert_not_awaited()
