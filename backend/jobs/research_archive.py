"""Drain frozen research archives without inference, validation, or collection."""

import asyncio
from contextlib import suppress
import logging

from backend.common.database import system_connection
from backend.dependencies import now_shanghai
from backend.investment.opportunity_fund_cache import precompute_topic_funds
from backend.macro import research_acceptance_repository as repository
from backend.macro.research_diagnostics import safe_error_class

logger = logging.getLogger("wealthsail.research_archive")
BATCH_LIMIT = 4
POLL_SECONDS = 60
AUDIT_STATUSES = {"AUDIT_PENDING", "ARCHIVING", "ARCHIVED", "UNRECOVERABLE"}


def archive_status(value):
    status = value.get("status") if isinstance(value, dict) else None
    return status if status in AUDIT_STATUSES else "AUDIT_PENDING"


async def archive_research_run(pool, run_id=None, *, now=None):
    async with system_connection(pool) as conn:
        async with conn.transaction():
            claim = await repository.claim_research_archive(conn, run_id=run_id)
    if claim is None:
        return {"auditStatus": "AUDIT_PENDING", "claimed": False}
    try:
        async with system_connection(pool) as conn:
            async with conn.transaction():
                # The repository copies the original bounded descriptors and
                # INITIAL outcome, then completes this exact lease atomically.
                await repository.persist_research_archive(conn, claim)
    except Exception as exc:  # noqa: BLE001 -- retry frozen data, never inference
        error_class = safe_error_class(type(exc).__name__)
        async with system_connection(pool) as conn:
            async with conn.transaction():
                pending = await repository.finish_research_archive(
                    conn,
                    claim,
                    status="AUDIT_PENDING",
                    error_class=error_class,
                )
        status = archive_status(pending)
        logger.warning(
            "research_archive_failed run_id=%s audit_status=%s error_class=%s",
            claim["run_id"],
            status,
            error_class,
        )
        return {"auditStatus": status, "claimed": True}
    logger.info(
        "research_archive_completed run_id=%s audit_status=ARCHIVED", claim["run_id"]
    )
    # This optional projection has its own durable retry state. A failure must
    # never roll back the accepted economic archive or consume a model slot.
    await _cache_candidates(pool, claim["run_id"])
    return {"auditStatus": "ARCHIVED", "claimed": True}


async def _cache_candidates(pool, run_id=None):
    try:
        return await precompute_topic_funds(pool, run_id)
    except Exception as exc:  # noqa: BLE001 -- independent background backfill
        logger.warning(
            "opportunity_cache_pending error_class=%s",
            safe_error_class(type(exc).__name__),
        )
        return {"claimed": False, "status": "PENDING"}


async def sync_research_archives(pool, *, now=None, limit=BATCH_LIMIT):
    if type(limit) is not int or not 1 <= limit <= BATCH_LIMIT:
        raise ValueError("invalid archive recovery batch limit")
    results = []
    for _ in range(limit):
        result = await archive_research_run(pool, now=now)
        if not result["claimed"]:
            break
        results.append(result)
    # Also discover old snapshots when no economic archive is pending. This is
    # the only backfill path; an API miss never performs the expensive search.
    for _ in range(limit):
        cached = await _cache_candidates(pool)
        if not cached["claimed"]:
            break
    return results


async def _wait(stop_event, seconds):
    with suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), seconds)


async def run_research_archive_service(
    pool, *, stop_event=None, now_provider=now_shanghai, wait=_wait
):
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        try:
            await sync_research_archives(pool, now=now_provider())
        except Exception as exc:  # noqa: BLE001 -- the durable lease remains recoverable
            logger.warning(
                "research_archive_service_failed audit_status=AUDIT_PENDING error_class=%s",
                safe_error_class(type(exc).__name__),
            )
        await wait(stop_event, POLL_SECONDS)
