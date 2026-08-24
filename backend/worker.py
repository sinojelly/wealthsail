"""WealthSail background Worker entrypoint.

Runs two independent loops:
  1. Four-session scheduler (PRE_MARKET / INTRADAY / FUND_1430 / POST_MARKET)
     with lease-based job runs and stale-run recovery;
  2. Outbox consumer (PENDING → SENDING → SENT with exponential backoff).

Connects with the dedicated ``wealthsail_worker`` BYPASSRLS role (service-role
semantics) — tenant isolation inside report generation is still enforced by the
per-tenant RLS handshake.

Usage:  python -m backend.worker
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime

from backend.common.database import create_pool
from backend.config import get_settings
from backend.jobs import runner
from backend.jobs.outbox import OutboxConsumer
from backend.jobs.scheduler import Scheduler
from backend.market import service as market_service

logger = logging.getLogger("wealthsail.worker")


async def _execute_session(pool, settings, session: str, now: datetime) -> None:
    # 1. 行情刷新（各时段抓取范围由 market service 决定）
    try:
        await market_service.refresh_market(pool, settings, session)
    except Exception:  # noqa: BLE001 — 行情失败不阻断报告生成
        logger.exception("session %s 行情刷新失败（继续生成报告）", session)
    # 2. 报告生成（租约抢占 + 每租户 RLS 事务）
    outcomes = await runner.run_session(pool, settings, session, now)
    for tenant_id, outcome in outcomes.items():
        logger.info("session %s tenant %s → %s", session, tenant_id, outcome)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    pool = await create_pool(
        settings.worker_database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    logger.info("worker connected: %s", settings.worker_database_url.split("@")[-1])

    # Recover runs stuck by a previous crash before starting fresh work.
    try:
        await runner.claim_stale_runs(pool, settings)
    except Exception:  # noqa: BLE001
        logger.exception("stale-run recovery failed (continuing)")

    scheduler = Scheduler(
        settings, lambda session, now: _execute_session(pool, settings, session, now)
    )
    consumer = OutboxConsumer(pool, settings)
    scheduler.start()
    consumer.start()

    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover — non-UNIX platforms
            pass

    logger.info("wealthsail worker running (scheduler + outbox consumer)")
    await stop_event.wait()
    logger.info("shutting down worker…")
    await scheduler.stop()
    await consumer.stop()
    await pool.close()
    logger.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
