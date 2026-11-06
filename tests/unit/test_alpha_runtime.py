"""The full worker owns and cancels all new services without model duplicates."""

import asyncio
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_phase2_runtime_starts_enabled_services_and_awaits_shutdown():
    from backend.jobs.alpha_runtime import run_alpha_runtime

    seen = []

    async def service(pool, *, stop_event):
        seen.append("started")
        try:
            await stop_event.wait()
        finally:
            seen.append("stopped")

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_alpha_runtime(
            object(),
            SimpleNamespace(
                alpha_public_enabled=True,
                alpha_opportunity_enabled=True,
                daily_research_enabled=True,
            ),
            stop_event=stop,
            services=[service, service, service],
        )
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert seen.count("started") == 3
    stop.set()
    await task
    assert seen.count("stopped") == 3


@pytest.mark.asyncio
async def test_nav_warming_runs_beside_archive_when_inference_is_disabled(monkeypatch):
    from backend.investment import opportunity_fund_nav
    from backend.jobs import research_archive
    from backend.jobs.alpha_runtime import run_alpha_runtime

    archive_started, nav_started, nav_cancelled = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def archive(_pool, *, stop_event):
        archive_started.set()
        await stop_event.wait()

    async def slow_nav(_pool, *, stop_event):
        nav_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            nav_cancelled.set()

    monkeypatch.setattr(research_archive, "run_research_archive_service", archive)
    monkeypatch.setattr(
        opportunity_fund_nav,
        "run_opportunity_fund_nav_service",
        slow_nav,
        raising=False,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_alpha_runtime(
            object(), SimpleNamespace(daily_research_enabled=False), stop_event=stop
        )
    )
    try:
        await asyncio.wait_for(archive_started.wait(), 1)
        await asyncio.wait_for(nav_started.wait(), 1)
        assert not task.done()
    finally:
        stop.set()
        await task
    assert nav_cancelled.is_set()
