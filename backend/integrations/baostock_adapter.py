"""Small, testable boundary around baostock dividend queries.

The package is synchronous and session-based.  Keeping its login lifecycle in
this module prevents provider-specific result objects from leaking into Worker
jobs and guarantees one login/logout pair for each sync batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Protocol


class BaostockAdapterError(RuntimeError):
    """Raised when baostock cannot establish or complete a query."""


class _BaostockProvider(Protocol):
    def login(self) -> Any: ...

    def logout(self) -> Any: ...

    def query_dividend_data(self, code: str, year: str, year_type: str) -> Any: ...


@dataclass(frozen=True)
class BaostockDividend:
    """Provider-neutral dividend dates and cash amount for one symbol."""

    symbol: str
    ex_date: date
    record_date: date | None
    pay_date: date | None
    cash_per_share: str | None


def to_baostock_code(symbol: str) -> str | None:
    """Translate a six-digit mainland symbol to baostock's exchange form."""

    normalized = symbol.strip()
    if len(normalized) != 6 or not normalized.isascii() or not normalized.isdigit():
        return None
    if normalized[0] in {"5", "6", "9"}:
        return f"sh.{normalized}"
    if normalized[0] in {"0", "2", "3"}:
        return f"sz.{normalized}"
    return None


class BaostockDividendAdapter:
    """A synchronous batch session for baostock corporate-action data."""

    def __init__(self, provider: _BaostockProvider | None = None) -> None:
        self._provider = provider
        self._entered = False

    def __enter__(self) -> "BaostockDividendAdapter":
        if self._provider is None:
            try:
                import baostock as provider  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - packaging boundary
                raise BaostockAdapterError("baostock dependency is unavailable") from exc
            self._provider = provider

        result = self._provider.login()
        if str(getattr(result, "error_code", "-1")) != "0":
            raise BaostockAdapterError(
                f"baostock login failed: {getattr(result, 'error_msg', 'unknown error')}"
            )
        self._entered = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._entered and self._provider is not None:
            self._provider.logout()
            self._entered = False

    def fetch_dividends(
        self, symbol: str, *, years: Iterable[int]
    ) -> list[BaostockDividend]:
        """Read all valid dividend rows for a supported A-share/ETF symbol."""

        if not self._entered or self._provider is None:
            raise BaostockAdapterError("baostock session has not been started")
        code = to_baostock_code(symbol)
        if code is None:
            return []

        records: list[BaostockDividend] = []
        for year in years:
            result = self._provider.query_dividend_data(code, str(year), "operate")
            if str(getattr(result, "error_code", "-1")) != "0":
                raise BaostockAdapterError(
                    "baostock dividend query failed: "
                    f"{getattr(result, 'error_msg', 'unknown error')}"
                )
            records.extend(self._read_rows(symbol, result))
        return records

    @staticmethod
    def _read_rows(symbol: str, result: Any) -> list[BaostockDividend]:
        fields = {name: index for index, name in enumerate(result.fields)}
        required = {"dividOperateDate", "dividRegistDate", "dividPayDate"}
        if not required.issubset(fields):
            raise BaostockAdapterError("baostock dividend response has unexpected fields")

        records: list[BaostockDividend] = []
        while result.next():
            row = result.get_row_data()
            ex_date = _parse_date(row[fields["dividOperateDate"]])
            if ex_date is None:
                continue
            cash_per_share = _normalize_decimal(
                row[fields["dividCashPsBeforeTax"]]
                if "dividCashPsBeforeTax" in fields
                else ""
            )
            records.append(
                BaostockDividend(
                    symbol=symbol,
                    ex_date=ex_date,
                    record_date=_parse_date(row[fields["dividRegistDate"]]),
                    pay_date=_parse_date(row[fields["dividPayDate"]]),
                    cash_per_share=cash_per_share,
                )
            )
        return records


def _parse_date(value: str) -> date | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _normalize_decimal(value: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return format(Decimal(normalized).normalize(), "f")
    except InvalidOperation:
        return None
