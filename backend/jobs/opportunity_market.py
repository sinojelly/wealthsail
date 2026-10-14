"""Public opportunity facts and read-only reuse of governed pre-market inputs."""

import asyncio
import json
import logging
import re
from datetime import date, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.common.database import system_connection
from backend.dependencies import now_shanghai
from backend.macro import market_repository as repository

logger = logging.getLogger("wealthsail.opportunity_market")
_JOB = "OPPORTUNITY_MARKET_EVIDENCE"
_DAY = timedelta(hours=24)
_HOUR = timedelta(hours=1)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_NEW_YORK = ZoneInfo("America/New_York")


def _time(value):
    result = repository._stamp(value)
    if result is None:
        raise ValueError("opportunity collection requires an aware timestamp")
    return result.astimezone(_SHANGHAI)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _refresh_due(now, previous):
    now = _time(now)
    if previous is None:
        return {"trigger": "BOOT", "retry_until": None}
    result = _json(previous["result"])
    recorded = _time(previous["recorded_at"])
    deadline = _time(result["retryUntil"]) if result.get("retryUntil") else None
    if (
        result.get("status") not in {"COMPLETED", "SKIPPED"}
        and deadline
        and now < deadline
    ):
        return (
            {"trigger": "RETRY", "retry_until": deadline}
            if now >= recorded + _HOUR
            else None
        )
    if now >= recorded + _DAY or deadline and now >= deadline:
        return {
            "trigger": "REGULAR",
            "retry_until": None,
            "retry_exhausted": bool(
                result.get("retryExhausted") or deadline and now >= deadline
            ),
        }
    return None


async def _claim_refresh(conn, now, *, force=False):
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", _JOB)
        active = await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM market.macro_evidence_attempts c
              WHERE c.job_key=$1 AND c.phase='CLAIMED' AND c.recorded_at<=$2 AND c.lease_until>$2
              AND NOT EXISTS(SELECT 1 FROM market.macro_evidence_attempts t
                WHERE t.run_id=c.run_id AND t.phase IN ('FINISHED','CANCELLED')))""",
            _JOB,
            now,
        )
        if active:
            return None
        previous = await conn.fetchrow(
            """SELECT * FROM market.macro_evidence_attempts WHERE job_key=$1
              AND phase IN ('FINISHED','CANCELLED') AND recorded_at<=$2
              ORDER BY recorded_at DESC,id DESC LIMIT 1""",
            _JOB,
            now,
        )
        due = (
            {"trigger": "FORCE", "retry_until": None}
            if force
            else _refresh_due(now, dict(previous) if previous else None)
        )
        if due is None:
            return None
        identity = uuid4()
        await conn.execute(
            """INSERT INTO market.macro_evidence_attempts(run_id,job_key,phase,started_at,recorded_at,lease_until,trigger,release_keys)
              VALUES($1,$2,'CLAIMED',$3,$3,$4,$5,'[]'::jsonb)""",
            identity,
            _JOB,
            now,
            now + timedelta(minutes=10),
            due["trigger"],
        )
        return {"id": identity, "started_at": now, **due}


async def _finish_refresh(conn, claim, now, result, *, cancelled=False):
    await conn.execute(
        """INSERT INTO market.macro_evidence_attempts(run_id,job_key,phase,started_at,recorded_at,trigger,release_keys,result)
          VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb) ON CONFLICT DO NOTHING""",
        claim["id"],
        _JOB,
        "CANCELLED" if cancelled else "FINISHED",
        claim["started_at"],
        now,
        claim["trigger"],
        "[]",
        json.dumps(result, default=str, ensure_ascii=False),
    )


async def fetch_opportunity_series(now, permissions=None):
    from backend.integrations.opportunity_sources import (
        fetch_opportunity_series as fetch,
    )

    return await fetch(now, permissions=permissions)


def _quote_date(value, zone, retrieved, now):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        day = date.fromisoformat(value)
    else:
        stamp = repository._stamp(value)
        if stamp is None or stamp > retrieved or stamp > now:
            return None
        day = stamp.astimezone(zone).date()
    return (
        day
        if day <= retrieved.astimezone(zone).date()
        and day <= now.astimezone(zone).date()
        else None
    )


def _reuse_brief(brief, now):
    missing = {
        "status": "NOT_COLLECTED",
        "reason": "INSUFFICIENT_PROVENANCE",
        "observations": 0,
    }
    if not brief:
        return [], {**missing, "reason": "NO_STORED_BRIEF"}
    try:
        context = _json(brief["source_context"])
    except (KeyError, TypeError, ValueError):
        return [], missing
    if not isinstance(context, dict):
        return [], missing
    for field in ("generated_at", "created_at"):
        stamp = repository._stamp(brief.get(field))
        if stamp is None or stamp > now:
            return [], missing
    names = {
        "标普500": "US_SP500_CHANGE_PCT",
        "标普500指数": "US_SP500_CHANGE_PCT",
        "纳斯达克": "US_NASDAQ_CHANGE_PCT",
        "纳斯达克综合指数": "US_NASDAQ_CHANGE_PCT",
    }
    quotes = (
        [
            (names.get(q.get("name")), q)
            for q in context.get("usIndices", [])
            if isinstance(q, dict)
        ]
        if isinstance(context.get("usIndices"), list)
        else []
    )
    if isinstance(context.get("a50Future"), dict):
        quotes.append(("A50_CHANGE_PCT", context["a50Future"]))
    records, skipped = [], 0
    for code, quote in quotes:
        if code is None:
            continue
        try:
            retrieved = repository._stamp(
                quote.get("retrievedAt") or context.get("retrievedAt")
            )
            source = quote.get("sourceName") or context.get("sourceName")
            method = quote.get("methodVersion") or context.get("methodVersion")
            if (
                quote.get("qualityStatus") != "FRESH"
                or not retrieved
                or retrieved > now
                or not isinstance(source, str)
                or not source.strip()
                or not isinstance(method, str)
                or not method.strip()
            ):
                raise ValueError("missing governed quote provenance")
            zone = _NEW_YORK if code.startswith("US_") else _SHANGHAI
            source_time = quote.get("sourceAsOf", quote.get("asOf"))
            reference = _quote_date(source_time, zone, retrieved, now)
            if reference is None:
                raise ValueError("missing quote reference time")
            # Reuse the existing pure freshness rule, without invoking its HTTP clients.
            from backend.integrations.market_data import _market_source_quality

            if _market_source_quality(source_time, now) != "FRESH":
                raise ValueError("stored quote has become stale")
            record = {
                "code": code,
                "referenceDate": reference.isoformat(),
                "frequency": "DAILY",
                "unit": "%",
                "value": quote.get("changePct"),
                "sourceUri": quote.get("sourceUri"),
                "sourceName": source,
                "publishedAt": None,
                "retrievedAt": retrieved,
                "availableAt": retrieved,
                "timeBasis": "RETRIEVAL_ONLY",
                "methodVersion": "opportunity-premarket-reuse-v1",
                "metadata": {
                    "definitionKey": "PRE_MARKET:" + code,
                    "definitionVersion": method,
                    "sourceKey": "PRE_MARKET",
                    "originBriefId": str(brief["id"]),
                    "sourceAsOf": source_time,
                    "qualityStatus": "FRESH",
                },
            }
            normalized = repository.normalize_series(record, now)
            if normalized["value"] is None:
                raise ValueError("invalid stored quote value")
            records.append(record)
        except (KeyError, TypeError, ValueError, OverflowError):
            skipped += 1
    return records, {
        "status": "SUCCESS",
        "observations": len(records),
        "skippedFacts": skipped,
    } if records else {**missing, "skippedFacts": skipped}


async def _read_pre_market(conn, now):
    brief = await conn.fetchrow(
        """SELECT id,trade_date,source_context,quality_status,generated_at,created_at
          FROM reporting.shared_pre_market_briefs WHERE trade_date<=($1 AT TIME ZONE 'Asia/Shanghai')::date
          AND generated_at<=$1 AND created_at<=$1 ORDER BY trade_date DESC,generated_at DESC,id LIMIT 1""",
        now,
    )
    return _reuse_brief(dict(brief) if brief else None, now)


def _source_key(record):
    if isinstance(record, dict):
        known = repository.CATALOG.get(record.get("code"))
        if known:
            return known[3]
        metadata = record.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("sourceKey"), str):
            return metadata["sourceKey"]
    return "UNKNOWN_SOURCE"


def _prepare_records(records, finished, permissions, outcomes, *, newly_fetched):
    groups, rejected = {}, 0
    for record in records:
        source = _source_key(record)
        try:
            normalized = repository.normalize_series(
                record, finished, permissions=permissions
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected += 1
            outcome = outcomes.setdefault(source, {})
            outcome.update(
                status="FAILED",
                reason="REJECTED_OBSERVATIONS",
                rejected=outcome.get("rejected", 0) + 1,
            )
            continue
        if normalized["value"] is None or normalized["metadata"].get(
            "qualityStatus"
        ) in {"CONFLICT", "UNAVAILABLE"}:
            outcome = outcomes.setdefault(source, {})
            outcome.update(
                status="FAILED",
                reason="INVALID_OBSERVATIONS",
                invalidObservations=outcome.get("invalidObservations", 0) + 1,
            )
        else:
            outcome = outcomes.setdefault(source, {})
            outcome["validObservations"] = outcome.get("validObservations", 0) + 1
        adopted = dict(record)
        if newly_fetched:
            # The cohort becomes available after the actual network completion,
            # while the publisher's independently observed release stays intact.
            adopted.update(retrievedAt=finished, availableAt=finished)
        groups.setdefault(source, []).append(adopted)
    return groups, rejected


def _status(outcomes, rejected, inserted):
    active = [
        row.get("status")
        for row in outcomes.values()
        if row.get("status") not in {"PERMISSION_REQUIRED", "NOT_COLLECTED", "SKIPPED"}
    ]
    if outcomes and not rejected and all(state == "SUCCESS" for state in active):
        return "COMPLETED"
    partial_rows = inserted and any(
        row.get("validObservations")
        and (row.get("rejected") or row.get("invalidObservations"))
        for row in outcomes.values()
    )
    return "PARTIAL" if "SUCCESS" in active or partial_rows else "UNAVAILABLE"


def _retry_result(result, claim, finished):
    if result["status"] != "COMPLETED":
        if claim.get("retry_exhausted"):
            result["retryExhausted"] = True
        else:
            result["retryUntil"] = claim.get("retry_until") or finished + _DAY


async def sync_opportunity_market(pool, now, *, force=False, now_provider=None):
    now, now_provider = _time(now), now_provider or now_shanghai
    async with system_connection(pool) as conn:
        claim = await _claim_refresh(conn, now, force=force)
    if claim is None:
        return {"status": "SKIPPED", "reason": "RECENT_ATTEMPT_OR_ACTIVE_CLAIM"}
    result = {
        "status": "UNAVAILABLE",
        "observations": 0,
        "rejected": 0,
        "sourceResults": {},
    }
    try:
        try:
            async with system_connection(pool) as conn:
                permissions = await repository.read_permissions(conn)
                try:
                    reused, pre_market = await _read_pre_market(conn, now)
                except Exception as exc:  # noqa: BLE001 -- old report storage fails independently
                    reused, pre_market = (
                        [],
                        {"status": "FAILED", "errorClass": type(exc).__name__},
                    )
            try:
                data = await fetch_opportunity_series(now, permissions=permissions)
            except Exception as exc:  # noqa: BLE001 -- keep durable reusable facts when HTTP fails
                data = {
                    "observations": [],
                    "sourceResults": {
                        "PUBLIC_SOURCES": {
                            "status": "FAILED",
                            "errorClass": type(exc).__name__,
                        }
                    },
                }
                result["errorClass"] = type(exc).__name__
            finished = max(now, _time(now_provider()))
            outcomes = {
                key: dict(value) for key, value in data.get("sourceResults", {}).items()
            }
            outcomes["PRE_MARKET"] = pre_market
            result["sourceResults"] = outcomes
            groups, result["rejected"] = _prepare_records(
                data.get("observations", []),
                finished,
                permissions,
                outcomes,
                newly_fetched=True,
            )
            reused_groups, rejected = _prepare_records(
                reused, finished, permissions, outcomes, newly_fetched=False
            )
            result["rejected"] += rejected
            for source, rows in reused_groups.items():
                groups.setdefault(source, []).extend(rows)
            if groups:
                async with system_connection(pool) as conn:
                    for source, rows in groups.items():
                        try:
                            counts = await repository.append_series(
                                conn, rows, finished, permissions=permissions
                            )
                            result["observations"] += counts["observations"]
                            result["rejected"] += counts["rejected"]
                            if counts["rejected"]:
                                outcomes.setdefault(source, {}).update(
                                    status="FAILED",
                                    reason="REJECTED_OBSERVATIONS",
                                    rejected=counts["rejected"],
                                )
                        except Exception as exc:  # noqa: BLE001 -- each source append owns its transaction
                            outcomes.setdefault(source, {}).update(
                                status="FAILED", errorClass=type(exc).__name__
                            )
            result["status"] = _status(
                outcomes, result["rejected"], result["observations"]
            )
        except Exception as exc:  # noqa: BLE001 -- preserve prior facts and record terminal failure
            result.update(status="UNAVAILABLE", errorClass=type(exc).__name__)
        finished = max(now, _time(now_provider()))
        _retry_result(result, claim, finished)
        async with system_connection(pool) as conn:
            await _finish_refresh(conn, claim, finished, result)
        logger.info(
            "opportunity_collection status=%s observations=%s rejected=%s",
            result["status"],
            result["observations"],
            result["rejected"],
        )
        return result
    except asyncio.CancelledError:
        finished = max(now, _time(now_provider()))
        result.update(status="UNAVAILABLE", errorClass="CancelledError")
        _retry_result(result, claim, finished)

        async def finish_cancelled():
            async with system_connection(pool) as conn:
                await _finish_refresh(conn, claim, finished, result, cancelled=True)

        cleanup = asyncio.create_task(finish_cancelled())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
        raise


async def _wait(stop_event, seconds):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_opportunity_market_service(
    pool, *, now_provider=None, wait=None, stop_event=None
):
    now_provider, wait = now_provider or now_shanghai, wait or _wait
    stop_event, first = stop_event or asyncio.Event(), True
    while not stop_event.is_set():
        try:
            await sync_opportunity_market(
                pool, now_provider(), force=first, now_provider=now_provider
            )
        except Exception as exc:  # noqa: BLE001 -- a collection failure cannot stop the service
            logger.warning("opportunity_tick_failed error_class=%s", type(exc).__name__)
        first = False
        if not stop_event.is_set():
            await wait(stop_event, 900)
