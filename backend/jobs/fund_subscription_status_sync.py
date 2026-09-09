"""Global trading-day synchronization of TianTian Fund subscription status."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.common.database import Pool, system_connection
from backend.integrations.fund_subscription_status import (
    FundSubscriptionStatus,
    fetch_fund_subscription_status,
)


logger = logging.getLogger("wealthsail.fund_subscription_status_sync")


@dataclass(frozen=True, slots=True)
class HeldFund:
    """One globally deduplicated off-exchange fund held by any tenant."""

    instrument_id: str
    symbol: str


@dataclass(frozen=True, slots=True)
class FundSubscriptionStatusSyncStats:
    """Aggregate, non-sensitive outcome emitted by the Worker."""

    scanned_instruments: int
    available_instruments: int
    changed_instruments: int
    unchanged_instruments: int
    unavailable_instruments: int
    failed_instruments: int


FundStatusFetcher = Callable[
    [tuple[str, ...]], Awaitable[dict[str, FundSubscriptionStatus]]
]
FundStatusWriter = Callable[
    [Any, str, FundSubscriptionStatus, datetime], Awaitable[bool]
]


async def sync_fund_subscription_status(
    pool: Pool,
    now: datetime,
    *,
    fetcher: FundStatusFetcher | None = None,
    writer: FundStatusWriter | None = None,
) -> FundSubscriptionStatusSyncStats:
    """Synchronize held funds through the migration-controlled write function."""

    async with system_connection(pool) as conn:
        funds = _deduplicate_held_funds(await _load_held_funds(conn))
        if not funds:
            return FundSubscriptionStatusSyncStats(0, 0, 0, 0, 0, 0)

        status_fetcher = fetcher or fetch_fund_subscription_status
        try:
            statuses = await status_fetcher(tuple(fund.symbol for fund in funds))
        except Exception as exc:  # noqa: BLE001 - reports must keep their schedule
            logger.warning(
                "fund_subscription_status_sync_fetch_failed error_class=%s",
                type(exc).__name__,
            )
            return FundSubscriptionStatusSyncStats(
                len(funds), 0, 0, 0, len(funds), 0
            )

        status_writer = writer or _persist_status
        available = changed = unchanged = unavailable = failed = 0
        for fund in funds:
            status = statuses.get(fund.symbol)
            if status is None:
                unavailable += 1
                continue
            available += 1
            try:
                did_change = await status_writer(conn, fund.instrument_id, status, now)
            except Exception as exc:  # noqa: BLE001 - one fund must not halt batch
                failed += 1
                logger.warning(
                    "fund_subscription_status_sync_persist_failed error_class=%s",
                    type(exc).__name__,
                )
                continue
            if did_change:
                changed += 1
            else:
                unchanged += 1

    stats = FundSubscriptionStatusSyncStats(
        scanned_instruments=len(funds),
        available_instruments=available,
        changed_instruments=changed,
        unchanged_instruments=unchanged,
        unavailable_instruments=unavailable,
        failed_instruments=failed,
    )
    logger.info(
        "fund_subscription_status_sync_complete scanned=%d available=%d changed=%d unchanged=%d unavailable=%d failed=%d",
        stats.scanned_instruments,
        stats.available_instruments,
        stats.changed_instruments,
        stats.unchanged_instruments,
        stats.unavailable_instruments,
        stats.failed_instruments,
    )
    return stats


def _deduplicate_held_funds(funds: Sequence[HeldFund]) -> list[HeldFund]:
    """Defend the provider boundary if the global holdings query duplicates."""

    by_instrument: dict[str, HeldFund] = {}
    for fund in funds:
        by_instrument.setdefault(fund.instrument_id, fund)
    return list(by_instrument.values())


async def _load_held_funds(conn: Any) -> list[HeldFund]:
    """Read active, positive OTC-fund holdings across all tenants once."""

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
        )
        SELECT DISTINCT instrument.id AS instrument_id,
               alias.alias_value AS symbol
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
        HeldFund(
            instrument_id=str(row["instrument_id"]),
            symbol=str(row["symbol"]).strip().zfill(6),
        )
        for row in rows
    ]


async def _persist_status(
    conn: Any,
    instrument_id: str,
    status: FundSubscriptionStatus,
    now: datetime,
) -> bool:
    """Persist one status through migration 061's sole Worker write path."""

    return bool(
        await conn.fetchval(
            """
            SELECT reference.upsert_fund_profile_status(
                $1::uuid, $2, $3, $4, $5, $6::jsonb, $7
            )
            """,
            instrument_id,
            status.purchase_status,
            status.redemption_status,
            now.date(),
            status.source_ref,
            json.dumps(dict(status.metadata), ensure_ascii=False, sort_keys=True),
            now,
        )
    )
