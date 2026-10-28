"""Global trading-day sync for official historical NAVs of held open funds.

The task deliberately shares the existing ``market.market_observations``
``NAV`` persistence boundary.  It does not enqueue tenant workflow jobs or
materialize account values: the data is a reusable market fact, while tenant
reports continue to read and cache their own derived trend results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, Sequence

import httpx

from backend.common.database import Pool, system_connection
from backend.config import Settings
from backend.funds.nav_window import (
    NAV_HISTORY_FETCH_CALENDAR_DAYS,
    NAV_SYNC_TARGET_SAMPLES,
)
from backend.integrations.market_data import (
    FUNDSOURCE_NAME,
    Observation,
    fetch_fund_nav_history,
)
from backend.jobs.data_tasks import NavPersistenceStats, persist_nav_observations

logger = logging.getLogger("wealthsail.fund_nav_sync")

# Query past the storage target so a not-yet-disclosed current NAV (or a
# delayed overseas disclosure) does not leave the first backfill at 549 rows.
_DISCLOSURE_BUFFER_TRADING_DAYS = 10
_INITIAL_HISTORY_TRADING_DAYS = (
    NAV_SYNC_TARGET_SAMPLES + _DISCLOSURE_BUFFER_TRADING_DAYS
)
_INCREMENTAL_OVERLAP_DAYS = 7
_CALENDAR_FALLBACK_DAYS = NAV_HISTORY_FETCH_CALENDAR_DAYS


@dataclass(frozen=True, slots=True)
class HeldFund:
    """One globally deduplicated held off-exchange fund."""

    instrument_id: str
    symbol: str
    latest_nav_on: date | None
    history_sample_count: int = 0


@dataclass(frozen=True, slots=True)
class FundNavHistorySyncStats:
    """Bounded, non-sensitive run outcome for Worker logs and monitoring."""

    scanned_instruments: int
    fetched_observations: int
    persisted_observations: int
    skipped_observations: int
    empty_instruments: int
    failed_instruments: int


NavFetcher = Callable[
    [str, date, date, httpx.AsyncClient], Awaitable[list[Observation]]
]
NavPersister = Callable[[Pool, str, list[Observation]], Awaitable[NavPersistenceStats]]


def nav_history_window(
    *,
    latest_nav_on: date | None,
    trading_days: Sequence[date],
    as_of: date,
    history_sample_count: int = 0,
) -> tuple[date, date]:
    """Return an inclusive fetch window for one fund.

    A new or shallow fund requests 560 exchange sessions to leave disclosure
    room beyond the 550 stored-sample target. Complete histories only re-read
    seven calendar days so a late provider correction can replace a revision.
    """

    if latest_nav_on is not None and history_sample_count >= NAV_SYNC_TARGET_SAMPLES:
        return latest_nav_on - timedelta(days=_INCREMENTAL_OVERLAP_DAYS), as_of

    normalized_days = sorted(
        {item for item in trading_days if item <= as_of}, reverse=True
    )
    if len(normalized_days) >= _INITIAL_HISTORY_TRADING_DAYS:
        return normalized_days[_INITIAL_HISTORY_TRADING_DAYS - 1], as_of
    # The Worker bootstraps the exchange calendar before the scheduler starts.
    # This fallback keeps a cold or temporarily incomplete calendar from
    # preventing the first truthful NAV import; 900 days includes holiday room.
    return as_of - timedelta(days=_CALENDAR_FALLBACK_DAYS), as_of


async def sync_fund_nav_history(
    pool: Pool,
    settings: Settings,
    now: datetime,
    *,
    fetcher: NavFetcher | None = None,
    persister: NavPersister | None = None,
) -> FundNavHistorySyncStats:
    """Synchronise all currently held off-exchange funds once at 22:00.

    Position scanning is global and deduplicated by the reference instrument,
    so one fund held by several users produces one provider request and one
    shared NAV history.  A provider failure is isolated to that fund and must
    never stop later funds or the Worker scheduler.
    """

    as_of = now.date()
    async with system_connection(pool) as conn:
        # SQL already selects distinct instruments, while this second boundary
        # protects provider load if an adapter or future query regresses.
        funds = _deduplicate_held_funds(await _load_held_funds(conn))
        trading_days = await _load_recent_trading_days(conn, as_of)
    if not funds:
        return FundNavHistorySyncStats(0, 0, 0, 0, 0, 0)

    if fetcher is None:

        async def nav_fetcher(
            symbol: str, start: date, end: date, client: httpx.AsyncClient
        ) -> list[Observation]:
            # Keep the global job bounded by the same provider pagination
            # settings as tenant-triggered historical NAV backfills.
            return await fetch_fund_nav_history(
                symbol,
                start,
                end,
                client,
                page_size=settings.fund_nav_history_page_size,
                max_pages=settings.fund_nav_history_max_pages,
            )
    else:
        nav_fetcher = fetcher
    nav_persister = persister or persist_nav_observations
    fetched = persisted = skipped = empty = failed = 0
    async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
        for fund in funds:
            start, end = nav_history_window(
                latest_nav_on=fund.latest_nav_on,
                history_sample_count=fund.history_sample_count,
                trading_days=trading_days,
                as_of=as_of,
            )
            try:
                observations = await nav_fetcher(fund.symbol, start, end, client)
                fetched += len(observations)
                if not observations:
                    empty += 1
                    logger.info(
                        "fund_nav_history_sync_empty symbol=%s start=%s end=%s",
                        fund.symbol,
                        start.isoformat(),
                        end.isoformat(),
                    )
                    continue
                result = await nav_persister(pool, fund.instrument_id, observations)
                persisted += result.inserted_count
                skipped += result.skipped_count
                logger.info(
                    "fund_nav_history_sync_persisted symbol=%s observations=%d inserted=%d skipped=%d",
                    fund.symbol,
                    len(observations),
                    result.inserted_count,
                    result.skipped_count,
                )
            except Exception as exc:  # noqa: BLE001 -- one vendor symbol must not stop all funds
                failed += 1
                logger.warning(
                    "fund_nav_history_sync_failed symbol=%s error_class=%s",
                    fund.symbol,
                    type(exc).__name__,
                )

    stats = FundNavHistorySyncStats(
        scanned_instruments=len(funds),
        fetched_observations=fetched,
        persisted_observations=persisted,
        skipped_observations=skipped,
        empty_instruments=empty,
        failed_instruments=failed,
    )
    logger.info(
        "fund_nav_history_sync_complete scanned=%d fetched=%d persisted=%d skipped=%d empty=%d failed=%d",
        stats.scanned_instruments,
        stats.fetched_observations,
        stats.persisted_observations,
        stats.skipped_observations,
        stats.empty_instruments,
        stats.failed_instruments,
    )
    return stats


def _deduplicate_held_funds(funds: Sequence[HeldFund]) -> list[HeldFund]:
    """Keep one global sync target per reference instrument."""

    by_instrument: dict[str, HeldFund] = {}
    for fund in funds:
        by_instrument.setdefault(fund.instrument_id, fund)
    return list(by_instrument.values())


async def _load_held_funds(conn) -> list[HeldFund]:
    """Read active holdings across every tenant, once per reference fund."""

    rows = await conn.fetch(
        """
        WITH latest_positions AS (
            SELECT DISTINCT ON (
                       snapshot.tenant_id, snapshot.account_id, snapshot.instrument_id
                   )
                   snapshot.tenant_id, snapshot.account_id, snapshot.instrument_id,
                   snapshot.quantity
              FROM ledger.position_snapshots snapshot
             ORDER BY snapshot.tenant_id, snapshot.account_id,
                      snapshot.instrument_id, snapshot.as_of DESC, snapshot.created_at DESC
        ), held_funds AS (
            SELECT DISTINCT position.instrument_id
              FROM latest_positions position
             WHERE position.quantity > 0
        )
        SELECT instrument.id AS instrument_id,
               alias.alias_value AS symbol,
               latest_nav.latest_nav_on, latest_nav.history_sample_count
          FROM held_funds held
          JOIN reference.instruments instrument ON instrument.id = held.instrument_id
          JOIN LATERAL (
              SELECT instrument_alias.alias_value
                FROM reference.instrument_aliases instrument_alias
               WHERE instrument_alias.instrument_id = instrument.id
                 AND instrument_alias.alias_type = 'TICKER'
                 AND instrument_alias.effective_from <= CURRENT_DATE
                 AND (
                     instrument_alias.effective_to IS NULL
                     OR instrument_alias.effective_to >= CURRENT_DATE
                 )
               ORDER BY instrument_alias.effective_from DESC
               LIMIT 1
          ) alias ON TRUE
          LEFT JOIN LATERAL (
              SELECT max((observation.as_of AT TIME ZONE 'Asia/Shanghai')::date)
                         AS latest_nav_on,
                     count(DISTINCT (observation.as_of AT TIME ZONE 'Asia/Shanghai')::date)
                         AS history_sample_count
                FROM market.market_observations observation
                JOIN reference.data_sources source ON source.id = observation.source_id
               WHERE observation.subject_type = 'FUND'
                 AND observation.subject_id = instrument.id
                 AND observation.field_code = 'NAV'
                 AND observation.value IS NOT NULL
                 AND observation.value > 0
                 AND source.name = $1
          ) latest_nav ON TRUE
         WHERE instrument.asset_type = 'OFF_EXCHANGE_FUND'
           AND instrument.lifecycle_status = 'ACTIVE'
         ORDER BY alias.alias_value
        """,
        FUNDSOURCE_NAME,
    )
    return [
        HeldFund(
            instrument_id=str(row["instrument_id"]),
            symbol=str(row["symbol"]).strip().zfill(6),
            latest_nav_on=row["latest_nav_on"],
            history_sample_count=int(row.get("history_sample_count") or 0),
        )
        for row in rows
    ]


async def _load_recent_trading_days(conn, as_of: date) -> list[date]:
    rows = await conn.fetch(
        """
        SELECT calendar_date
          FROM reference.trading_calendars
         WHERE market = 'SSE'
           AND is_trading_day = TRUE
           AND calendar_date <= $1
         ORDER BY calendar_date DESC
         LIMIT $2
        """,
        as_of,
        _INITIAL_HISTORY_TRADING_DAYS,
    )
    days = [row["calendar_date"] for row in rows]
    if len(days) >= _INITIAL_HISTORY_TRADING_DAYS:
        return days

    # Synthesizing 550 weekdays misses exchange holidays and would still yield
    # fewer than 550 NAV samples. Let the window use the 900-day safety margin.
    return []
