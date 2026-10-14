"""Bootstrap monthly macro facts; coverage never verifies publication time.

Unchanged facts are deduplicated by the persistence layer. Collection freshness
therefore follows each source's latest retrieval snapshot, not observation time.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from backend.common.database import system_connection
from backend.jobs.investment_macro import sync_macro

logger = logging.getLogger("wealthsail.macro_refresh")
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MAX_AGE = timedelta(hours=24)
_SOURCES = {
    "CPI_YOY": "https://data.eastmoney.com/cjsj/cpi.html",
    "PMI": "https://data.eastmoney.com/cjsj/pmi.html",
    "PPI_YOY": "https://data.eastmoney.com/cjsj/ppi.html",
    "M2_YOY": "https://data.eastmoney.com/cjsj/hbgyl.html",
}


def _local_now(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("macro refresh requires a timezone-aware now")
    return now.astimezone(_SHANGHAI)


def _months(start: date, end: date) -> list[date]:
    months = []
    while start <= end:
        months.append(start)
        start = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return months


def _coverage(rows, snapshots, now):
    start = date(now.year - 5, now.month, 1)
    current_month = date(now.year, now.month, 1)
    source_snapshots = {row["source_uri"]: row for row in snapshots}
    periods = {code: set() for code in _SOURCES}
    for row in rows:
        try:
            value = Decimal(str(row["value"]))
            period = row["period"]
            if isinstance(period, datetime):
                period = period.astimezone(_SHANGHAI).date()
            if (
                isinstance(row["value"], bool)
                or not value.is_finite()
                or not isinstance(period, date)
                or period.day != 1
                or not start <= period <= current_month
            ):
                continue
            periods[row["field_code"]].add(period)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue

    result = {}
    for code, source_uri in _SOURCES.items():
        snapshot = source_snapshots.get(source_uri)
        latest_period, collected_at = None, None
        if snapshot:
            source_as_of = snapshot["source_as_of"]
            if isinstance(source_as_of, datetime):
                source_as_of = source_as_of.astimezone(_SHANGHAI).date()
            if isinstance(source_as_of, date):
                latest_period = source_as_of.replace(day=1)
            collected_at = snapshot["retrieved_at"]
        source_period_valid = (
            latest_period is not None and start <= latest_period <= current_month
        )
        actual = sorted(periods[code])
        known_latest = actual[-1] if actual else None
        end = latest_period if source_period_valid else known_latest
        if known_latest is not None:
            end = max(end, known_latest) if end else known_latest
        expected = _months(start, end) if end else []
        missing = sorted(set(expected) - set(actual))
        source_covers_known = bool(
            source_period_valid and (not known_latest or latest_period >= known_latest)
        )
        fresh = bool(
            isinstance(collected_at, datetime)
            and collected_at.tzinfo is not None
            and timedelta(0) <= now - collected_at < _MAX_AGE
        )
        result[code] = {
            "sourceUri": source_uri,
            "windowStart": start.strftime("%Y-%m"),
            "firstPeriod": actual[0].strftime("%Y-%m") if actual else None,
            "lastPeriod": actual[-1].strftime("%Y-%m") if actual else None,
            "sourceLatestPeriod": (
                latest_period.strftime("%Y-%m") if latest_period else None
            ),
            "observations": len(actual),
            "missingPeriods": [period.strftime("%Y-%m") for period in missing],
            "lastCollectedAt": collected_at.isoformat() if collected_at else None,
            "fresh": fresh,
            "sourceCoversKnownPeriods": source_covers_known,
            "complete": bool(source_covers_known and actual and not missing),
            "publicationVerified": False,
        }
    return result


async def _load_coverage(pool, now):
    start = datetime(now.year - 5, now.month, 1, tzinfo=_SHANGHAI)
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """WITH latest AS (
                SELECT DISTINCT ON (o.field_code, o.as_of)
                       o.field_code, o.value, o.as_of, o.method_version,
                       o.metadata, o.quality_status, s.origin_uri
                FROM market.market_observations o
                JOIN reference.data_sources d ON d.id=o.source_id
                JOIN market.source_snapshots s ON s.id=o.snapshot_id
                    AND s.source_id=o.source_id
                WHERE o.subject_type='MACRO_SERIES'
                  AND d.name='AKSHARE_RESEARCH_MACRO' AND d.source_type='MACRO'
                  AND o.as_of >= $1 AND o.as_of <= $2
                  AND o.observed_at <= $2 AND s.retrieved_at <= $2
                  AND (s.published_at IS NULL OR s.published_at <= $2)
                ORDER BY o.field_code, o.as_of, o.revision DESC, o.observed_at DESC
            )
            SELECT field_code, value,
                   (as_of AT TIME ZONE 'Asia/Shanghai')::date AS period
            FROM latest
            WHERE method_version='research-macro-history-v2'
              AND quality_status='STALE'
              AND metadata->>'publicationVerified'='false'
              AND metadata->>'sourceUri'=origin_uri
              AND origin_uri=CASE field_code
                  WHEN 'CPI_YOY' THEN 'https://data.eastmoney.com/cjsj/cpi.html'
                  WHEN 'PMI' THEN 'https://data.eastmoney.com/cjsj/pmi.html'
                  WHEN 'PPI_YOY' THEN 'https://data.eastmoney.com/cjsj/ppi.html'
                  WHEN 'M2_YOY' THEN 'https://data.eastmoney.com/cjsj/hbgyl.html'
              END""",
            start,
            now,
        )
        snapshots = await conn.fetch(
            """SELECT DISTINCT ON (s.origin_uri)
                      s.origin_uri AS source_uri, s.as_of AS source_as_of,
                      s.retrieved_at
               FROM market.source_snapshots s
               JOIN reference.data_sources d ON d.id=s.source_id
               WHERE d.name='AKSHARE_RESEARCH_MACRO' AND d.source_type='MACRO'
                 AND s.origin_uri=ANY($1::text[])
                 AND s.retrieval_key LIKE 'research-macro:%'
                 AND s.quality_status='STALE' AND s.retrieved_at <= $2
                 AND s.as_of <= $2
                 AND (s.published_at IS NULL OR s.published_at <= $2)
               ORDER BY s.origin_uri, s.retrieved_at DESC, s.id DESC""",
            list(_SOURCES.values()),
            now,
        )
    return _coverage(rows, snapshots, now)


async def _refresh(pool, now, *, previous=None):
    try:
        fetched = await sync_macro(pool, now)
    except Exception as exc:  # noqa: BLE001 -- preserve prior facts on source failure
        logger.warning("macro_refresh_failed error_class=%s", type(exc).__name__)
        return {
            "status": "UNAVAILABLE",
            "reason": "REFRESH_FAILED",
            "observations": 0,
            "coverage": previous or {},
        }
    try:
        coverage = await _load_coverage(pool, now)
    except Exception as exc:  # noqa: BLE001 -- a failed check cannot certify collection
        logger.warning("macro_coverage_failed error_class=%s", type(exc).__name__)
        return {
            "status": "UNAVAILABLE",
            "reason": "COVERAGE_CHECK_FAILED",
            "observations": fetched.get("observations", 0),
            "coverage": previous or {},
        }
    for evidence in coverage.values():
        collected = evidence["lastCollectedAt"]
        # An earlier successful collection cannot hide this attempt's failure.
        evidence["refreshed"] = bool(
            fetched.get("observations", 0) > 0
            and collected
            and datetime.fromisoformat(collected) == now
        )
    any_refreshed = any(row["refreshed"] for row in coverage.values())
    complete = all(
        row["refreshed"] and row["complete"] and row["fresh"]
        for row in coverage.values()
    )
    return {
        "status": "COMPLETED"
        if complete
        else "PARTIAL"
        if any_refreshed
        else "UNAVAILABLE",
        "reason": "REFRESHED"
        if complete
        else "SOURCE_OR_HISTORY_INCOMPLETE"
        if any_refreshed
        else "NO_SOURCE_REFRESHED",
        "observations": fetched.get("observations", 0),
        "coverage": coverage,
    }


async def ensure_macro_history(pool, now: datetime) -> dict:
    """Refresh missing, gapped or >=24h-old histories; no calendar dependency."""
    now = _local_now(now)
    try:
        coverage = await _load_coverage(pool, now)
    except Exception as exc:  # noqa: BLE001 -- callers can retry without blocking API
        logger.warning("macro_coverage_failed error_class=%s", type(exc).__name__)
        return {
            "status": "UNAVAILABLE",
            "reason": "COVERAGE_CHECK_FAILED",
            "observations": 0,
            "coverage": {},
        }
    if all(row["complete"] and row["fresh"] for row in coverage.values()):
        return {
            "status": "SKIPPED",
            "reason": "RECENT_COMPLETE_HISTORY",
            "observations": 0,
            "coverage": coverage,
        }
    return await _refresh(pool, now, previous=coverage)


async def refresh_macro_history(pool, now: datetime) -> dict:
    """Force the entire five-year fetch and report success separately per source."""
    return await _refresh(pool, _local_now(now))
