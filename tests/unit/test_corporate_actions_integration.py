"""Tests for provider-neutral corporate-action reconciliation."""

from dataclasses import dataclass
from datetime import date

import pytest

from backend.integrations.corporate_actions import (
    CorporateAction,
    ProviderResult,
    ProviderState,
    fetch_akshare_actions,
    fetch_baostock_actions,
    reconcile_provider_results,
)


def _action(
    provider: str, *, action_type: str = "CASH_DIVIDEND"
) -> CorporateAction:
    return CorporateAction(
        symbol="600519",
        action_type=action_type,
        ex_date=date(2026, 9, 8),
        record_date=date(2026, 9, 5),
        pay_date=date(2026, 9, 10),
        params={"cashPerShare": "23.957"},
        source_ref=f"{provider}/600519",
    )


def test_reconcile_merges_same_symbol_and_ex_date_with_all_verifiers() -> None:
    result = reconcile_provider_results(
        "600519",
        [
            ProviderResult.available("AKSHARE_CORPORATE_ACTIONS", [_action("ak")]),
            ProviderResult.available("BAOSTOCK_CORPORATE_ACTIONS", [_action("bs")]),
        ],
    )

    assert result.quality_status == "FRESH"
    assert len(result.actions) == 1
    assert result.actions[0].verified_by == (
        "AKSHARE_CORPORATE_ACTIONS",
        "BAOSTOCK_CORPORATE_ACTIONS",
    )
    assert result.actions[0].source_event_id == "auto-corporate-actions:CN:600519:2026-09-08"


def test_reconcile_keeps_one_available_source_fresh() -> None:
    result = reconcile_provider_results(
        "600519",
        [
            ProviderResult.available("AKSHARE_CORPORATE_ACTIONS", [_action("ak")]),
            ProviderResult.unavailable("BAOSTOCK_CORPORATE_ACTIONS", "timeout"),
        ],
    )

    assert result.quality_status == "FRESH"
    assert result.actions[0].verified_by == ("AKSHARE_CORPORATE_ACTIONS",)


def test_reconcile_marks_conflicting_action_types_for_the_same_ex_date() -> None:
    result = reconcile_provider_results(
        "600519",
        [
            ProviderResult.available("AKSHARE_CORPORATE_ACTIONS", [_action("ak")]),
            ProviderResult.available(
                "BAOSTOCK_CORPORATE_ACTIONS", [_action("bs", action_type="EX_RIGHTS")]
            ),
        ],
    )

    assert result.quality_status == "CONFLICT"
    assert result.actions[0].quality_status == "CONFLICT"


def test_reconcile_does_not_create_a_fake_action_when_all_applicable_sources_fail() -> None:
    result = reconcile_provider_results(
        "600519",
        [
            ProviderResult.unavailable("AKSHARE_CORPORATE_ACTIONS", "timeout"),
            ProviderResult.unavailable("BAOSTOCK_CORPORATE_ACTIONS", "login failed"),
        ],
    )

    assert result.quality_status == "UNAVAILABLE"
    assert result.actions == ()
    assert result.provider_states == {
        "AKSHARE_CORPORATE_ACTIONS": ProviderState.UNAVAILABLE,
        "BAOSTOCK_CORPORATE_ACTIONS": ProviderState.UNAVAILABLE,
    }


def test_unsupported_baostock_is_not_a_failure_for_exchange_fund() -> None:
    result = reconcile_provider_results(
        "510300",
        [
            ProviderResult.available("AKSHARE_CORPORATE_ACTIONS", []),
            ProviderResult.unsupported("BAOSTOCK_CORPORATE_ACTIONS"),
        ],
    )

    assert result.quality_status == "FRESH"
    assert result.actions == ()


class _FakeFrame:
    empty = False
    columns = ["股票代码", "除权除息日", "股权登记日", "派息日", "每股派息"]

    def iterrows(self):
        yield 0, {
            "股票代码": "600519",
            "除权除息日": "2026-09-08",
            "股权登记日": "2026-09-05",
            "派息日": "2026-09-10",
            "每股派息": "23.957",
        }
        yield 1, {
            "股票代码": "000001",
            "除权除息日": "2026-09-09",
            "股权登记日": "",
            "派息日": "",
            "每股派息": "0.3",
        }


class _FakeAkshare:
    def stock_dividend(self) -> _FakeFrame:
        return _FakeFrame()


@pytest.mark.asyncio
async def test_akshare_fetch_filters_requested_symbols_and_normalizes_dates() -> None:
    result = await fetch_akshare_actions(["600519"], provider=_FakeAkshare())

    assert result.state is ProviderState.AVAILABLE
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.symbol == "600519"
    assert action.ex_date == date(2026, 9, 8)
    assert action.record_date == date(2026, 9, 5)
    assert action.pay_date == date(2026, 9, 10)
    assert action.params == {"cashPerShare": "23.957"}


@dataclass
class _FakeLoginResult:
    error_code: str = "0"
    error_msg: str = ""


class _FakeDividendResult:
    error_code = "0"
    error_msg = ""
    fields = [
        "code",
        "dividOperateDate",
        "dividRegistDate",
        "dividPayDate",
        "dividCashPsBeforeTax",
    ]

    def __init__(self) -> None:
        self._read = False

    def next(self) -> bool:
        if self._read:
            return False
        self._read = True
        return True

    def get_row_data(self) -> list[str]:
        return ["sh.600519", "2026-09-08", "2026-09-05", "2026-09-10", "23.957"]


class _FakeBaostock:
    def login(self) -> _FakeLoginResult:
        return _FakeLoginResult()

    def logout(self) -> None:
        return None

    def query_dividend_data(
        self, code: str, year: str, year_type: str
    ) -> _FakeDividendResult:
        assert code == "sh.600519"
        assert year_type == "operate"
        return _FakeDividendResult()


@pytest.mark.asyncio
async def test_baostock_fetch_normalizes_dividends_for_the_requested_symbols() -> None:
    result = await fetch_baostock_actions(
        ["600519"], as_of=date(2026, 9, 1), provider=_FakeBaostock()
    )

    assert result.state is ProviderState.AVAILABLE
    assert result.actions[0].symbol == "600519"
    assert result.actions[0].action_type == "CASH_DIVIDEND"
    assert result.actions[0].params == {"cashPerShare": "23.957"}
