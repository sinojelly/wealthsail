"""Worker startup must bootstrap empty global read models before loops start."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

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
    monkeypatch.setattr(worker, "Scheduler", lambda *_args, **_kwargs: Component("scheduler"))
    monkeypatch.setattr(worker, "OutboxConsumer", lambda *_args: Component("outbox"))
    monkeypatch.setattr(worker, "DataTaskConsumer", lambda *_args: Component("data-task"))
    monkeypatch.setattr(worker, "_recovery_loop", idle_recovery)
    monkeypatch.setattr(worker.asyncio, "Event", ImmediateEvent)
    monkeypatch.setattr(worker, "close_http_client", AsyncMock())

    await worker.main()

    assert events[:5] == [
        "stale-run-recovery",
        "market-hot-bootstrap",
        "scheduler-start",
        "outbox-start",
        "data-task-start",
    ]
