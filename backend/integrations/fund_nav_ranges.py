"""AkShare accumulated-NAV ranges for the fund screener cache.

The values in this module are source facts, not investment recommendations.
It deliberately uses accumulated NAV so distributions cannot create an
artificial low point.  Provider failures return an explicit unavailable fact;
callers must persist it without retaining prior numbers as if they were fresh.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable


logger = logging.getLogger("wealthsail.fund_nav_ranges")

_PERCENT_QUANTUM = Decimal("0.0001")
_DATE_COLUMNS = ("净值日期", "日期")
_ACCUMULATED_NAV_COLUMNS = ("累计净值", "累计净值走势")


@dataclass(frozen=True, slots=True)
class FundNavRange:
    """One auditable accumulated-NAV range, or an explicit unavailable row."""

    fund_code: str
    fund_name: str
    fund_type: str
    latest_nav: Decimal | None
    latest_nav_date: date | None
    highest_nav: Decimal | None
    highest_nav_date: date | None
    lowest_nav: Decimal | None
    lowest_nav_date: date | None
    drawdown_from_high: Decimal | None
    rise_from_low: Decimal | None
    current_percentile: Decimal | None
    inception_date: date | None
    data_source: str


def build_fund_nav_range(
    *,
    fund_code: str,
    fund_name: str,
    fund_type: str,
    history: Iterable[tuple[date, Decimal]],
) -> FundNavRange:
    """Calculate full-history extrema from valid accumulated-NAV points."""

    _validate_identity(fund_code, fund_name, fund_type)
    values_by_date: dict[date, Decimal] = {}
    for observed_on, nav in history:
        if not isinstance(observed_on, date) or not isinstance(nav, Decimal):
            continue
        if not nav.is_finite() or nav <= 0:
            continue
        values_by_date[observed_on] = nav
    if not values_by_date:
        raise ValueError("accumulated NAV history contains no usable values")

    points = sorted(values_by_date.items())
    inception_date, _ = points[0]
    latest_nav_date, latest_nav = points[-1]
    highest_nav = max(nav for _, nav in points)
    lowest_nav = min(nav for _, nav in points)
    highest_nav_date = max(day for day, nav in points if nav == highest_nav)
    lowest_nav_date = max(day for day, nav in points if nav == lowest_nav)

    return FundNavRange(
        fund_code=fund_code,
        fund_name=fund_name,
        fund_type=fund_type,
        latest_nav=latest_nav,
        latest_nav_date=latest_nav_date,
        highest_nav=highest_nav,
        highest_nav_date=highest_nav_date,
        lowest_nav=lowest_nav,
        lowest_nav_date=lowest_nav_date,
        drawdown_from_high=_percent(highest_nav - latest_nav, highest_nav),
        rise_from_low=_percent(latest_nav - lowest_nav, lowest_nav),
        current_percentile=_percent(latest_nav - lowest_nav, highest_nav - lowest_nav),
        inception_date=inception_date,
        data_source="AKSHARE",
    )


async def fetch_fund_nav_range(
    fund_code: str,
    *,
    fund_name: str,
    fund_type: str,
    provider: Any | None = None,
) -> FundNavRange:
    """Fetch accumulated NAV from AkShare, failing closed on any bad response."""

    _validate_identity(fund_code, fund_name, fund_type)
    try:
        if provider is None:
            import akshare as provider  # noqa: PLC0415

        frame = await asyncio.to_thread(
            provider.fund_open_fund_info_em,
            symbol=fund_code,
            indicator="累计净值走势",
            period="成立来",
        )
        return build_fund_nav_range(
            fund_code=fund_code,
            fund_name=fund_name,
            fund_type=fund_type,
            history=_history_from_frame(frame),
        )
    except Exception as exc:  # noqa: BLE001 -- an outage must remain explicit
        logger.warning(
            "fund_nav_range_fetch_failed fund_code=%s error_class=%s",
            fund_code,
            type(exc).__name__,
        )
        return _unavailable(fund_code, fund_name, fund_type)


async def fetch_fund_identity(
    fund_code: str, *, provider: Any | None = None
) -> tuple[str, str]:
    """Resolve the canonical name for a requested Chinese mutual-fund code.

    AkShare exposes this lightweight identity response through its Xueqiu
    adapter.  It is used only in the Worker, before fetching accumulated NAV;
    failures propagate so the durable task retries rather than writing a
    fabricated name to the shared reference cache.
    """

    if len(fund_code) != 6 or not fund_code.isdigit():
        raise ValueError("fund code must contain exactly six digits")
    if provider is None:
        import akshare as provider  # noqa: PLC0415
    frame = await asyncio.to_thread(
        provider.fund_individual_basic_info_xq, symbol=fund_code
    )
    name = _identity_name_from_frame(frame)
    if not name:
        raise RuntimeError("AkShare fund identity response has no fund name")
    return (name, "OFF_EXCHANGE_FUND")


def _history_from_frame(frame: Any) -> list[tuple[date, Decimal]]:
    if frame is None or getattr(frame, "empty", True) or not hasattr(frame, "iterrows"):
        raise RuntimeError("AkShare accumulated NAV response is empty or malformed")
    columns = {str(column) for column in getattr(frame, "columns", ())}
    date_column = next((column for column in _DATE_COLUMNS if column in columns), None)
    nav_column = next(
        (column for column in _ACCUMULATED_NAV_COLUMNS if column in columns), None
    )
    if date_column is None or nav_column is None:
        raise RuntimeError("AkShare accumulated NAV response columns changed")

    points: list[tuple[date, Decimal]] = []
    for _, row in frame.iterrows():
        observed_on = _parse_date(_row_value(row, date_column))
        nav = _parse_decimal(_row_value(row, nav_column))
        if observed_on is not None and nav is not None and nav > 0:
            points.append((observed_on, nav))
    if not points:
        raise RuntimeError("AkShare accumulated NAV response has no usable rows")
    return points


def _identity_name_from_frame(frame: Any) -> str | None:
    """Read AkShare's transposed ``item``/``value`` fund metadata response."""

    if frame is None or getattr(frame, "empty", True) or not hasattr(frame, "iterrows"):
        return None
    for _, row in frame.iterrows():
        item = str(_row_value(row, "item") or "").strip()
        if item == "基金名称":
            name = str(_row_value(row, "value") or "").strip()
            return name or None
    return None


def _unavailable(fund_code: str, fund_name: str, fund_type: str) -> FundNavRange:
    return FundNavRange(
        fund_code=fund_code,
        fund_name=fund_name,
        fund_type=fund_type,
        latest_nav=None,
        latest_nav_date=None,
        highest_nav=None,
        highest_nav_date=None,
        lowest_nav=None,
        lowest_nav_date=None,
        drawdown_from_high=None,
        rise_from_low=None,
        current_percentile=None,
        inception_date=None,
        data_source="UNAVAILABLE",
    )


def _validate_identity(fund_code: str, fund_name: str, fund_type: str) -> None:
    if len(fund_code) != 6 or not fund_code.isdigit():
        raise ValueError("fund code must contain exactly six digits")
    if not fund_name.strip() or not fund_type.strip():
        raise ValueError("fund name and type are required")


def _row_value(row: Any, column: str) -> Any:
    try:
        return row[column]
    except (KeyError, TypeError):
        return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _percent(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    return ((numerator / denominator) * Decimal("100")).quantize(
        _PERCENT_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
