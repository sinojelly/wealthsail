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
