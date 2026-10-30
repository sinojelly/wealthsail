"""Independent macro-only collector: python -m backend.jobs.macro_worker.

Startup checks complete recent histories. Every day at 06:30 Asia/Shanghai the
full five-year window is fetched; incomplete attempts retry an hour after they
finish. --once always fetches and exits nonzero for PARTIAL/UNAVAILABLE results.
Independent index research checks its exchange calendar. Opt-in public Codex
research starts at 07:00 on weekdays; no report delivery, outbox or trading loop starts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.common.database import create_pool, verify_runtime_login_boundary
from backend.config import Settings, _uses_supabase_transaction_pooler, get_settings
from backend.jobs.investment_macro_refresh import (
    ensure_macro_history,
    refresh_macro_history,
)
from backend.jobs.macro_evidence import run_macro_evidence_service, sync_macro_evidence
from backend.jobs.daily_research import run_daily_research_service
from backend.jobs.alpha_public import sync_alpha_public
from backend.jobs.fund_research import run_fund_research_service

logger = logging.getLogger("wealthsail.macro_worker")
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DAILY_TIME = time(6, 30)
_RETRY_DELAY = timedelta(hours=1)
_SUCCESS = {"SKIPPED", "COMPLETED"}


def validate_macro_settings(settings: Settings) -> Settings:
    """Require Worker session authority without requiring sending credentials."""
    dsn = settings.worker_database_url.strip()
    if not dsn:
        raise ValueError("WORKER_DATABASE_URL is required for the macro Worker")
    if _uses_supabase_transaction_pooler(dsn):
        raise ValueError("Worker cannot use a Supabase transaction-pooling endpoint")
    if settings.worker_database_pool_mode is None:
        raise ValueError("WORKER_DATABASE_POOL_MODE must declare direct or session")
    if settings.worker_database_pool_mode not in {"direct", "session"}:
        raise ValueError("Worker does not support transaction pooling")
    api_authority = {
        "DATABASE_URL": settings.database_url,
        "JWT_SECRET": settings.jwt_secret,
        "REFRESH_TOKEN_HMAC_KEY": settings.refresh_token_hmac_key,
        "PASSWORD_RESET_HMAC_KEY": settings.password_reset_hmac_key,
        "BINDING_HMAC_KEY": settings.binding_hmac_key,
        "WECOM_CALLBACK_TOKEN": settings.wecom_callback_token,
        "WECOM_CALLBACK_AES_KEY": settings.wecom_callback_aes_key,
    }
    for name, value in api_authority.items():
        if value.strip():
            raise ValueError(f"{name} must not be provided to the macro Worker")
    if settings.auth_dev_mode:
        raise ValueError("AUTH_DEV_MODE is no longer supported")
    return settings


def _now_shanghai():
    return datetime.now(_SHANGHAI)


def _local(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("macro service requires a timezone-aware clock")
    return now.astimezone(_SHANGHAI)


def _completed_day(attempt: datetime) -> date:
    if attempt.time() >= _DAILY_TIME:
        return attempt.date()
    return attempt.date() - timedelta(days=1)


def _startup_completed_day(result, attempted):
    if result["status"] == "COMPLETED":
        return _completed_day(attempted)
    previous_day = attempted.date() - timedelta(days=1)
    today_slot = datetime.combine(attempted.date(), _DAILY_TIME, _SHANGHAI)
    try:
        coverage = result["coverage"]
        collected = [
            datetime.fromisoformat(coverage[field]["lastCollectedAt"])
            for field in ("CPI_YOY", "PMI", "PPI_YOY", "M2_YOY")
        ]
        return (
            attempted.date()
            if all(today_slot <= stamp <= attempted for stamp in collected)
            else previous_day
        )
    except (KeyError, TypeError, ValueError):
        return previous_day


def _next_daily(now: datetime, completed_day: date) -> datetime:
    target = max(now.date(), completed_day + timedelta(days=1))
    scheduled = datetime.combine(target, _DAILY_TIME, _SHANGHAI)
    return max(scheduled, now)


async def _wait_or_stop(stop_event, seconds):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def sync_opportunity_market(pool, now, *, force=False):
    from backend.jobs.opportunity_market import sync_opportunity_market as sync

    return await sync(pool, now, force=force)


async def run_opportunity_market_service(pool, *, stop_event):
    from backend.jobs.opportunity_market import run_opportunity_market_service as run

    await run(pool, stop_event=stop_event)


async def run_opportunity_monitor_service(pool, *, stop_event):
    from backend.jobs.opportunity_monitors import run_opportunity_monitor_service as run

    await run(pool, stop_event=stop_event)


async def run_index_research_service(pool, *, stop_event):
    from backend.jobs.index_research import run_index_research_service as run

    await run(pool, get_settings(), stop_event=stop_event)


async def _collect(function, pool, now):
    try:
        result = await function(pool, now)
    except Exception as exc:  # noqa: BLE001 -- failure retries, cancellation propagates
        logger.warning("macro_collection_failed error_class=%s", type(exc).__name__)
        result = {
            "status": "UNAVAILABLE",
            "reason": "COLLECTION_FAILED",
            "observations": 0,
            "coverage": {},
        }
    logger.info(
        "macro_collection status=%s reason=%s observations=%s",
        result["status"],
        result.get("reason", "-"),
        result.get("observations", 0),
    )
    for field, row in result.get("coverage", {}).items():
        logger.info(
            "macro_source field=%s complete=%s fresh=%s refreshed=%s "
            "observations=%s missing=%s latest=%s",
            field,
            row.get("complete", False),
            row.get("fresh", False),
            row.get("refreshed", "-"),
            row.get("observations", 0),
            ",".join(row.get("missingPeriods", [])),
            row.get("lastPeriod"),
        )
    return result


async def run_macro_service(pool, *, now_provider=None, wait=None, stop_event=None):
    """Run sequential collections until stopped; hooks permit clock-only tests.

    Startup consumes today's slot only after a full fetch, or if all sources'
    successful collections already occurred since today's 06:30. A successful
    retry before 06:30 leaves the regular collection due. This process owns one
    collection; it does not claim a cross-process durable job lease.
    """
    now_provider = now_provider or _now_shanghai
    wait = wait or _wait_or_stop
    stop_event = stop_event or asyncio.Event()
    if stop_event.is_set():
        return
    attempted = _local(now_provider())
    result = await _collect(ensure_macro_history, pool, attempted)
    finished = _local(now_provider())
    due = (
        _next_daily(finished, _startup_completed_day(result, attempted))
        if result["status"] in _SUCCESS
        else finished + _RETRY_DELAY
    )
    while not stop_event.is_set():
        current = _local(now_provider())
        delay = (due - current).total_seconds()
        if delay > 0:
            await wait(stop_event, delay)
            continue
        result = await _collect(refresh_macro_history, pool, current)
        finished = _local(now_provider())
        due = (
            _next_daily(finished, _completed_day(current))
            if result["status"] in _SUCCESS
            else finished + _RETRY_DELAY
        )


async def _serve(pool, settings=None):
    settings = settings or get_settings()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            installed.append(sig)
        except (
            NotImplementedError
        ):  # pragma: no cover -- Windows uses asyncio.run cancellation
            pass
    service = asyncio.create_task(
        run_macro_service(pool, stop_event=stop_event), name="wealthsail-macro-only"
    )

    async def _official():
        try:
            await run_macro_evidence_service(pool, stop_event=stop_event)
        except Exception as exc:  # noqa: BLE001 -- evidence cannot stop historical collection
            logger.warning(
                "macro_official_service_failed error_class=%s", type(exc).__name__
            )

    official = asyncio.create_task(_official(), name="wealthsail-macro-official")

    async def independent(run):
        try:
            await run(pool, stop_event=stop_event)
        except Exception as exc:  # noqa: BLE001 -- independent evidence/monitor failures
            logger.warning(
                "opportunity_service_failed error_class=%s", type(exc).__name__
            )

    market = asyncio.create_task(
        independent(run_opportunity_market_service),
        name="wealthsail-opportunity-market",
    )
    monitor = asyncio.create_task(
        independent(run_opportunity_monitor_service),
        name="wealthsail-opportunity-monitor",
    )
    index = asyncio.create_task(
        independent(run_index_research_service), name="wealthsail-index-research"
    )
    fund = asyncio.create_task(
        independent(
            lambda pool, *, stop_event: run_fund_research_service(
                pool, settings, stop_event=stop_event
            )
        ),
        name="wealthsail-fund-research",
    )
    daily = asyncio.create_task(
        independent(
            lambda pool, *, stop_event: run_daily_research_service(
                pool, settings, stop_event=stop_event
            )
        ),
        name="wealthsail-daily-research",
    )
    from backend.jobs.alpha_runtime import run_alpha_runtime

    alpha = asyncio.create_task(
        run_alpha_runtime(pool, settings, stop_event=stop_event, include_daily=False),
        name="wealthsail-alpha-phase2",
    )
    stopping = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait(
            {service, stopping}, return_when=asyncio.FIRST_COMPLETED
        )
        if service in done:
            await service
    finally:
        for task in (
            service,
            official,
            market,
            monitor,
            index,
            fund,
            daily,
            alpha,
            stopping,
        ):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            service,
            official,
            market,
            monitor,
            index,
            fund,
            daily,
            alpha,
            stopping,
            return_exceptions=True,
        )
        for sig in installed:
            loop.remove_signal_handler(sig)


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Macro-only history collector")
    parser.add_argument("--once", action="store_true", help="force backfill then exit")
    args = parser.parse_args(argv)
    settings = validate_macro_settings(get_settings())
    logging.basicConfig(level=settings.log_level)
    pool = await create_pool(
        settings.worker_database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        statement_cache_size=settings.db_statement_cache_size,
        runtime_role="wealthsail_worker",
    )
    try:
        # Even local development must use the provisioned least-privileged login.
        await verify_runtime_login_boundary(pool, process="worker")
        if args.once:
            result = await _collect(refresh_macro_history, pool, _now_shanghai())
            try:
                result["officialEvidence"] = await sync_macro_evidence(
                    pool, _now_shanghai(), force=True
                )
            except Exception as exc:  # noqa: BLE001 -- keep existing history status and exit contract
                result["officialEvidence"] = {
                    "status": "UNAVAILABLE",
                    "errorClass": type(exc).__name__,
                }
            try:
                result["marketEvidence"] = await sync_opportunity_market(
                    pool, _now_shanghai(), force=True
                )
            except Exception as exc:  # noqa: BLE001 -- public market collection owns its status
                result["marketEvidence"] = {
                    "status": "UNAVAILABLE",
                    "errorClass": type(exc).__name__,
                }
            if settings.alpha_public_enabled:
                try:
                    result["alphaPublic"] = await sync_alpha_public(
                        pool, _now_shanghai()
                    )
                except Exception as exc:  # noqa: BLE001 -- public source failure is independent
                    result["alphaPublic"] = {
                        "status": "FAILED",
                        "errorClass": type(exc).__name__,
                    }
            else:
                result["alphaPublic"] = {"status": "DISABLED"}
            print(json.dumps(result, ensure_ascii=False, default=str))
            return 0 if result["status"] == "COMPLETED" else 1
        logger.info(
            "macro-only service running (06:30 Asia/Shanghai; hourly failure retry)"
        )
        await _serve(pool, settings)
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    except Exception as exc:  # noqa: BLE001 -- never log DSN-bearing exception messages
        logger.error("macro_worker_failed error_class=%s", type(exc).__name__)
        raise SystemExit(1) from None
