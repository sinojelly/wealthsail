"""Own Phase 2 service lifetimes in either supported worker entrypoint."""

import asyncio
import logging

logger = logging.getLogger("wealthsail.alpha_runtime")


async def run_alpha_runtime(
    pool, settings, *, stop_event=None, include_daily=True, services=None
):
    stop_event = stop_event or asyncio.Event()
    if services is None:
        from backend.jobs.alpha_public import run_alpha_public_service
        from backend.jobs.alpha_opportunities import run_alpha_opportunity_service
        from backend.jobs.daily_research import run_daily_research_service

        services = []
        if getattr(settings, "alpha_public_enabled", False):
            services.append(run_alpha_public_service)
        if getattr(settings, "alpha_opportunity_enabled", False):

            async def opportunities(pool, *, stop_event):
                await run_alpha_opportunity_service(
                    pool, settings, stop_event=stop_event
                )

            services.append(opportunities)
        if include_daily and getattr(settings, "daily_research_enabled", False):

            async def daily(pool, *, stop_event):
                await run_daily_research_service(pool, settings, stop_event=stop_event)

            services.append(daily)

    async def guarded(service):
        try:
            await service(pool, stop_event=stop_event)
        except Exception as exc:  # noqa: BLE001 -- independent loops do not stop existing reports
            logger.warning("alpha_service_failed error_class=%s", type(exc).__name__)

    tasks = [asyncio.create_task(guarded(service)) for service in services]
    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
