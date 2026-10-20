"""Consume only webpage OTC fund/NAV refreshes in the macro runtime."""

from __future__ import annotations

import asyncio
import json
import logging

from backend.common.database import system_connection
from backend.jobs.data_tasks import (
    DataTaskLease,
    _heartbeat_loop,
    complete_data_task,
    data_task_owner,
    fail_data_task,
    process_data_task,
)
from backend.jobs.fund_nav_research import refresh_fund_nav_history

logger = logging.getLogger("wealthsail.fund_research")


async def claim_fund_refresh(pool, *, owner, lease_seconds):
    """Atomically claim a due request, including an expired lease generation."""
    async with system_connection(pool) as conn:
        row = await conn.fetchrow(
            """WITH candidate AS (
                SELECT id FROM workflow.job_runs
                WHERE (job_type IN ('FUND_NAV_RANGE_FETCH','FUND_UNIT_NAV_HISTORY_FETCH')
                    OR (job_type = 'INVESTMENT_RESEARCH_REFRESH' AND payload->>'kind' = 'fund'
                        AND EXISTS (SELECT 1 FROM reference.research_fund_catalog c
                            WHERE c.instrument_id::text = payload->>'id'
                            AND c.trade_form = 'FUND' AND c.market = 'FUND_CN')))
                    AND ((status = 'PENDING' AND available_at <= now()
                            AND (next_retry_at IS NULL OR next_retry_at <= now()))
                        OR (status = 'FAILED' AND next_retry_at <= now())
                        OR (status = 'RUNNING' AND lease_until < now()))
                ORDER BY available_at,id LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            UPDATE workflow.job_runs r SET status = 'RUNNING', started_at = now(),
                finished_at = NULL, error_class = NULL, lease_token = gen_random_uuid(),
                lease_owner = $1, lease_until = now() + make_interval(secs => $2),
                heartbeat_at = now(), attempt_count = attempt_count + 1, next_retry_at = NULL
            FROM candidate c WHERE r.id = c.id
            RETURNING r.id,r.tenant_id,r.job_type,r.payload,r.attempt_count,r.lease_token""",
            owner,
            lease_seconds,
        )
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return DataTaskLease(
        run_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        job_type=row["job_type"],
        payload=dict(payload),
        attempt_count=row["attempt_count"],
        lease_token=str(row["lease_token"]),
    )


async def _refresh_request(pool, settings, lease):
    if lease.job_type == "FUND_UNIT_NAV_HISTORY_FETCH":
        from backend.jobs.fund_nav_full_history import process_full_history_task

        await process_full_history_task(pool, lease)
        return
    if lease.job_type == "FUND_NAV_RANGE_FETCH":
        # Preserve the existing full-history accumulated-NAV range cache too.
        await process_data_task(pool, settings, lease)
        code = lease.payload.get("fundCode")
    else:
        async with system_connection(pool) as conn:
            subject = await conn.fetchrow(
                """SELECT fund_code,market,trade_form FROM reference.research_fund_catalog
                WHERE instrument_id::text = $1 AND trade_form = 'FUND' AND market = 'FUND_CN'""",
                lease.payload.get("id"),
            )
        if not subject:
            raise ValueError("web fund refresh requires a catalogued OTC fund")
        code = subject["fund_code"]
    await refresh_fund_nav_history(pool, code)


async def _process_with_heartbeat(pool, settings, lease):
    work = asyncio.create_task(_refresh_request(pool, settings, lease))
    heartbeat = asyncio.create_task(
        _heartbeat_loop(pool, lease, settings.data_task_lease_seconds),
        name=f"fund-request-heartbeat:{lease.run_id}",
    )
    try:
        done, _ = await asyncio.wait(
            {work, heartbeat}, return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat in done:
            await heartbeat
            raise RuntimeError("fund-request heartbeat stopped before completion")
        await work
    finally:
        for task in (work, heartbeat):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, heartbeat, return_exceptions=True)


async def _requeue_cancelled(pool, lease):
    async with system_connection(pool) as conn:
        await conn.execute(
            """UPDATE workflow.job_runs SET status = 'PENDING', finished_at = NULL,
                lease_token = NULL, lease_owner = NULL, lease_until = NULL,
                heartbeat_at = now(), next_retry_at = now() + interval '5 seconds',
                attempt_count = greatest(0,attempt_count - 1)
            WHERE id = $1 AND lease_token = $2 AND status = 'RUNNING'""",
            lease.run_id,
            lease.lease_token,
        )


async def consume_fund_refresh(pool, settings):
    lease = await claim_fund_refresh(
        pool,
        owner=f"fund-research:{data_task_owner()}",
        lease_seconds=settings.data_task_lease_seconds,
    )
    if lease is None:
        return False
    try:
        await _process_with_heartbeat(pool, settings, lease)
        if not await complete_data_task(pool, lease):
            raise RuntimeError("fund-request lease lost before completion")
    except asyncio.CancelledError:
        await _requeue_cancelled(pool, lease)
        raise
    except Exception as exc:  # retry and terminal policy stay shared with full Worker
        await fail_data_task(pool, lease, settings, exc)
    return True


async def run_fund_research_service(pool, settings, *, stop_event):
    async def requested():
        while not stop_event.is_set():
            try:
                for _ in range(20):
                    if stop_event.is_set() or not await consume_fund_refresh(
                        pool, settings
                    ):
                        break
            except (
                Exception
            ) as exc:  # queue failures must not stop independent services
                logger.warning("fund_request_failed error_class=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=settings.data_task_poll_interval_seconds
                )
            except TimeoutError:
                pass

    task = asyncio.create_task(requested(), name="wealthsail-fund-refresh-requests")
    try:
        await stop_event.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
