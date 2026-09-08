"""Unit tests for the isolated baostock corporate-action adapter."""

from __future__ import annotations

from dataclasses import dataclass

from backend.integrations.baostock_adapter import BaostockDividendAdapter, to_baostock_code


def test_to_baostock_code_maps_supported_a_share_and_exchange_fund_symbols() -> None:
    assert to_baostock_code("600519") == "sh.600519"
    assert to_baostock_code("000001") == "sz.000001"
    assert to_baostock_code("510300") == "sh.510300"
    assert to_baostock_code("ABC") is None
    assert to_baostock_code("110022") is None


@dataclass
class _FakeLoginResult:
    error_code: str = "0"
    error_msg: str = ""


class _FakeQueryResult:
    error_code = "0"
    error_msg = ""
    fields = [
        "code",
        "dividOperateDate",
        "dividRegistDate",
        "dividPayDate",
        "dividCashPsBeforeTax",
    ]

    def __init__(self, rows: list[list[str]]) -> None:
        self._rows = rows
        self._index = -1

    def next(self) -> bool:
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self) -> list[str]:
        return self._rows[self._index]


class _FakeBaostock:
    def __init__(self) -> None:
        self.login_calls = 0
        self.logout_calls = 0
        self.queries: list[tuple[str, str, str]] = []

    def login(self) -> _FakeLoginResult:
        self.login_calls += 1
        return _FakeLoginResult()

    def logout(self) -> None:
        self.logout_calls += 1

    def query_dividend_data(
        self, code: str, year: str, year_type: str
    ) -> _FakeQueryResult:
        self.queries.append((code, year, year_type))
        if year == "2026":
            return _FakeQueryResult(
                [["sh.600519", "2026-09-08", "2026-09-05", "2026-09-10", "23.957"]]
            )
        return _FakeQueryResult([])


def test_batch_adapter_logs_in_once_and_normalizes_dividend_dates() -> None:
    provider = _FakeBaostock()

    with BaostockDividendAdapter(provider=provider) as adapter:
        records = adapter.fetch_dividends("600519", years=(2026, 2027))

    assert provider.login_calls == 1
    assert provider.logout_calls == 1
    assert provider.queries == [
        ("sh.600519", "2026", "operate"),
        ("sh.600519", "2027", "operate"),
    ]
    assert len(records) == 1
    assert records[0].symbol == "600519"
    assert records[0].ex_date.isoformat() == "2026-09-08"
    assert records[0].record_date.isoformat() == "2026-09-05"
    assert records[0].pay_date.isoformat() == "2026-09-10"
    assert records[0].cash_per_share == "23.957"
