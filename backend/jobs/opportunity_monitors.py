"""Read opted-in research under actor RLS and write product-only transitions."""

import asyncio
import logging

from backend.common.database import system_connection, tenant_transaction
from backend.dependencies import now_shanghai
from backend.investment import decision_repository as repository

logger = logging.getLogger("wealthsail.opportunity_monitors")


async def get_context(*args, **kwargs):
    # Assembly imports decision_context, so import at call time rather than startup.
    from backend.investment.opportunity_service import get_context as assembly

    return await assembly(*args, **kwargs)


async def sync_opportunity_monitors(pool, now=None):
    now = now or now_shanghai()
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """SELECT m.* FROM risk.opportunity_condition_monitors m
          JOIN identity.tenant_members actor ON actor.tenant_id=m.tenant_id AND actor.user_id=m.user_id AND actor.status='ACTIVE'
          WHERE m.enabled IS TRUE AND m.enabled_at<=$1 ORDER BY m.created_at,m.id""",
            now,
        )
    output = {"checked": 0, "baselines": 0, "notices": 0, "failed": 0}
    for row in rows:
        monitor = dict(row)
        try:
            async with tenant_transaction(
                pool, monitor["user_id"], monitor["tenant_id"]
            ) as conn:
                result = await get_context(
                    conn,
                    monitor["tenant_id"],
                    monitor["user_id"],
                    direction_id=monitor.get("direction_id"),
                    index_id=monitor.get("index_id"),
                    instrument_id=monitor.get("instrument_id"),
                    fund_code=monitor.get("fund_code"),
                    fund_market=monitor.get("fund_market"),
                    now=now,
                )
            async with system_connection(pool) as conn:
                async with conn.transaction():
                    stored = await repository.record_monitor_result(
                        conn, monitor, result, now
                    )
            output["checked"] += 1
            output["baselines"] += bool(stored.get("baseline"))
            output["notices"] += bool(stored.get("notice"))
        except Exception as exc:  # noqa: BLE001 -- a single actor cannot stop other opted-in monitors
            output["failed"] += 1
            logger.warning(
                "opportunity_monitor_failed monitor_id=%s error_class=%s",
                monitor["id"],
                type(exc).__name__,
            )
    logger.info(
        "opportunity_monitors_tick checked=%s baselines=%s notices=%s failed=%s",
        *output.values(),
    )
    return output


async def _wait(stop_event, seconds):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_opportunity_monitor_service(
    pool, *, now_provider=None, wait=None, stop_event=None
):
    now_provider = now_provider or now_shanghai
    wait = wait or _wait
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        try:
            await sync_opportunity_monitors(pool, now_provider())
        except Exception as exc:  # noqa: BLE001 -- independent research loop remains recoverable
            logger.warning(
                "opportunity_monitors_tick_failed error_class=%s", type(exc).__name__
            )
        try:
            from backend.jobs.study_tracking import sync_study_tracking

            await sync_study_tracking(pool, now_provider())
        except Exception as exc:  # noqa: BLE001 -- study failures do not stop existing monitors
            logger.warning(
                "study_tracking_tick_failed error_class=%s", type(exc).__name__
            )
        await wait(stop_event, 900)
