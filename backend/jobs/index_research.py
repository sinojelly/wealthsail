"""Independent CSI research: python -m backend.jobs.index_research --once."""

import argparse
import asyncio
import json
import logging
import httpx
from datetime import timedelta
from uuid import uuid4

from backend.common.database import (
    create_pool,
    system_connection,
    verify_runtime_login_boundary,
)
from backend.config import get_settings
from backend.dependencies import now_shanghai
from backend.jobs.investment_research import refresh_subject
from backend.market.service import ensure_trading_calendar
from backend.market.index_policy import config_hash, object_value, research_status

logger = logging.getLogger("wealthsail.index_research")
PREFIX = "INDEX_RESEARCH:"


def refresh_due(now, previous, configuration_hash=None):
    if previous is None:
        return True
    result = object_value(previous.get("result"))
    if (
        configuration_hash is not None
        and result.get("configurationHash") != configuration_hash
    ):
        return True
    delay = timedelta(hours=24 if result.get("status") == "COMPLETED" else 1)
    return now >= previous["recorded_at"] + delay


async def subjects(conn):
    return [
        dict(row)
        for row in await conn.fetch(
            "SELECT * FROM reference.research_indices WHERE id IN ('hs300','a500','zz500') OR (verified_at IS NOT NULL AND provider_symbol IS NOT NULL) ORDER BY sort_order,id"
        )
    ]


async def claim_index(conn, subject, now, *, force=False):
    key, configuration = PREFIX + subject["id"], config_hash(subject)
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
        active = await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM market.macro_evidence_attempts c
          WHERE job_key=$1 AND phase='CLAIMED' AND recorded_at<=$2 AND lease_until>$2
          AND NOT EXISTS(SELECT 1 FROM market.macro_evidence_attempts t WHERE t.run_id=c.run_id AND t.phase IN ('FINISHED','CANCELLED')))""",
            key,
            now,
        )
        if active:
            return None
        previous = await conn.fetchrow(
            "SELECT * FROM market.macro_evidence_attempts WHERE job_key=$1 AND phase IN ('FINISHED','CANCELLED') AND recorded_at<=$2 ORDER BY recorded_at DESC,id DESC LIMIT 1",
            key,
            now,
        )
        if not force and not refresh_due(now, previous, configuration):
            return None
        identity = uuid4()
        await conn.execute(
            """INSERT INTO market.macro_evidence_attempts(run_id,job_key,phase,started_at,recorded_at,lease_until,trigger)
          VALUES($1,$2,'CLAIMED',$3,$3,$4,$5)""",
            identity,
            key,
            now,
            now + timedelta(minutes=10),
            "FORCE" if force else "CHECK",
        )
        return {
            "id": identity,
            "key": key,
            "startedAt": now,
            "configurationHash": configuration,
        }


async def finish_index(conn, claim, now, result, *, cancelled=False):
    # A late lease owner cannot overwrite a newer generation's terminal state.
    await conn.execute(
        """INSERT INTO market.macro_evidence_attempts(run_id,job_key,phase,started_at,recorded_at,trigger,result)
      SELECT $1,$2,$3,$4,$5,'INDEX',$6::jsonb
      WHERE EXISTS(SELECT 1 FROM market.macro_evidence_attempts c WHERE c.run_id=$1 AND c.phase='CLAIMED' AND c.lease_until>=$5)
      ON CONFLICT DO NOTHING""",
        claim["id"],
        claim["key"],
        "CANCELLED" if cancelled else "FINISHED",
        claim["startedAt"],
        now,
        json.dumps(
            {**result, "configurationHash": claim["configurationHash"]}, default=str
        ),
    )


async def sync_index_research(pool, settings, now, *, force=False, index_id=None):
    async with system_connection(pool) as conn:
        rows = await subjects(conn)
    indices = {}
    calendar_checked = False
    for subject in rows:
        if index_id and subject["id"] != index_id:
            continue
        async with system_connection(pool) as conn:
            claim = await claim_index(conn, subject, now, force=force)
        if claim is None:
            indices[subject["id"]] = {"status": "SKIPPED"}
            continue
        try:
            if not calendar_checked:
                calendar_checked = True
                try:
                    await asyncio.wait_for(ensure_trading_calendar(pool), timeout=60)
                except Exception as exc:  # noqa: BLE001 -- existing complete calendars remain usable
                    logger.warning(
                        "index_calendar_refresh_failed error_class=%s",
                        type(exc).__name__,
                    )
            result = await asyncio.wait_for(
                refresh_subject(
                    pool,
                    settings,
                    "index",
                    subject["id"],
                    now.date(),
                    now=now,
                    _claimed=True,
                ),
                timeout=120,
            )
            result = {
                "status": research_status(result),
                "asOf": (result or {}).get("asOf"),
                "dataQuality": (result or {}).get("dataQuality"),
                "trendQuality": (result or {}).get("trendQuality"),
            }
        except asyncio.CancelledError:
            async with system_connection(pool) as conn:
                await finish_index(
                    conn, claim, now_shanghai(), {"status": "CANCELLED"}, cancelled=True
                )
            raise
        except Exception as exc:  # noqa: BLE001 -- independent index failure
            result = {"status": "FAILED", "errorClass": type(exc).__name__}
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and type(exc.response.status_code) is int
                and 100 <= exc.response.status_code <= 599
            ):
                result["httpStatus"] = exc.response.status_code
            logger.warning(
                "index_research_failed index=%s error_class=%s",
                subject["id"],
                type(exc).__name__,
            )
        async with system_connection(pool) as conn:
            await finish_index(conn, claim, now_shanghai(), result)
        indices[subject["id"]] = result
    failed = any(row["status"] in {"FAILED", "PARTIAL"} for row in indices.values())
    status = "PARTIAL" if failed else "COMPLETED"
    if not indices:
        status = "UNAVAILABLE"
    elif all(row["status"] == "SKIPPED" for row in indices.values()):
        status = "SKIPPED"
    return {"status": status, "indices": indices}


async def consume_index_refresh(pool, settings):
    """Claim only web index requests; never fund/report/outbox tasks."""
    from backend.jobs.data_tasks import (
        DataTaskLease,
        complete_data_task,
        fail_data_task,
    )

    async with system_connection(pool) as conn:
        row = await conn.fetchrow("""WITH candidate AS (
          SELECT id FROM workflow.job_runs WHERE job_type='INVESTMENT_RESEARCH_REFRESH' AND payload->>'kind'='index'
          AND ((status='PENDING' AND available_at<=now() AND (next_retry_at IS NULL OR next_retry_at<=now())) OR (status='FAILED' AND next_retry_at<=now())
            OR (status='RUNNING' AND lease_until<now())) ORDER BY available_at,id LIMIT 1 FOR UPDATE SKIP LOCKED)
          UPDATE workflow.job_runs r SET status='RUNNING',started_at=now(),finished_at=NULL,error_class=NULL,
          lease_token=gen_random_uuid(),lease_owner='index-research',lease_until=now()+interval '10 minutes',
          heartbeat_at=now(),attempt_count=attempt_count+1,next_retry_at=NULL
          FROM candidate c WHERE r.id=c.id RETURNING r.*""")
    if row is None:
        return False
    payload = object_value(row["payload"])
    lease = DataTaskLease(
        run_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        job_type=row["job_type"],
        payload=payload,
        attempt_count=row["attempt_count"],
        lease_token=str(row["lease_token"]),
    )

    async def requeue():
        async with system_connection(pool) as conn:
            await conn.execute(
                """UPDATE workflow.job_runs SET status='PENDING',
              finished_at=NULL,lease_token=NULL,lease_owner=NULL,lease_until=NULL,
              next_retry_at=now()+interval '5 seconds',attempt_count=greatest(0,attempt_count-1)
              WHERE id=$1 AND lease_token=$2 AND status='RUNNING'""",
                lease.run_id,
                lease.lease_token,
            )

    try:
        result = await sync_index_research(
            pool, settings, now_shanghai(), force=True, index_id=payload.get("id")
        )
    except asyncio.CancelledError:
        await requeue()
        raise
    outcome = result["indices"].get(payload.get("id"), {})
    if outcome.get("status") in {"COMPLETED", "PARTIAL"}:
        await complete_data_task(pool, lease)
    elif outcome.get("status") == "SKIPPED":
        await requeue()
    else:
        await fail_data_task(
            pool,
            lease,
            settings,
            RuntimeError("index collection unavailable or already leased"),
        )
    return True


async def _wait(stop_event, seconds):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_index_research_service(pool, settings, *, stop_event, wait=None):
    async def periodic():
        while not stop_event.is_set():
            try:
                await sync_index_research(pool, settings, now_shanghai())
            except Exception as exc:  # noqa: BLE001 -- periodic failures remain independent
                logger.warning(
                    "index_research_service_failed error_class=%s", type(exc).__name__
                )
            await (wait or _wait)(stop_event, 900)

    async def requested():
        while not stop_event.is_set():
            try:
                for _ in range(20):
                    if stop_event.is_set() or not await consume_index_refresh(
                        pool, settings
                    ):
                        break
            except Exception as exc:  # noqa: BLE001 -- queue failures retry without stopping periodic work
                logger.warning(
                    "index_request_failed error_class=%s", type(exc).__name__
                )
            await (wait or _wait)(stop_event, 5)

    tasks = [asyncio.create_task(periodic()), asyncio.create_task(requested())]
    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main(argv=None):
    from backend.jobs.macro_worker import validate_macro_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", required=True)
    args = parser.parse_args(argv)
    settings = validate_macro_settings(get_settings())
    pool = await create_pool(
        settings.worker_database_url, runtime_role="wealthsail_worker"
    )
    try:
        await verify_runtime_login_boundary(pool, process="worker")
        result = await sync_index_research(
            pool, settings, now_shanghai(), force=args.once
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "COMPLETED" else 1
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
