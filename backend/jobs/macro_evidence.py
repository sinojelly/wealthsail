"""Independent official evidence collection with persistent short claims."""

import asyncio
import logging
from datetime import timedelta

from backend.common.database import system_connection
from backend.dependencies import now_shanghai
from backend.macro import evidence_repository as repository

logger = logging.getLogger("wealthsail.macro_evidence")


async def fetch_macro_evidence(now):
    from backend.integrations.macro_official import fetch_macro_evidence as fetch

    return await fetch(now)


async def sync_macro_evidence(pool, now, *, force=False, now_provider=None):
    now_provider = now_provider or now_shanghai
    now = repository._timestamp(now)
    async with system_connection(pool) as conn:
        claim = await repository.claim_refresh(conn, now, force=force)
    if claim is None:
        return {"status": "SKIPPED", "reason": "RECENT_ATTEMPT_OR_ACTIVE_CLAIM"}
    result = {"status": "UNAVAILABLE", "releases": 0, "schedules": 0, "events": 0}
    try:
        try:
            data = await fetch_macro_evidence(now)
            finished = max(now, repository._timestamp(now_provider()))
            outcomes = data.get("sourceResults", {})
            statuses = [row.get("status") for row in outcomes.values()]
            result.update(sourceResults=outcomes)
            async with system_connection(pool) as conn:
                result.update(await repository.append_evidence(conn, data, finished))
            result["status"] = (
                "COMPLETED"
                if statuses and all(status == "SUCCESS" for status in statuses)
                else "PARTIAL"
                if "SUCCESS" in statuses
                else "UNAVAILABLE"
            )
            if result.get("rejected") and result["status"] == "COMPLETED":
                result["status"] = "PARTIAL"
            if claim.get("release_targets"):
                async with system_connection(pool) as conn:
                    evidence = await repository.read_evidence(conn, finished)
                actual = {
                    (row["code"], str(row["reference_period"])[:7])
                    for row in evidence["releases"]
                }
                if any(
                    (row["code"], str(row["reference_period"])[:7]) not in actual
                    for row in claim["release_targets"]
                ):
                    if result["status"] == "COMPLETED":
                        result["status"] = "PARTIAL"
                    result["reason"] = "AWAITING_SCHEDULED_RELEASE"
        except Exception as exc:  # noqa: BLE001 -- official failure never changes historic facts
            result.update(status="UNAVAILABLE", errorClass=type(exc).__name__)
        finished = max(now, repository._timestamp(now_provider()))
        if result["status"] != "COMPLETED":
            if claim.get("retry_exhausted"):
                result["retryExhausted"] = True
            else:
                result["retryUntil"] = claim.get("retry_until") or finished + timedelta(
                    hours=24
                )
        async with system_connection(pool) as conn:
            await repository.finish_refresh(conn, claim, finished, result)
        logger.info(
            "macro_official_collection status=%s releases=%s schedules=%s events=%s",
            result["status"],
            result["releases"],
            result["schedules"],
            result["events"],
        )
        return result
    except asyncio.CancelledError:
        finished = max(now, repository._timestamp(now_provider()))
        result.update(
            status="UNAVAILABLE",
            errorClass="CancelledError",
            retryUntil=finished + timedelta(hours=24),
        )
        async with system_connection(pool) as conn:
            await repository.finish_refresh(
                conn, claim, finished, result, cancelled=True
            )
        raise


async def _wait(stop_event, seconds):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_macro_evidence_service(
    pool, *, now_provider=None, wait=None, stop_event=None
):
    now_provider = now_provider or now_shanghai
    wait = wait or _wait
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        try:
            await sync_macro_evidence(pool, now_provider())
        except Exception as exc:  # noqa: BLE001 -- keep independent sources alive
            logger.warning(
                "macro_official_tick_failed error_class=%s", type(exc).__name__
            )
        await wait(stop_event, 900)
