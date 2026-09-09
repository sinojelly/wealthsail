"""TianTian Fund subscription/redemption status adapter.

The adapter is deliberately database-free.  It turns the public structured
status list into a small, conservative fact set for the global Worker; unknown
provider wording is omitted rather than guessed as an open trading status.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_SOURCE_REF = "akshare/fund_purchase_em"
_FETCH_TIMEOUT_SECONDS = 45
_REQUIRED_COLUMNS = frozenset(
    {"基金代码", "基金简称", "申购状态", "赎回状态", "下一开放日"}
)
_NULL_TEXT = frozenset({"", "-", "--", "nan", "none", "nat", "null"})

logger = logging.getLogger("wealthsail.fund_subscription_status")


@dataclass(frozen=True, slots=True)
class FundSubscriptionStatus:
    """One fully recognizable fund subscription/redemption status snapshot."""

    symbol: str
    purchase_status: str
    redemption_status: str
    source_ref: str
    metadata: Mapping[str, str | None]


async def fetch_fund_subscription_status(
    symbols: Sequence[str], *, provider: Any | None = None
) -> dict[str, FundSubscriptionStatus]:
    """Fetch and filter the public TianTian Fund status list once.

    The source lists the whole market.  Only requested six-digit fund codes
    with both recognized states are returned, preventing an ambiguous source
    response from overwriting an existing verified profile.
    """

    requested = tuple(
        dict.fromkeys(
            normalized
            for symbol in symbols
            if (normalized := _normalize_symbol(symbol)) is not None
        )
    )
    if not requested:
        return {}
    try:
        if provider is None:
            import akshare as provider  # noqa: PLC0415

        frame = await asyncio.wait_for(
            asyncio.to_thread(provider.fund_purchase_em),
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a provider outage is non-fatal
        logger.warning(
            "fund_subscription_status_fetch_failed error_class=%s",
            type(exc).__name__,
        )
        return {}
    return _normalize_purchase_rows(frame, requested_symbols=requested)


def _normalize_purchase_rows(
    frame: Any, *, requested_symbols: Sequence[str]
) -> dict[str, FundSubscriptionStatus]:
    """Keep only complete, conservatively mapped provider records."""

    if frame is None or getattr(frame, "empty", True):
        return {}
    columns = {str(column) for column in getattr(frame, "columns", ())}
    if not _REQUIRED_COLUMNS <= columns or not hasattr(frame, "iterrows"):
        logger.warning(
            "fund_subscription_status_columns_changed columns=%s",
            sorted(columns),
        )
        return {}

    wanted = set(requested_symbols)
    statuses: dict[str, FundSubscriptionStatus] = {}
    for _, row in frame.iterrows():
        symbol = _normalize_symbol(_row_text(row, "基金代码"))
        if symbol is None or symbol not in wanted:
            continue
        purchase_raw = _row_text(row, "申购状态")
        redemption_raw = _row_text(row, "赎回状态")
        purchase_status = _normalize_status(purchase_raw)
        redemption_status = _normalize_status(redemption_raw)
        if purchase_status is None or redemption_status is None:
            continue
        statuses[symbol] = FundSubscriptionStatus(
            symbol=symbol,
            purchase_status=purchase_status,
            redemption_status=redemption_status,
            source_ref=_SOURCE_REF,
            metadata={
                "fundName": _row_text(row, "基金简称"),
                "purchaseStatusRaw": purchase_raw,
                "redemptionStatusRaw": redemption_raw,
                "nextOpenDate": _row_text(row, "下一开放日"),
            },
        )
    return statuses


def _normalize_symbol(value: object) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    if not re.fullmatch(r"\d{1,6}", text):
        return None
    return text.zfill(6)


def _normalize_status(value: str | None) -> str | None:
    if value is None:
        return None
    if "暂停" in value or "限制" in value:
        return "SUSPENDED"
    if "开放" in value or "正常" in value:
        return "OPEN"
    return None


def _row_text(row: Any, column: str) -> str | None:
    try:
        return _clean_text(row[column])
    except (KeyError, TypeError):
        return None


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _NULL_TEXT else text
