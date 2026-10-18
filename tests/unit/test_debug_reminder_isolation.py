from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from test_daily_research_job import NOW


@pytest.mark.asyncio
async def test_automatic_reminder_selects_only_scheduled_report(monkeypatch):
    from backend.jobs import alpha_opportunities

    conn = AsyncMock()
    conn.fetchrow.return_value = None

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(alpha_opportunities, "system_connection", connection)
    await alpha_opportunities._today_report(object(), NOW)
    assert "r.run_mode='SCHEDULED'" in conn.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_runtime_owns_debug_consumer_without_enabling_scheduled_daily(
    monkeypatch,
):
    import asyncio
    from types import SimpleNamespace
    from backend.jobs import research_debug, alpha_runtime

    seen = []

    async def consume(pool, settings, *, stop_event):
        seen.append("start")
        try:
            await stop_event.wait()
        finally:
            seen.append("stop")

    monkeypatch.setattr(research_debug, "run_debug_research_service", consume)
    stop = asyncio.Event()
    task = asyncio.create_task(
        alpha_runtime.run_alpha_runtime(
            object(),
            SimpleNamespace(
                app_env="development",
                daily_research_debug_enabled=True,
                daily_research_enabled=True,
                alpha_public_enabled=False,
                alpha_opportunity_enabled=False,
            ),
            stop_event=stop,
            include_daily=False,
        )
    )
    for _ in range(5):
        await asyncio.sleep(0)
    stop.set()
    await task
    assert seen == ["start", "stop"]
