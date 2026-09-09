"""Global Worker task for auditable corporate-action synchronisation."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

from backend.common.database import Pool, system_connection
from backend.integrations.corporate_actions import (
    BAOSTOCK_CORPORATE_ACTIONS,
    AKSHARE_FUND_DIVIDEND_ACTIONS,
    CorporateAction,
    ProviderResult,
    fetch_akshare_fund_dividend_actions,
    fetch_akshare_actions,
    fetch_baostock_actions,
    reconcile_provider_results,
)

logger = logging.getLogger("wealthsail.corporate_actions_sync")

AkshareFetcher = Callable[[tuple[str, ...]], Awaitable[ProviderResult]]
BaostockFetcher = Callable[[tuple[str, ...], date], Awaitable[ProviderResult]]
FundDividendFetcher = Callable[[tuple[str, ...], date], Awaitable[ProviderResult]]
_SUPPORTED_ASSET_TYPES = frozenset(
    {"CN_STOCK", "EXCHANGE_FUND", "OFF_EXCHANGE_FUND"}
)


@dataclass(frozen=True)
class HeldInstrument:
    instrument_id: str
    symbol: str
    asset_type: str


@dataclass(frozen=True)
class CorporateActionSyncStats:
    scanned_instruments: int
    persisted_actions: int
    unavailable_instruments: int
    failed_instruments: int


async def sync_corporate_actions(
    pool: Pool,
    now: datetime,
    *,
    akshare_fetcher: AkshareFetcher | None = None,
    baostock_fetcher: BaostockFetcher | None = None,
    fund_dividend_fetcher: FundDividendFetcher | None = None,
) -> CorporateActionSyncStats:
    """Synchronise held stock/ETF/fund actions without creating workflow jobs."""

    as_of = now.date()
    async with system_connection(pool) as conn:
        instruments = await _load_held_instruments(conn)
        if not instruments:
            return CorporateActionSyncStats(0, 0, 0, 0)

        exchange_symbols = tuple(
            instrument.symbol
            for instrument in instruments
            if instrument.asset_type != "OFF_EXCHANGE_FUND"
        )
        stock_symbols = tuple(
            instrument.symbol
            for instrument in instruments
            if instrument.asset_type == "CN_STOCK"
        )
        fund_symbols = tuple(
            instrument.symbol
            for instrument in instruments
            if instrument.asset_type == "OFF_EXCHANGE_FUND"
        )
        akshare = akshare_fetcher or _default_akshare_fetcher
        baostock = baostock_fetcher or _default_baostock_fetcher
        fund_dividends = fund_dividend_fetcher or _default_fund_dividend_fetcher
        akshare_result = await akshare(exchange_symbols)
        baostock_result = (
            await baostock(stock_symbols, as_of)
            if stock_symbols
            else ProviderResult.available(BAOSTOCK_CORPORATE_ACTIONS, ())
        )
        fund_dividend_result = (
            await fund_dividends(fund_symbols, as_of)
            if fund_symbols
            else ProviderResult.available(AKSHARE_FUND_DIVIDEND_ACTIONS, ())
        )

        persisted = 0
        unavailable = 0
        failed = 0
        for instrument in instruments:
            if instrument.asset_type == "OFF_EXCHANGE_FUND":
                provider_results = [
                    _for_symbol(fund_dividend_result, instrument.symbol)
                ]
            else:
                provider_results = [_for_symbol(akshare_result, instrument.symbol)]
            if instrument.asset_type == "CN_STOCK":
                provider_results.append(_for_symbol(baostock_result, instrument.symbol))
            elif instrument.asset_type == "EXCHANGE_FUND":
                provider_results.append(
                    ProviderResult.unsupported(BAOSTOCK_CORPORATE_ACTIONS)
                )
            reconciled = reconcile_provider_results(instrument.symbol, provider_results)
            if reconciled.quality_status == "UNAVAILABLE":
                unavailable += 1
                logger.warning(
                    "corporate_actions_sync_unavailable symbol=%s providers=%s",
                    instrument.symbol,
                    {key: value.value for key, value in reconciled.provider_states.items()},
                )
                continue
            try:
                for action in reconciled.actions:
                    await _persist_action(conn, instrument.instrument_id, action)
                    persisted += 1
            except Exception:  # noqa: BLE001 - one symbol must not block the batch
                failed += 1
                logger.exception(
                    "corporate_actions_sync_persist_failed symbol=%s",
                    instrument.symbol,
                )

    logger.info(
        "corporate_actions_sync_complete scanned=%d persisted=%d unavailable=%d failed=%d",
        len(instruments),
        persisted,
        unavailable,
        failed,
    )
    return CorporateActionSyncStats(len(instruments), persisted, unavailable, failed)


async def _load_held_instruments(conn) -> list[HeldInstrument]:
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
                      snapshot.instrument_id, snapshot.as_of DESC
        )
        SELECT DISTINCT instrument.id AS instrument_id,
               alias.alias_value AS symbol,
               instrument.asset_type
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
           AND instrument.asset_type = ANY($1::TEXT[])
           AND instrument.lifecycle_status = 'ACTIVE'
         ORDER BY symbol
        """,
        sorted(_SUPPORTED_ASSET_TYPES),
    )
    return [
        HeldInstrument(
            instrument_id=str(row["instrument_id"]),
            symbol=str(row["symbol"]).strip().zfill(6),
            asset_type=str(row["asset_type"]),
        )
        for row in rows
    ]


def _for_symbol(result: ProviderResult, symbol: str) -> ProviderResult:
    return ProviderResult(
        provider=result.provider,
        state=result.state,
        actions=tuple(action for action in result.actions if action.symbol == symbol),
        error=result.error,
    )


async def _persist_action(conn, instrument_id: str, action: CorporateAction) -> None:
    await conn.fetchval(
        """
        SELECT market.upsert_corporate_action_sync(
            $1::uuid, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10::text[]
        )
        """,
        instrument_id,
        action.action_type,
        action.ex_date,
        action.record_date,
        action.pay_date,
        json.dumps(dict(action.params), ensure_ascii=False, sort_keys=True),
        action.source_event_id,
        action.source_ref,
        action.quality_status,
        list(action.verified_by),
    )


async def _default_akshare_fetcher(symbols: tuple[str, ...]) -> ProviderResult:
    return await fetch_akshare_actions(symbols)


async def _default_baostock_fetcher(
    symbols: tuple[str, ...], as_of: date
) -> ProviderResult:
    return await fetch_baostock_actions(symbols, as_of=as_of)


async def _default_fund_dividend_fetcher(
    symbols: tuple[str, ...], as_of: date
) -> ProviderResult:
    return await fetch_akshare_fund_dividend_actions(symbols, as_of=as_of)
