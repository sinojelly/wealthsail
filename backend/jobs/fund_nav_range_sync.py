"""Daily shared accumulated-NAV range refresh for the fund screener."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Sequence

from backend.common.database import Pool, system_connection
from backend.config import Settings
from backend.integrations.fund_nav_ranges import (
    FundNavRange,
    fetch_fund_identity,
    fetch_fund_nav_range,
)


logger = logging.getLogger("wealthsail.fund_nav_range_sync")


@dataclass(frozen=True, slots=True)
class HeldFundForRange:
    """A globally shared held fund that needs one cache refresh."""

    instrument_id: str
    fund_code: str
    fund_name: str
    fund_type: str


@dataclass(frozen=True, slots=True)
class FundNavRangeSyncStats:
    """Non-sensitive daily refresh outcome for logs and monitoring."""

    scanned_funds: int
    available_funds: int
    unavailable_funds: int
    failed_funds: int


RangeFetcher = Callable[..., Awaitable[FundNavRange]]
RangePersister = Callable[[Pool, FundNavRange], Awaitable[None]]
FundIdentityResolver = Callable[[str], Awaitable[tuple[str, str]]]


async def fetch_and_persist_fund_nav_range(
    pool: Pool,
    fund_code: str,
    *,
    identity_resolver: FundIdentityResolver | None = None,
    range_fetcher: RangeFetcher | None = None,
    persister: RangePersister | None = None,
) -> FundNavRange:
    """Resolve one fund identity, then refresh its shared accumulated-NAV cache.

    The API only creates a durable request.  This Worker-only helper owns all
    provider I/O, so an API restart cannot lose a requested refresh.  A range
    provider outage remains an explicit ``UNAVAILABLE`` row; an identity lookup
    outage is retried by the durable job because a name must never be invented.

    With no explicit resolver the identity resolves from the shared fund
    master table first and only falls back to the provider on a miss.
    """

    if identity_resolver is None:
        from backend.jobs.fund_master_sync import (  # noqa: PLC0415
            master_first_identity_resolver,
        )

        def _master_conn_factory(_fund_code: str):
            return system_connection(pool)

        identity_resolver = master_first_identity_resolver(
            _master_conn_factory, fetch_fund_identity
        )
    fetched = range_fetcher or fetch_fund_nav_range
    persist = persister or persist_fund_nav_range
    fund_name, fund_type = await identity_resolver(fund_code)
    result = await fetched(fund_code, fund_name=fund_name, fund_type=fund_type)
    await persist(pool, result)
    return result


async def sync_fund_nav_ranges(
    pool: Pool,
    settings: Settings,
    now: datetime,
    *,
    fetcher: RangeFetcher | None = None,
    persister: RangePersister | None = None,
) -> FundNavRangeSyncStats:
    """Refresh one accumulated-NAV range per currently held OTC fund.

    The table is a shared reference cache: fund codes are deduplicated across
    tenants and every source failure is persisted as ``UNAVAILABLE``.  It does
    not enqueue tenant jobs or alter any existing official-NAV history.
    """

    del settings, now  # kept in the standard Worker task signature for parity
    async with system_connection(pool) as conn:
        funds = _deduplicate_held_funds(await _load_held_funds(conn))
    range_fetcher = fetcher or fetch_fund_nav_range
    range_persister = persister or persist_fund_nav_range
    available = unavailable = failed = 0
    for fund in funds:
        try:
            result = await range_fetcher(
                fund.fund_code,
                fund_name=fund.fund_name,
                fund_type=fund.fund_type,
            )
            await range_persister(pool, result)
            if result.data_source == "UNAVAILABLE":
                unavailable += 1
            else:
                available += 1
        except Exception as exc:  # noqa: BLE001 -- isolate one fund's failure
            failed += 1
            logger.warning(
                "fund_nav_range_sync_failed fund_code=%s error_class=%s",
                fund.fund_code,
                type(exc).__name__,
            )

    stats = FundNavRangeSyncStats(
        scanned_funds=len(funds),
        available_funds=available,
        unavailable_funds=unavailable,
        failed_funds=failed,
    )
    logger.info(
        "fund_nav_range_sync_complete scanned=%d available=%d unavailable=%d failed=%d",
        stats.scanned_funds,
        stats.available_funds,
        stats.unavailable_funds,
        stats.failed_funds,
    )
    return stats


async def persist_fund_nav_range(pool: Pool, result: FundNavRange) -> None:
    """Write one cache row through the migration-governed Worker function."""

    async with system_connection(pool) as conn:
        await conn.fetchval(
            """
            SELECT reference.upsert_fund_nav_range(
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
            )
            """,
            result.fund_code,
            result.fund_name,
            result.fund_type,
            result.latest_nav,
            result.latest_nav_date,
            result.highest_nav,
            result.highest_nav_date,
            result.lowest_nav,
            result.lowest_nav_date,
            result.drawdown_from_high,
            result.rise_from_low,
            result.current_percentile,
            result.inception_date,
            result.data_source,
        )


def _deduplicate_held_funds(funds: Sequence[HeldFundForRange]) -> list[HeldFundForRange]:
    by_code: dict[str, HeldFundForRange] = {}
    for fund in funds:
        by_code.setdefault(fund.fund_code, fund)
    return list(by_code.values())


async def _load_held_funds(conn) -> list[HeldFundForRange]:
    """Read active held OTC funds across all tenants without tenant output."""

    rows = await conn.fetch(
        """
        WITH latest_positions AS (
            SELECT DISTINCT ON (
                       snapshot.tenant_id, snapshot.account_id, snapshot.instrument_id
                   )
                   snapshot.instrument_id, snapshot.quantity
              FROM ledger.position_snapshots snapshot
             ORDER BY snapshot.tenant_id, snapshot.account_id,
                      snapshot.instrument_id, snapshot.as_of DESC, snapshot.created_at DESC
        )
        SELECT instrument.id AS instrument_id,
               alias.alias_value AS symbol,
               instrument.canonical_name AS fund_name,
               instrument.asset_type AS fund_type
          FROM latest_positions position
          JOIN reference.instruments instrument ON instrument.id = position.instrument_id
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
         WHERE position.quantity > 0
           AND instrument.asset_type = 'OFF_EXCHANGE_FUND'
           AND instrument.lifecycle_status = 'ACTIVE'
         ORDER BY alias.alias_value
        """
    )
    return [
        HeldFundForRange(
            instrument_id=str(row["instrument_id"]),
            fund_code=str(row["symbol"]).strip().zfill(6),
            fund_name=str(row["fund_name"]),
            fund_type=str(row["fund_type"]),
        )
        for row in rows
    ]
