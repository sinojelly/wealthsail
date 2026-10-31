"""Public opportunity facts and read-only reuse of governed pre-market inputs."""

import asyncio
import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
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


def relative_strength_benchmark(analysis, now):
    """Freeze visible CSI300 observations; a missing benchmark stays unknown."""
    from copy import deepcopy
    from urllib.parse import urlsplit

    now = _time(now)
    unavailable = {
        "state": "UNAVAILABLE",
        "indexId": "hs300",
        "label": "沪深300",
        "points": [],
        "reason": "CSI300_HISTORY_UNAVAILABLE_OR_UNVERIFIED",
    }
    if not isinstance(analysis, dict) or analysis.get("dataQuality") != "READY":
        return unavailable
    try:
        retrieved = _time(analysis.get("retrievedAt"))
        day = date.fromisoformat(str(analysis.get("asOf")))
        source = urlsplit(analysis.get("source", ""))
        points = analysis.get("points")
        if (
            retrieved > now
            or not 0 <= (now.date() - day).days <= 10
            or source.scheme != "https"
            or not source.hostname
            or source.username
            or source.password
            or source.port not in {None, 443}
            or not isinstance(points, list)
            or not points
        ):
            return unavailable
        for field in ("availableAt", "publishedAt"):
            if analysis.get(field) is not None and _time(analysis[field]) > now:
                return unavailable
        previous = None
        for point in points:
            observed = date.fromisoformat(str(point["date"]))
            close = repository._number(point["close"])
            if (
                close is None
                or close <= 0
                or observed > day
                or previous is not None
                and observed <= previous
                or point.get("isTradingDay") is False
            ):
                return unavailable
            previous = observed
        if previous != day:
            return unavailable
    except (ValueError, TypeError, KeyError):
        return unavailable
    return {
        "state": "READY",
        "indexId": "hs300",
        "label": "沪深300",
        "asOf": day.isoformat(),
        "retrievedAt": retrieved.isoformat(),
        "sourceUri": analysis["source"],
        "points": deepcopy(points[-600:]),
    }


def normalize_subscription_status(value):
    """Project verified subscription facts and derive the small-limit guard."""
    if not isinstance(value, dict):
        return None
    purchase = value.get("purchaseStatus")
    if not isinstance(purchase, str) or purchase not in {
        "OPEN",
        "LIMITED",
        "SUSPENDED",
    }:
        return None
    raw_limit = value.get("dailyLimitCny")
    limit = None
    if not isinstance(raw_limit, bool) and isinstance(
        raw_limit, (str, int, float, Decimal)
    ):
        try:
            amount = Decimal(str(raw_limit))
            if amount.is_finite() and amount >= 0:
                limit = amount
        except (InvalidOperation, ValueError):
            pass
    label = (
        "⛔ 暂停申购"
        if purchase == "SUSPENDED"
        else f"⛔ 单日限购{format(limit.normalize(), 'f')}元"
        if limit is not None and limit <= 100
        else None
    )
    return {
        "purchaseStatus": purchase,
        "dailyLimitCny": format(limit.normalize(), "f") if limit is not None else None,
        "restricted": label is not None,
        "label": label,
        **({"retrievedAt": value["retrievedAt"]} if value.get("retrievedAt") else {}),
    }


async def read_fund_subscription_status(conn, now, fund_codes):
    """Read the effective shared status store; unknown status stays unknown.

    The subscription sync persists to ``reference.fund_profiles``; its narrow
    research view leaves the source table's worker isolation intact. Catalog
    funds without an instrument id can resolve a verified ticker alias.
    """
    codes = sorted(
        {
            code
            for code in fund_codes
            if isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code)
        }
    )
    if not codes:
        return {}
    now = _time(now)
    rows = await conn.fetch(
        """WITH products AS (
          SELECT c.fund_code,c.instrument_id FROM reference.research_fund_catalog c
            WHERE c.market='FUND_CN' AND c.fund_code=ANY($1::text[])
              AND c.instrument_id IS NOT NULL
          UNION
          SELECT a.alias_value,a.instrument_id FROM reference.instrument_aliases a
            JOIN reference.instruments i ON i.id=a.instrument_id
            WHERE a.alias_type='TICKER' AND a.alias_value=ANY($1::text[])
              AND i.asset_type='OFF_EXCHANGE_FUND'
              AND a.effective_from<=($2 AT TIME ZONE 'Asia/Shanghai')::date
              AND (a.effective_to IS NULL OR a.effective_to>=($2 AT TIME ZONE 'Asia/Shanghai')::date)
        ), identities AS (
          SELECT fund_code FROM products GROUP BY fund_code
            HAVING count(DISTINCT instrument_id)=1
        ) SELECT DISTINCT ON(p.fund_code) p.fund_code,profile.purchase_status,
            profile.daily_purchase_limit,profile.retrieved_at
          FROM products p JOIN identities identity USING(fund_code)
          JOIN reference.fund_subscription_status profile ON profile.instrument_id=p.instrument_id
          WHERE profile.effective_from<=($2 AT TIME ZONE 'Asia/Shanghai')::date
            AND (profile.effective_to IS NULL OR profile.effective_to>=($2 AT TIME ZONE 'Asia/Shanghai')::date)
            AND (profile.retrieved_at IS NULL OR profile.retrieved_at<=$2)
          ORDER BY p.fund_code,profile.effective_from DESC,profile.retrieved_at DESC NULLS LAST""",
        codes,
        now,
    )
    statuses = {}
    for row in rows:
        code = row.get("fund_code")
        retrieved = repository._stamp(row.get("retrieved_at"))
        if (
            code not in codes
            or row.get("retrieved_at") is not None
            and (retrieved is None or retrieved > now)
        ):
            continue
        status = normalize_subscription_status(
            {
                "purchaseStatus": row.get("purchase_status"),
                "dailyLimitCny": row.get("daily_purchase_limit"),
                **({"retrievedAt": retrieved.isoformat()} if retrieved else {}),
            }
        )
        if status is not None:
            statuses[code] = status
    return statuses


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
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        try:
            await sync_opportunity_market(
                pool, now_provider(), force=False, now_provider=now_provider
            )
        except Exception as exc:  # noqa: BLE001 -- a collection failure cannot stop the service
            logger.warning("opportunity_tick_failed error_class=%s", type(exc).__name__)
        if not stop_event.is_set():
            await wait(stop_event, 900)
