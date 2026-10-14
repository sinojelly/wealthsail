"""The macro-only runtime has no report, message or trading-calendar loop."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from importlib import import_module, util
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.config import Settings

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _worker():
    name = "backend.jobs.macro_worker"
    assert util.find_spec(name), "independent macro worker implementation is missing"
    return import_module(name)


def _settings(**changes):
    values = {
        "_env_file": None,
        "app_env": "production",
        "worker_database_url": "postgresql://worker@example.test/wealthsail",
        "worker_database_pool_mode": "session",
    }
    values.update(changes)
    return Settings(**values)


class Clock:
    def __init__(self, now, *, cycles):
        self.now = now
        self.delays = []
        self.cycles = cycles

    def read(self):
        return self.now

    async def wait(self, stop_event, seconds):
        assert seconds > 0, "failure retry must never spin"
        self.delays.append(seconds)
        if len(self.delays) > self.cycles:
            stop_event.set()
            return
        self.now += timedelta(seconds=seconds)


def _collectors(monkeypatch, *, startup="SKIPPED", results=None):
    worker = _worker()
    initial = AsyncMock(
        return_value={
            "status": startup,
            "reason": "test",
            "observations": 0,
            "coverage": {},
        }
    )
    refreshed = AsyncMock(
        side_effect=[
            {"status": value, "reason": "test", "observations": 0, "coverage": {}}
            for value in results or []
        ]
    )
    monkeypatch.setattr(worker, "ensure_macro_history", initial)
    monkeypatch.setattr(worker, "refresh_macro_history", refreshed)
    return worker, initial, refreshed


@pytest.mark.asyncio
async def test_daily_0630_runs_once_per_day_including_saturday_and_sunday(monkeypatch):
    start = datetime(2026, 10, 16, 6, 29, tzinfo=SHANGHAI)  # Friday
    clock = Clock(start, cycles=3)
    worker, initial, refreshed = _collectors(monkeypatch, results=["COMPLETED"] * 3)
    await worker.run_macro_service(object(), now_provider=clock.read, wait=clock.wait)
    initial.assert_awaited_once()
    assert [call.args[1] for call in refreshed.await_args_list] == [
        datetime(2026, 10, day, 6, 30, tzinfo=SHANGHAI) for day in [16, 17, 18]
    ]
    assert clock.delays[:3] == [60, 86400, 86400]


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", ["PARTIAL", "UNAVAILABLE"])
async def test_failed_startup_retries_after_one_hour_then_returns_to_daily_schedule(
    monkeypatch, initial_state
):
    start = datetime(2026, 10, 16, 5, tzinfo=SHANGHAI)
    clock = Clock(start, cycles=2)
    worker, _, refreshed = _collectors(
        monkeypatch, startup=initial_state, results=["COMPLETED", "COMPLETED"]
    )
    await worker.run_macro_service(object(), now_provider=clock.read, wait=clock.wait)
    assert [call.args[1] for call in refreshed.await_args_list] == [
        start + timedelta(hours=1),
        start + timedelta(hours=1, minutes=30),
    ]
    assert clock.delays[:2] == [3600, 1800]


@pytest.mark.asyncio
async def test_partial_daily_retry_crosses_midnight_without_duplicate_daily_collection(
    monkeypatch,
):
    start = datetime(2026, 10, 16, 22, 30, tzinfo=SHANGHAI)
    clock = Clock(start, cycles=4)
    worker, _, refreshed = _collectors(
        monkeypatch,
        startup="PARTIAL",
        results=["PARTIAL", "UNAVAILABLE", "COMPLETED", "COMPLETED"],
    )
    await worker.run_macro_service(object(), now_provider=clock.read, wait=clock.wait)
    assert [call.args[1] for call in refreshed.await_args_list] == [
        start + timedelta(hours=1),
        start + timedelta(hours=2),
        start + timedelta(hours=3),
        datetime(2026, 10, 17, 6, 30, tzinfo=SHANGHAI),
    ]
    assert clock.delays[:4] == [3600, 3600, 3600, 18000]


@pytest.mark.asyncio
async def test_successful_startup_after_daily_time_does_not_immediately_duplicate_fetch(
    monkeypatch,
):
    start = datetime(2026, 10, 16, 10, tzinfo=SHANGHAI)
    clock = Clock(start, cycles=1)
    worker, _, refreshed = _collectors(
        monkeypatch, startup="COMPLETED", results=["COMPLETED"]
    )
    await worker.run_macro_service(object(), now_provider=clock.read, wait=clock.wait)
    assert refreshed.await_args.args[1] == datetime(
        2026, 10, 17, 6, 30, tzinfo=SHANGHAI
    )


@pytest.mark.asyncio
async def test_utc_clock_still_schedules_shanghai_0630(monkeypatch):
    clock = Clock(datetime(2026, 10, 15, 22, 29, tzinfo=timezone.utc), cycles=1)
    worker, _, refreshed = _collectors(monkeypatch, results=["COMPLETED"])
    await worker.run_macro_service(object(), now_provider=clock.read, wait=clock.wait)
    assert refreshed.await_args.args[1] == datetime(
        2026, 10, 16, 6, 30, tzinfo=SHANGHAI
    )
    assert clock.delays[0] == 60


@pytest.mark.asyncio
async def test_unexpected_collection_exception_retries_hourly_and_preserves_service(
    monkeypatch,
):
    clock = Clock(datetime(2026, 10, 16, 7, tzinfo=SHANGHAI), cycles=1)
    worker, initial, refreshed = _collectors(monkeypatch, results=["COMPLETED"])
    initial.side_effect = RuntimeError("provider unavailable")
    await worker.run_macro_service(object(), now_provider=clock.read, wait=clock.wait)
    assert clock.delays[0] == 3600
    assert refreshed.await_count == 1


@pytest.mark.asyncio
async def test_pre_set_stop_event_does_not_start_collection(monkeypatch):
    worker, initial, refreshed = _collectors(monkeypatch)
    stopped = asyncio.Event()
    stopped.set()
    await worker.run_macro_service(object(), stop_event=stopped)
    initial.assert_not_awaited()
    refreshed.assert_not_awaited()


def test_production_macro_worker_requires_only_its_database_authority():
    settings = _settings()
    assert _worker().validate_macro_settings(settings) is settings
    assert not settings.wecom_corp_secret and not settings.routing_encryption_key


@pytest.mark.parametrize(
    "changes, error",
    [
        ({"worker_database_url": " "}, "WORKER_DATABASE_URL"),
        ({"worker_database_pool_mode": None}, "WORKER_DATABASE_POOL_MODE"),
        ({"worker_database_pool_mode": "transaction"}, "transaction"),
        (
            {
                "worker_database_url": "postgresql://worker@aws-0.pooler.supabase.com:6543/db",
                "worker_database_pool_mode": "session",
            },
            "transaction",
        ),
        ({"database_url": "postgresql://api@example.test/db"}, "DATABASE_URL"),
        ({"jwt_secret": "x" * 40}, "JWT_SECRET"),
        ({"refresh_token_hmac_key": "x" * 40}, "REFRESH_TOKEN_HMAC_KEY"),
        ({"password_reset_hmac_key": "x" * 40}, "PASSWORD_RESET_HMAC_KEY"),
        ({"binding_hmac_key": "x" * 40}, "BINDING_HMAC_KEY"),
        ({"wecom_callback_token": "x" * 40}, "WECOM_CALLBACK_TOKEN"),
        ({"wecom_callback_aes_key": "x" * 43}, "WECOM_CALLBACK_AES_KEY"),
    ],
)
def test_macro_worker_rejects_unsupported_topology_and_api_authority(changes, error):
    with pytest.raises(ValueError, match=error):
        _worker().validate_macro_settings(_settings(**changes))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, exit_code", [("COMPLETED", 0), ("PARTIAL", 1), ("UNAVAILABLE", 1)]
)
async def test_once_forces_refresh_prints_source_coverage_and_closes_pool(
    monkeypatch, capsys, status, exit_code
):
    worker = _worker()
    settings = _settings()
    pool = type("Pool", (), {"close": AsyncMock()})()
    create = AsyncMock(return_value=pool)
    verify = AsyncMock()
    forced = AsyncMock(
        return_value={
            "status": status,
            "reason": "source-state",
            "observations": 180,
            "coverage": {
                "CPI_YOY": {"refreshed": False, "missingPeriods": ["2023-02"]}
            },
        }
    )
    ensure = AsyncMock()
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "create_pool", create)
    monkeypatch.setattr(worker, "verify_runtime_login_boundary", verify)
    monkeypatch.setattr(worker, "refresh_macro_history", forced)
    monkeypatch.setattr(worker, "ensure_macro_history", ensure)
    assert await worker.main(["--once"]) == exit_code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status
    assert result["coverage"]["CPI_YOY"]["missingPeriods"] == ["2023-02"]
    forced.assert_awaited_once()
    ensure.assert_not_awaited()
    assert create.await_args.args == (settings.worker_database_url,)
    assert create.await_args.kwargs["runtime_role"] == "wealthsail_worker"
    assert verify.await_args.kwargs == {"process": "worker"}
    pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_role_boundary_failure_closes_pool_without_collecting(monkeypatch):
    worker = _worker()
    pool = type("Pool", (), {"close": AsyncMock()})()
    refresh = AsyncMock()
    monkeypatch.setattr(
        worker, "get_settings", lambda: _settings(app_env="development")
    )
    monkeypatch.setattr(worker, "create_pool", AsyncMock(return_value=pool))
    verify = AsyncMock(
        side_effect=RuntimeError("Worker login cannot SET ROLE wealthsail_worker")
    )
    monkeypatch.setattr(worker, "verify_runtime_login_boundary", verify)
    monkeypatch.setattr(worker, "refresh_macro_history", refresh)
    with pytest.raises(RuntimeError, match="Worker login"):
        await worker.main(["--once"])
    assert "skip" not in verify.await_args.kwargs
    refresh.assert_not_awaited()
    pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_runtime_only_launches_macro_service_and_closes_pool(monkeypatch):
    worker = _worker()
    pool = type("Pool", (), {"close": AsyncMock()})()
    service = AsyncMock()
    monkeypatch.setattr(worker, "get_settings", lambda: _settings())
    monkeypatch.setattr(worker, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(worker, "verify_runtime_login_boundary", AsyncMock())
    monkeypatch.setattr(worker, "run_macro_service", service)
    assert await worker.main([]) == 0
    assert service.await_args.args == (pool,)
    pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_cancellation_waits_for_collection_cleanup_before_pool_close(
    monkeypatch,
):
    worker = _worker()
    entered, cleanup_started, release_cleanup = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    pool = type("Pool", (), {"close": AsyncMock()})()

    async def slow_service(pool, *, stop_event):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()

    monkeypatch.setattr(worker, "get_settings", lambda: _settings())
    monkeypatch.setattr(worker, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(worker, "verify_runtime_login_boundary", AsyncMock())
    monkeypatch.setattr(worker, "run_macro_service", slow_service)
    main = asyncio.create_task(worker.main([]))
    await asyncio.wait_for(entered.wait(), 1)
    main.cancel()
    await asyncio.wait_for(cleanup_started.wait(), 1)
    pool.close.assert_not_awaited()
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(main, 1)
    pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_collection_error_log_never_contains_provider_or_database_secret(
    monkeypatch, caplog
):
    worker, initial, _ = _collectors(monkeypatch)
    initial.side_effect = RuntimeError("postgresql://worker:secret@example.test/db")
    clock = Clock(datetime(2026, 10, 16, 7, tzinfo=SHANGHAI), cycles=0)
    await worker.run_macro_service(object(), now_provider=clock.read, wait=clock.wait)
    assert "error_class=RuntimeError" in caplog.text
    assert "secret" not in caplog.text and "postgresql" not in caplog.text


@pytest.mark.asyncio
async def test_recent_yesterday_skip_still_catches_up_today_daily_collection(
    monkeypatch,
):
    start = datetime(2026, 10, 16, 10, tzinfo=SHANGHAI)
    clock = Clock(start, cycles=0)
    worker, initial, refreshed = _collectors(monkeypatch, results=["COMPLETED"])
    initial.return_value["coverage"] = {
        field: {"lastCollectedAt": (start - timedelta(hours=23)).isoformat()}
        for field in ["CPI_YOY", "PMI", "PPI_YOY", "M2_YOY"]
    }
    await worker.run_macro_service(object(), now_provider=clock.read, wait=clock.wait)
    assert refreshed.await_count == 1
    assert refreshed.await_args.args[1] == start
    assert clock.delays[0] == 73800


@pytest.mark.asyncio
async def test_recent_today_collection_after_0630_skip_does_not_duplicate_daily_fetch(
    monkeypatch,
):
    start = datetime(2026, 10, 16, 10, tzinfo=SHANGHAI)
    clock = Clock(start, cycles=0)
    worker, initial, refreshed = _collectors(monkeypatch)
    initial.return_value["coverage"] = {
        field: {"lastCollectedAt": start.replace(hour=7).isoformat()}
        for field in ["CPI_YOY", "PMI", "PPI_YOY", "M2_YOY"]
    }
    await worker.run_macro_service(object(), now_provider=clock.read, wait=clock.wait)
    refreshed.assert_not_awaited()
    assert clock.delays[0] == 73800


@pytest.mark.asyncio
async def test_fatal_service_error_removes_signal_handlers_and_closes_pool(monkeypatch):
    worker = _worker()
    pool = type("Pool", (), {"close": AsyncMock()})()
    installed, removed = [], []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback: installed.append(sig)
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: removed.append(sig))
    monkeypatch.setattr(worker, "get_settings", lambda: _settings())
    monkeypatch.setattr(worker, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(worker, "verify_runtime_login_boundary", AsyncMock())
    monkeypatch.setattr(
        worker,
        "run_macro_service",
        AsyncMock(side_effect=RuntimeError("fatal service error")),
    )
    with pytest.raises(RuntimeError, match="fatal service error"):
        await worker.main([])
    assert set(removed) == set(installed)
    pool.close.assert_awaited_once()
