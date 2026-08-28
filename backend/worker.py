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

from backend.common.database import create_pool, verify_runtime_login_boundary
from backend.config import get_settings, validate_runtime_settings
from backend.jobs import runner
from backend.jobs.data_tasks import DataTaskConsumer
from backend.jobs.outbox import OutboxConsumer
from backend.jobs.scheduler import Scheduler
from backend.market import service as market_service
from backend.jobs.scheduler import is_trading_day
from backend.integrations.wecom import close_http_client
from modules.market_data.service import MarketDataService

logger = logging.getLogger("wealthsail.worker")


async def _execute_session(pool, settings, session: str, now: datetime) -> None:
    # 1. 行情刷新（各时段抓取范围由 market service 决定）
    try:
        await market_service.refresh_market(pool, settings, session)
    except Exception:  # noqa: BLE001 — 行情失败不阻断报告生成
        logger.exception("session %s 行情刷新失败（继续生成报告）", session)
    # 2. 市场情绪是全局 Worker 快照。API 只读库内结果，绝不按请求抓取。
    try:
        await MarketDataService(pool).refresh_market_sentiment()
    except Exception:  # noqa: BLE001 — 情绪不可用不阻断租户报告
        logger.exception("session %s 市场情绪刷新失败（继续生成报告）", session)
    # 3. 报告生成（租约抢占 + 每租户 RLS 事务）
    outcomes = await runner.run_session(pool, settings, session, now)
    for tenant_id, outcome in outcomes.items():
        logger.info("session %s tenant %s → %s", session, tenant_id, outcome)


async def main() -> None:
    settings = validate_runtime_settings(get_settings(), process="worker")
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    pool = await create_pool(
        settings.worker_database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        runtime_role="wealthsail_worker",
    )
    await verify_runtime_login_boundary(
        pool, process="worker", skip=not settings.is_production
    )
    logger.info("worker connected: %s", settings.worker_database_url.split("@")[-1])

    # Recover runs stuck by a previous crash before starting fresh work.
    try:
        await runner.claim_stale_runs(pool, settings)
    except Exception:  # noqa: BLE001
        logger.exception("stale-run recovery failed (continuing)")

    async def _calendar_check(now: datetime) -> bool:
        async with pool.acquire() as conn:
            return await is_trading_day(conn, now)

    scheduler = Scheduler(
        settings,
        lambda session, now: _execute_session(pool, settings, session, now),
        trading_day_check=_calendar_check,
    )
    consumer = OutboxConsumer(pool, settings)
    data_tasks = DataTaskConsumer(pool, settings)
    scheduler.start()
    consumer.start()
    data_tasks.start()

    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover — non-UNIX platforms
            pass

    logger.info("wealthsail worker running (scheduler + outbox + data tasks)")
    await stop_event.wait()
    logger.info("shutting down worker…")
    await scheduler.stop()
    await consumer.stop()
    await data_tasks.stop()
    await close_http_client()
    await pool.close()
    logger.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
