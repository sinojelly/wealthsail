"""Worker-only public NAV warming after durable topic-candidate discovery.

The tenant job queue requires an authorized human requester. Public research
instead reuses the existing append-only market evidence lease boundary. Its
per-code leases contain no research run, topic, user or tenant associations.
"""

import asyncio
import json
import logging
from time import perf_counter
from uuid import UUID, uuid4

from backend.ai_usage.tracker import SNAPSHOT_TOTAL_BUDGET_SECONDS
from backend.common.database import system_connection
from backend.funds.nav_history import local, valid_points
from backend.funds.nav_window import NAV_RESEARCH_MIN_SAMPLES, NAV_SYNC_TARGET_SAMPLES
from backend.investment.live_fund_plans import (
    _code,
    _safe_history,
    refresh_report_fund_plans,
)
from backend.investment.discovery_limits import OPPORTUNITY_TOPIC_LIMIT
from backend.investment.opportunity_fund_cache import prepare_cached_candidates
from backend.investment.opportunity_fund_prices import price_position
from backend.investment.opportunity_fund_recall import (
    RECALL_MATCH_VERSION,
    _IDENTITY_SQL,
    _identities,
    enrich_topic_funds,
)
from backend.investment.theme_graph import collect_theme_nav_histories
from backend.macro.research_diagnostics import safe_error_class
from backend.integrations.research_lifecycle import research_deadlines

MAX_RECALLED_NAV_FUNDS = 24
MAX_READY_NAV_RUNS = 4
_LEASE_SECONDS = 360  # Collector has a 240 second global deadline.
# Scheduling priority only: expiry never proves model exit or releases a guard.
_MODEL_PRIORITY_WINDOW_SECONDS = (
    research_deadlines(1200)["workerSeconds"] + SNAPSHOT_TOTAL_BUDGET_SECONDS
)
_TRIGGER = "OPPORTUNITY_NAV"
logger = logging.getLogger("wealthsail.opportunity_fund_nav")


def _plan(product):
    return {
        **product,
        "priceBasis": "UNIT_NAV",
        "unit": "CNY",
        "currency": "CNY",
        "label": product.get("name"),
    }


def select_recalled_nav_targets(recalls, *, limit=MAX_RECALLED_NAV_FUNDS):
    """Round-robin the displayed three independent groups, globally capped."""
    topics = []
    for item in recalls.values():
        rows, groups = [], set()
        for product in item.get("funds") or item.get("indirectFunds", []):
            group = product.get("fundGroupKey") or product.get("fundCode")
            if _code(_plan(product)) and group not in groups:
                rows.append(product)
                groups.add(group)
            if len(rows) == 3:
                break
        topics.append(rows)
    selected = {}
    for rank in range(3):
        for rows in topics:
            if rank < len(rows):
                product = rows[rank]
                selected.setdefault(product["fundCode"], product)
                if len(selected) == limit:
                    return list(selected.values())
    return list(selected.values())


async def priced_recalled_nav_codes(conn, products, now, *, histories=None):
    """Use exactly the consumer's source, continuity and freshness decisions."""
    if not products:
        return set()
    result = await refresh_report_fund_plans(
        conn,
        {"discoveryActionableZones": [_plan(row) for row in products]},
        now,
        histories=histories,
    )
    return {
        plan["fundCode"]
        for plan in result["discoveryActionableZones"]
        if plan.get("historySampleCount", 0) >= NAV_RESEARCH_MIN_SAMPLES
        and price_position(plan, now)["status"] != "DATA_PENDING"
    }


def _object(value):
    value = json.loads(value) if isinstance(value, str) else value
    return value if isinstance(value, dict) else {}


async def _load_targets(conn, run_id, now):
    row = await conn.fetchrow(
        """SELECT c.candidates,r.public_packet FROM research.opportunity_fund_cache c
        JOIN research.research_topic_snapshots s ON s.run_id=c.run_id AND s.id=c.snapshot_id
        JOIN research.daily_research_runs r ON r.run_id=c.run_id
        JOIN reference.opportunity_fund_catalog_revision v ON v.singleton
        WHERE c.run_id=$1 AND c.status='READY' AND c.validated_at=s.validated_at
          AND c.content_digest=s.content_digest AND c.match_version=$2
          AND c.catalog_revision=v.revision AND c.catalog_day=CURRENT_DATE
          AND c.updated_at<=$3""",
        UUID(str(run_id)),
        RECALL_MATCH_VERSION,
        now,
    )
    if row is None:
        return []
    candidates = _object(row["candidates"])
    candidates = prepare_cached_candidates(candidates, list(candidates))
    # Reuse current master identity and current share-class availability checks;
    # cached names/UUIDs never authorize collection by themselves.
    enriched = await enrich_topic_funds(
        conn, candidates, _object(row["public_packet"]), now
    )
    return select_recalled_nav_targets(enriched, limit=OPPORTUNITY_TOPIC_LIMIT * 3)


async def _claim(conn, code):
    """Serialize claims by public fund code, using only database lease clocks."""
    key = f"{_TRIGGER}:{code}"
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
    blocked = await conn.fetchval(
        """SELECT EXISTS(SELECT 1 FROM market.macro_evidence_attempts c
        WHERE c.job_key=$1 AND c.phase='CLAIMED' AND c.lease_until>clock_timestamp()
          AND NOT EXISTS(SELECT 1 FROM market.macro_evidence_attempts f
            WHERE f.run_id=c.run_id AND f.phase IN ('FINISHED','CANCELLED')))
        OR EXISTS(SELECT 1 FROM market.macro_evidence_attempts f
          WHERE f.job_key=$1 AND f.phase IN ('FINISHED','CANCELLED')
            AND f.recorded_at>clock_timestamp()-CASE WHEN f.result->>'status'='SUCCESS'
              THEN interval '12 hours' ELSE interval '1 hour' END)""",
        key,
    )
    if blocked:
        return None
    identity = uuid4()
    await conn.execute(
        """INSERT INTO market.macro_evidence_attempts
        (run_id,job_key,phase,started_at,recorded_at,lease_until,trigger,release_keys)
        VALUES($1,$2,'CLAIMED',clock_timestamp(),clock_timestamp(),
          clock_timestamp()+$3*interval '1 second',$4,'[]'::jsonb)""",
        identity,
        key,
        _LEASE_SECONDS,
        _TRIGGER,
    )
    return {"id": identity, "key": key, "code": code}


async def _finish(pool, claim, product, history=None, *, cancelled=False):
    """Fence expired workers and recheck current identity before public writes."""
    async with system_connection(pool) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", claim["key"]
            )
            owned = await conn.fetchrow(
                """SELECT c.started_at FROM market.macro_evidence_attempts c
                WHERE c.run_id=$1 AND c.phase='CLAIMED' AND c.lease_until>clock_timestamp()
                  AND NOT EXISTS(SELECT 1 FROM market.macro_evidence_attempts f
                    WHERE f.run_id=c.run_id AND f.phase IN ('FINISHED','CANCELLED'))""",
                claim["id"],
            )
            if not owned:
                return False
            now = local(await conn.fetchval("SELECT clock_timestamp()"))
            current = _identities(
                await conn.fetch(_IDENTITY_SQL, now, [claim["code"]]), now
            )
            identity = current.get(claim["code"])
            safe = (
                not cancelled
                and isinstance(history, dict)
                and identity is not None
                and identity["identity"]
                == (product["subjectKind"], product["subjectId"])
                and identity["name"] == product["name"]
                and _safe_history(history)
                and 0
                < len(valid_points(history, claim["code"], now))
                <= NAV_SYNC_TARGET_SAMPLES
            )
            if safe:
                await conn.execute(
                    """INSERT INTO market.fund_research_nav_histories
                    (fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$3,$4::jsonb)""",
                    claim["code"],
                    local_from_history(history, "retrievedAt"),
                    local_from_history(history, "availableAt"),
                    json.dumps(history),
                )
            usable = safe and claim["code"] in await priced_recalled_nav_codes(
                conn,
                [product],
                now,
                histories=[history],
            )
            await conn.execute(
                """INSERT INTO market.macro_evidence_attempts
                (run_id,job_key,phase,started_at,recorded_at,trigger,release_keys,result)
                VALUES($1,$2,$3,$4,clock_timestamp(),$5,'[]'::jsonb,$6::jsonb)
                ON CONFLICT DO NOTHING""",
                claim["id"],
                claim["key"],
                "CANCELLED" if cancelled else "FINISHED",
                owned["started_at"],
                _TRIGGER,
                json.dumps(
                    {
                        "status": "SUCCESS"
                        if usable
                        else "PARTIAL"
                        if safe
                        else "FAILED",
                        "records": len(history["points"]) if safe else 0,
                        "usable": bool(usable),
                    }
                ),
            )
            return {"persisted": safe, "usable": bool(usable)}


def local_from_history(history, field):
    from datetime import datetime

    return local(datetime.fromisoformat(history[field]))


async def schedule_recalled_fund_nav(pool, run_id, *, now=None):
    """Warm READY recall Top funds in the independent service, never in a GET.

    Up to 24 globally deduplicated codes use the existing four-concurrent,
    240-second public research collector, requesting 550 disclosed samples.
    Live readers remain read-only and see new histories on their next request.
    """
    return await _schedule_recalled_fund_nav_runs(pool, [run_id], now=now)


async def _schedule_recalled_fund_nav_runs(pool, run_ids, *, now=None):
    if len(run_ids) > MAX_READY_NAV_RUNS:
        raise ValueError("recalled NAV warming exceeds its READY run budget")
    claims, products, targets, ready = {}, {}, {}, set()
    summary = {
        "selected": 0,
        "reused": 0,
        "claimed": 0,
        "persisted": 0,
        "usable": 0,
        "partial": 0,
        "failed": 0,
    }
    histories, cancelled = {}, False
    try:
        async with system_connection(pool) as conn:
            if await conn.fetchval("SELECT current_user") != "wealthsail_worker":
                raise PermissionError(
                    "recalled NAV warming requires the worker service role"
                )
            now = local(now or await conn.fetchval("SELECT clock_timestamp()"))
            for run_id in run_ids:
                for product in await _load_targets(conn, run_id, now):
                    targets.setdefault(product["fundCode"], product)
            ordered = list(targets.values())
            for offset in range(0, len(ordered), MAX_RECALLED_NAV_FUNDS):
                batch = ordered[offset : offset + MAX_RECALLED_NAV_FUNDS]
                ready.update(await priced_recalled_nav_codes(conn, batch, now))
                for product in batch:
                    code = product["fundCode"]
                    if code in ready:
                        continue
                    async with conn.transaction():
                        claim = await _claim(conn, code)
                        if claim:
                            # Remember before COMMIT awaits: cancellation during
                            # an uncertain commit must still attempt cleanup.
                            claims[code], products[code] = claim, product
                    if len(claims) == MAX_RECALLED_NAV_FUNDS:
                        break
                if len(claims) == MAX_RECALLED_NAV_FUNDS:
                    break
        summary.update(selected=len(targets), reused=len(ready), claimed=len(claims))
        if claims:
            collected = await collect_theme_nav_histories(
                pool,
                now,
                asset_rows=[
                    {
                        "asset_code": code,
                        "overseas_calendar_required": products[code].get(
                            "overseasCalendarRequired"
                        )
                        is True,
                    }
                    for code in claims
                ],
                minimum_points=NAV_SYNC_TARGET_SAMPLES,
            )
            histories = {
                row["fundCode"]: row
                for row in collected["fundHistories"]
                if isinstance(row, dict) and row.get("fundCode") in claims
            }
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:

        async def finish_all():
            for code, claim in claims.items():
                result = await _finish(
                    pool,
                    claim,
                    products[code],
                    histories.get(code),
                    cancelled=cancelled,
                )
                if result and result["persisted"]:
                    summary["persisted"] += 1
                    summary["usable" if result["usable"] else "partial"] += 1
                else:
                    summary["failed"] += 1

        if claims:
            cleanup = asyncio.create_task(finish_all())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
    return summary


async def _latest_ready_run(pool):
    runs = await _latest_ready_runs(pool)
    return runs[0] if runs else None


async def _research_model_running(pool):
    """Yield optional warming to an unfinished research invocation.

    A historical UNCERTAIN result or an old result-less orphan may retain its
    guard indefinitely. Only fresh unfinished invocations receive scheduling
    priority; the finite observation window does not prove exit, change a
    result, release occupation, or preempt an already started collection round.
    """
    async with system_connection(pool) as conn:
        return await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM research.daily_research_model_guards g
            LEFT JOIN research.daily_research_results r ON r.run_id=g.run_id
            WHERE g.released_at IS NULL AND r.run_id IS NULL
              AND g.acquired_at>=clock_timestamp()-$1*interval '1 second')""",
            _MODEL_PRIORITY_WINDOW_SECONDS,
        )


async def _latest_ready_runs(pool):
    async with system_connection(pool) as conn:
        if await conn.fetchval("SELECT current_user") != "wealthsail_worker":
            raise PermissionError(
                "recalled NAV warming requires the worker service role"
            )
        rows = await conn.fetch(
            """SELECT c.run_id FROM research.opportunity_fund_cache c
            JOIN research.research_topic_snapshots s ON s.run_id=c.run_id AND s.id=c.snapshot_id
            JOIN reference.opportunity_fund_catalog_revision v ON v.singleton
            WHERE c.status='READY' AND c.validated_at=s.validated_at
              AND c.content_digest=s.content_digest AND c.match_version=$1
              AND c.catalog_revision=v.revision AND c.catalog_day=CURRENT_DATE
            ORDER BY c.validated_at DESC,s.created_at DESC,c.run_id LIMIT $2""",
            RECALL_MATCH_VERSION,
            MAX_READY_NAV_RUNS,
        )
        return [row["run_id"] for row in rows]


async def run_opportunity_fund_nav_service(
    pool, *, stop_event, poll_interval_seconds=60
):
    """Discover durable READY targets independently of the archive loop.

    A restart needs no in-memory wakeup or impersonated tenant request. Four
    recent READY runs share one 24-provider-code budget; reused or cooling codes
    do not prevent later targets from progressing. All claims are durable.
    """

    async def collect():
        while not stop_event.is_set():
            started = perf_counter()
            try:
                busy = await _research_model_running(pool)
                if busy:
                    logger.info("opportunity_nav_deferred reason=RESEARCH_RUNNING")
                run_ids = [] if busy else await _latest_ready_runs(pool)
                if run_ids and not stop_event.is_set():
                    result = await _schedule_recalled_fund_nav_runs(pool, run_ids)
                    if result["claimed"]:
                        logger.info(
                            "opportunity_nav_complete selected=%d reused=%d claimed=%d persisted=%d usable=%d partial=%d failed=%d duration_ms=%.1f",
                            result["selected"],
                            result["reused"],
                            result["claimed"],
                            result["persisted"],
                            result["usable"],
                            result["partial"],
                            result["failed"],
                            (perf_counter() - started) * 1000,
                        )
            except Exception as exc:  # noqa: BLE001 -- public source failures must not stop research
                logger.warning(
                    "opportunity_nav_failed error_class=%s duration_ms=%.1f",
                    safe_error_class(type(exc).__name__),
                    (perf_counter() - started) * 1000,
                )
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=max(1, poll_interval_seconds)
                )
            except TimeoutError:
                pass

    task = asyncio.create_task(collect(), name="wealthsail-opportunity-nav-collection")
    try:
        await stop_event.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
