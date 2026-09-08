"""Provider-neutral corporate-action reconciliation.

This module deliberately contains no database access.  It converts individual
provider outcomes into a small, auditable result that the Worker can persist
through the database's controlled write function.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Any, Mapping, Sequence

from backend.integrations.baostock_adapter import BaostockDividendAdapter


AKSHARE_CORPORATE_ACTIONS = "AKSHARE_CORPORATE_ACTIONS"
BAOSTOCK_CORPORATE_ACTIONS = "BAOSTOCK_CORPORATE_ACTIONS"


class ProviderState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    action_type: str
    ex_date: date
    record_date: date | None
    pay_date: date | None
    params: Mapping[str, str]
    source_ref: str | None
    quality_status: str = "FRESH"
    verified_by: tuple[str, ...] = ()

    @property
    def source_event_id(self) -> str:
        return f"auto-corporate-actions:CN:{self.symbol}:{self.ex_date.isoformat()}"


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    state: ProviderState
    actions: tuple[CorporateAction, ...] = ()
    error: str | None = None

    @classmethod
    def available(
        cls, provider: str, actions: Sequence[CorporateAction]
    ) -> "ProviderResult":
        return cls(provider=provider, state=ProviderState.AVAILABLE, actions=tuple(actions))

    @classmethod
    def unavailable(cls, provider: str, error: str) -> "ProviderResult":
        return cls(provider=provider, state=ProviderState.UNAVAILABLE, error=error)

    @classmethod
    def unsupported(cls, provider: str) -> "ProviderResult":
        return cls(provider=provider, state=ProviderState.UNSUPPORTED)


@dataclass(frozen=True)
class SyncResult:
    symbol: str
    quality_status: str
    actions: tuple[CorporateAction, ...]
    provider_states: Mapping[str, ProviderState]


def reconcile_provider_results(
    symbol: str, provider_results: Sequence[ProviderResult]
) -> SyncResult:
    """Merge corroborated provider records without inventing missing events."""

    provider_states = {result.provider: result.state for result in provider_results}
    applicable = [
        result for result in provider_results if result.state != ProviderState.UNSUPPORTED
    ]
    if applicable and all(result.state == ProviderState.UNAVAILABLE for result in applicable):
        return SyncResult(
            symbol=symbol,
            quality_status="UNAVAILABLE",
            actions=(),
            provider_states=provider_states,
        )

    grouped: dict[tuple[str, date], list[tuple[str, CorporateAction]]] = {}
    for result in provider_results:
        if result.state != ProviderState.AVAILABLE:
            continue
        for action in result.actions:
            if action.symbol != symbol:
                continue
            grouped.setdefault((action.symbol, action.ex_date), []).append(
                (result.provider, action)
            )

    resolved: list[CorporateAction] = []
    has_conflict = False
    for _key, corroborated in sorted(grouped.items(), key=lambda item: item[0]):
        action_types = {action.action_type for _, action in corroborated}
        quality_status = "CONFLICT" if len(action_types) > 1 else "FRESH"
        has_conflict = has_conflict or quality_status == "CONFLICT"
        primary = corroborated[0][1]
        verified_by = tuple(sorted({provider for provider, _ in corroborated}))
        resolved.append(
            replace(
                primary,
                quality_status=quality_status,
                verified_by=verified_by,
            )
        )

    return SyncResult(
        symbol=symbol,
        quality_status="CONFLICT" if has_conflict else "FRESH",
        actions=tuple(resolved),
        provider_states=provider_states,
    )


async def fetch_akshare_actions(
    symbols: Sequence[str], *, provider: Any | None = None
) -> ProviderResult:
    """Fetch one market-wide AkShare dividend table and retain requested symbols."""

    wanted = {symbol.strip().zfill(6) for symbol in symbols}
    if not wanted:
        return ProviderResult.available(AKSHARE_CORPORATE_ACTIONS, ())
    try:
        if provider is None:
            import akshare as provider  # noqa: PLC0415

        rows, source_ref = await asyncio.to_thread(_akshare_dividend_rows, provider)
        actions = tuple(
            action
            for row in rows
            if (action := _normalize_akshare_action(row, source_ref)) is not None
            and action.symbol in wanted
        )
        return ProviderResult.available(AKSHARE_CORPORATE_ACTIONS, actions)
    except Exception as exc:  # noqa: BLE001 - provider faults must degrade safely
        return ProviderResult.unavailable(AKSHARE_CORPORATE_ACTIONS, type(exc).__name__)


async def fetch_baostock_actions(
    symbols: Sequence[str], *, as_of: date, provider: Any | None = None
) -> ProviderResult:
    """Fetch dividend records for supported symbols in one baostock session."""

    wanted = tuple(dict.fromkeys(symbol.strip().zfill(6) for symbol in symbols))
    if not wanted:
        return ProviderResult.available(BAOSTOCK_CORPORATE_ACTIONS, ())
    try:
        actions = await asyncio.to_thread(
            _fetch_baostock_actions_sync,
            wanted,
            (as_of.year, as_of.year + 1),
            provider,
        )
        return ProviderResult.available(BAOSTOCK_CORPORATE_ACTIONS, actions)
    except Exception as exc:  # noqa: BLE001 - provider faults must degrade safely
        return ProviderResult.unavailable(BAOSTOCK_CORPORATE_ACTIONS, type(exc).__name__)


def _akshare_dividend_rows(provider: Any) -> tuple[list[Any], str]:
    for function_name in ("stock_dividend", "stock_dividend_cninfo"):
        function = getattr(provider, function_name, None)
        if function is None:
            continue
        frame = function()
        if frame is None or getattr(frame, "empty", True):
            return [], f"akshare/{function_name}"
        return [row for _, row in frame.iterrows()], f"akshare/{function_name}"
    raise RuntimeError("akshare has no supported dividend endpoint")


def _normalize_akshare_action(row: Any, source_ref: str) -> CorporateAction | None:
    columns = _row_columns(row)
    symbol = _first_text(row, columns, ("股票代码", "代码"))
    ex_date = _first_date(row, columns, ("除权除息日", "除息日", "除权日"))
    if symbol is None or ex_date is None:
        return None
    cash = _first_text(row, columns, ("每股派息", "每股分红", "派息"))
    return CorporateAction(
        symbol=symbol.zfill(6),
        action_type="CASH_DIVIDEND",
        ex_date=ex_date,
        record_date=_first_date(row, columns, ("股权登记日", "登记日")),
        pay_date=_first_date(row, columns, ("派息日", "红利发放日", "股息发放日")),
        params={"cashPerShare": cash} if cash else {},
        source_ref=source_ref,
    )


def _fetch_baostock_actions_sync(
    symbols: Sequence[str], years: Sequence[int], provider: Any | None
) -> tuple[CorporateAction, ...]:
    deduplicated: dict[tuple[str, date], CorporateAction] = {}
    with BaostockDividendAdapter(provider=provider) as adapter:
        for symbol in symbols:
            for dividend in adapter.fetch_dividends(symbol, years=years):
                deduplicated[(dividend.symbol, dividend.ex_date)] = CorporateAction(
                    symbol=dividend.symbol,
                    action_type="CASH_DIVIDEND",
                    ex_date=dividend.ex_date,
                    record_date=dividend.record_date,
                    pay_date=dividend.pay_date,
                    params=(
                        {"cashPerShare": dividend.cash_per_share}
                        if dividend.cash_per_share
                        else {}
                    ),
                    source_ref="baostock/query_dividend_data",
                )
    return tuple(deduplicated.values())


def _row_columns(row: Any) -> set[str]:
    if hasattr(row, "keys"):
        return {str(column) for column in row.keys()}
    return set()


def _first_text(row: Any, columns: set[str], names: Sequence[str]) -> str | None:
    for name in names:
        if name not in columns:
            continue
        value = str(row[name] or "").strip()
        if value and value.lower() not in {"nan", "none", "nat"}:
            return value
    return None


def _first_date(row: Any, columns: set[str], names: Sequence[str]) -> date | None:
    value = _first_text(row, columns, names)
    if value is None:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
