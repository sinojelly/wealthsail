"""Worker startup must bootstrap empty global read models before loops start."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from types import ModuleType
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend import worker
from backend.config import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("bootstrap_fails", [False, True])
async def test_worker_bootstraps_market_hot_before_background_loops(
    monkeypatch, bootstrap_fails: bool
) -> None:
    events: list[str] = []
    settings = Settings(
        _env_file=None,
        worker_database_url="postgresql://worker@example.test/wealthsail",
        worker_database_pool_mode="session",
    )

    class Pool:
        close = AsyncMock()

    pool = Pool()

    async def bootstrap(received_pool):
        assert received_pool is pool
        events.append("market-hot-bootstrap")
        if bootstrap_fails:
            raise RuntimeError("provider unavailable")
        return {"status": "COMPLETED", "qualityStatus": "ESTIMATED"}

    async def claim_stale_runs(received_pool, received_settings):
        assert received_pool is pool
        assert received_settings is settings
        events.append("stale-run-recovery")

    class Component:
        def __init__(self, name: str) -> None:
            self.name = name

        def start(self) -> None:
            events.append(f"{self.name}-start")

        async def stop(self) -> None:
            events.append(f"{self.name}-stop")

    class ImmediateEvent:
        def set(self) -> None:
            return None

        async def wait(self) -> None:
            return None

    async def idle_recovery(*_args) -> None:
        await asyncio.Future()

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(
        worker,
        "validate_runtime_settings",
        lambda _settings, *, process: settings,
    )
    monkeypatch.setattr(worker, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(worker, "verify_runtime_login_boundary", AsyncMock())
    monkeypatch.setattr(
        worker.runner,
        "claim_stale_runs",
        AsyncMock(side_effect=claim_stale_runs),
    )
    monkeypatch.setattr(
        worker.market_service,
        "ensure_market_hot_snapshot",
        AsyncMock(side_effect=bootstrap),
    )
    monkeypatch.setattr(
        worker.market_service,
        "ensure_trading_calendar",
        AsyncMock(
            side_effect=lambda received_pool: events.append("calendar-bootstrap")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        worker, "Scheduler", lambda *_args, **_kwargs: Component("scheduler")
    )
    monkeypatch.setattr(worker, "OutboxConsumer", lambda *_args: Component("outbox"))
    monkeypatch.setattr(
        worker, "DataTaskConsumer", lambda *_args: Component("data-task")
    )
    monkeypatch.setattr(worker, "_recovery_loop", idle_recovery)
    monkeypatch.setattr(worker.asyncio, "Event", ImmediateEvent)
    monkeypatch.setattr(worker, "close_http_client", AsyncMock())

    await worker.main()

    assert events[:6] == [
        "stale-run-recovery",
        "calendar-bootstrap",
        "market-hot-bootstrap",
        "scheduler-start",
        "outbox-start",
        "data-task-start",
    ]


def _patch_worker_loops(monkeypatch, events: list[str], stop_event):
    settings = Settings(
        _env_file=None,
        worker_database_url="postgresql://worker@example.test/wealthsail",
        worker_database_pool_mode="session",
    )

    async def close_pool():
        events.append("pool-close")

    class Pool:
        close = AsyncMock(side_effect=close_pool)

    pool = Pool()

    class Component:
        def __init__(self, name):
            self.name = name

        def start(self):
            events.append(f"{self.name}-start")

        async def stop(self):
            events.append(f"{self.name}-stop")

    async def recovery(*_args):
        try:
            await asyncio.Future()
        finally:
            events.append("recovery-stopped")

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(
        worker, "validate_runtime_settings", lambda _settings, *, process: settings
    )
    monkeypatch.setattr(worker, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(worker, "verify_runtime_login_boundary", AsyncMock())
    monkeypatch.setattr(worker.runner, "claim_stale_runs", AsyncMock())
    monkeypatch.setattr(
        worker.market_service,
        "ensure_trading_calendar",
        AsyncMock(return_value={"status": "READY"}),
    )
    monkeypatch.setattr(
        worker.market_service,
        "ensure_market_hot_snapshot",
        AsyncMock(return_value={"status": "READY"}),
    )
    monkeypatch.setattr(
        worker, "Scheduler", lambda *_args, **_kwargs: Component("scheduler")
    )
    monkeypatch.setattr(worker, "OutboxConsumer", lambda *_args: Component("outbox"))
    monkeypatch.setattr(
        worker, "DataTaskConsumer", lambda *_args: Component("data-task")
    )
    monkeypatch.setattr(worker, "_recovery_loop", recovery)
    monkeypatch.setattr(worker.asyncio, "Event", lambda: stop_event)
    monkeypatch.setattr(worker, "close_http_client", AsyncMock())
    return pool


def _patch_macro_provider(monkeypatch, ensure):
    module = ModuleType("backend.jobs.investment_macro_refresh")
    module.ensure_macro_history = ensure
    monkeypatch.setitem(sys.modules, module.__name__, module)


@pytest.mark.asyncio
async def test_macro_bootstrap_runs_after_loops_start_and_is_awaited_before_pool_close(
    monkeypatch,
):
    events: list[str] = []
    now = datetime(2026, 10, 14, 13, tzinfo=ZoneInfo("Asia/Shanghai"))
    received = []

    class StopAfterOneTick:
        def set(self):
            pass

        async def wait(self):
            await asyncio.sleep(0)

    pool = _patch_worker_loops(monkeypatch, events, StopAfterOneTick())

    async def ensure(received_pool, received_now):
        received.append((received_pool, received_now))
        events.append("macro-start")
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            events.append("macro-stopped")

    _patch_macro_provider(monkeypatch, ensure)
    monkeypatch.setattr(worker, "now_shanghai", lambda: now, raising=False)

    await worker.main()

    assert received == [(pool, now)]
    assert events[:3] == ["scheduler-start", "outbox-start", "data-task-start"]
    assert events.index("macro-start") > events.index("data-task-start")
    assert events.index("macro-stopped") < events.index("pool-close")
    assert events.index("recovery-stopped") < events.index("pool-close")


@pytest.mark.asyncio
async def test_macro_bootstrap_failure_keeps_existing_loops_alive(monkeypatch, caplog):
    events: list[str] = []

    class StopAfterOneTick:
        def set(self):
            pass

        async def wait(self):
            await asyncio.sleep(0)

    pool = _patch_worker_loops(monkeypatch, events, StopAfterOneTick())
    ensure = AsyncMock(side_effect=RuntimeError("macro provider unavailable"))
    _patch_macro_provider(monkeypatch, ensure)

    await worker.main()

    ensure.assert_awaited_once()
    assert ensure.await_args.args[0] is pool
    assert str(ensure.await_args.args[1].tzinfo) == "Asia/Shanghai"
    assert "investment_macro_bootstrap_failed" in caplog.text
    assert events[:3] == ["scheduler-start", "outbox-start", "data-task-start"]
    assert events[-1] == "pool-close"


@pytest.mark.asyncio
async def test_worker_cancellation_also_joins_owned_macro_bootstrap(monkeypatch):
    events: list[str] = []
    startup = asyncio.Event()

    class WaitForCancellation:
        def set(self):
            pass

        async def wait(self):
            await asyncio.Future()

    _patch_worker_loops(monkeypatch, events, WaitForCancellation())

    async def ensure(*_args):
        startup.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            events.append("macro-stopped")

    _patch_macro_provider(monkeypatch, ensure)
    running = asyncio.create_task(worker.main())
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert startup.is_set()
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    assert events.index("macro-stopped") < events.index("pool-close")
    assert events.index("recovery-stopped") < events.index("pool-close")
