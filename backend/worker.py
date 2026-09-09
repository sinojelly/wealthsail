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
from backend.jobs.corporate_actions_sync import sync_corporate_actions
from backend.jobs.fund_nav_sync import sync_fund_nav_history
from backend.jobs.outbox import OutboxConsumer
from backend.jobs.scheduler import Scheduler
from backend.market import service as market_service
from backend.jobs.scheduler import is_trading_day
from backend.integrations.wecom import close_http_client

logger = logging.getLogger("wealthsail.worker")


async def _execute_session(pool, settings, session: str, now: datetime) -> None:
    if session == "CORPORATE_ACTIONS_REFRESH":
        # This is a global source refresh, not a tenant report run.  It never
        # creates workflow.jobs and must not cause normal report generation.
        try:
            stats = await sync_corporate_actions(pool, now)
            logger.info(
                "corporate_actions_refresh scanned=%d persisted=%d unavailable=%d failed=%d",
                stats.scanned_instruments,
                stats.persisted_actions,
                stats.unavailable_instruments,
                stats.failed_instruments,
            )
        except Exception:  # noqa: BLE001 -- retry on next trading day, keep Worker alive
            logger.exception("corporate action refresh failed")
        return

    if session == "FUND_NAV_HISTORY_REFRESH":
        # Historical official NAV is a global source fact.  It intentionally
        # bypasses workflow.jobs and cannot produce a tenant report by itself.
        try:
            stats = await sync_fund_nav_history(pool, settings, now)
            logger.info(
                "fund_nav_history_refresh scanned=%d fetched=%d persisted=%d skipped=%d empty=%d failed=%d",
                stats.scanned_instruments,
                stats.fetched_observations,
                stats.persisted_observations,
                stats.skipped_observations,
                stats.empty_instruments,
                stats.failed_instruments,
            )
        except Exception:  # noqa: BLE001 -- retry next trading day, keep Worker alive
            logger.exception("fund NAV history refresh failed")
        return

    # 1. 行情刷新（各时段抓取范围由 market service 决定）
    try:
        await market_service.refresh_market(pool, settings, session)
    except Exception:  # noqa: BLE001 — 行情失败不阻断报告生成
        logger.exception("session %s 行情刷新失败（继续生成报告）", session)
    # 2. 热点排行和市场情绪都是全局 Worker 快照；API 只读数据库。
    try:
        await market_service.refresh_market_hot(pool)
    except Exception:  # noqa: BLE001 — 热点不可用不阻断租户报告
        logger.exception("session %s 市场热点刷新失败（继续生成报告）", session)
    try:
        await market_service.refresh_market_sentiment(pool)
    except Exception:  # noqa: BLE001 — 情绪不可用不阻断租户报告
        logger.exception("session %s 市场情绪刷新失败（继续生成报告）", session)
    # 3. 报告生成（租约抢占 + 每租户 RLS 事务）
    outcomes = await runner.run_session(pool, settings, session, now)
    for tenant_id, outcome in outcomes.items():
        logger.info("session %s tenant %s → %s", session, tenant_id, outcome)


async def _recovery_loop(pool, settings) -> None:
    """Continuously consume recent persisted report runs needing recovery."""
    interval = max(5.0, settings.scheduler_tick_seconds)
    while True:
        try:
            outcomes = await runner.run_recoverable_jobs(pool, settings)
            for key, outcome in outcomes.items():
                logger.info("recovered %s → %s", key, outcome)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- keep the independent loop alive
            logger.exception("job recovery scan failed")
        await asyncio.sleep(interval)


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
        statement_cache_size=settings.db_statement_cache_size,
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

    # The scheduler falls back to weekdays when the exchange calendar is
    # empty, but portfolio freshness also needs holiday-aware trading dates.
    # Bootstrap once on a cold deployment; the portfolio read path retains a
    # persisted-official-NAV fallback if the provider is temporarily down.
    try:
        calendar = await market_service.ensure_trading_calendar(pool)
        logger.info(
            "trading calendar bootstrap status=%s rows=%s reason=%s",
            calendar["status"],
            calendar.get("rows", 0),
            calendar.get("reason", "-"),
        )
    except Exception:  # noqa: BLE001 -- independent loops must still start
        logger.exception("trading calendar bootstrap failed (continuing)")

    # A fresh market-hot migration can be deployed outside trading hours.
    # Seed only an empty read model here; normal refreshes remain tied to the
    # four governed trading sessions and API requests never call Providers.
    try:
        bootstrap = await market_service.ensure_market_hot_snapshot(pool)
        logger.info(
            "market hotspot bootstrap status=%s quality=%s reason=%s",
            bootstrap["status"],
            bootstrap.get("qualityStatus", "-"),
            bootstrap.get("reason", "-"),
        )
    except Exception:  # noqa: BLE001 -- independent loops must still start
        logger.exception("market hotspot bootstrap failed (continuing)")

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
    recovery_task = asyncio.create_task(
        _recovery_loop(pool, settings), name="wealthsail-job-recovery"
    )
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
    recovery_task.cancel()
    try:
        await recovery_task
    except asyncio.CancelledError:
        pass
    await close_http_client()
    await pool.close()
    logger.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
