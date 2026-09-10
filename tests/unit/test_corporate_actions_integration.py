"""Tests for provider-neutral corporate-action reconciliation."""

from dataclasses import dataclass
from datetime import date

import pytest

from backend.integrations import corporate_actions
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
    columns = ["公告日期", "送股", "转增", "派息", "进度", "除权除息日", "股权登记日"]

    def iterrows(self):
        yield 0, {
            "公告日期": "2026-09-01",
            "送股": "0",
            "转增": "0",
            "派息": "23.957",
            "进度": "实施",
            "除权除息日": "2026-09-08",
            "股权登记日": "2026-09-05",
        }


class _FakeAkshare:
    def stock_fhps_detail_em(self, *, symbol: str) -> _FakeFrame:
        assert symbol == "600519"
        return _FakeFrame()


class _FakeHistoryDividendDetailFrame:
    """AkShare ``stock_fhps_detail_em`` 的逐股票事件形态。"""

    empty = False
    columns = [
        "现金分红-现金分红比例",
        "送转股份-送股比例",
        "送转股份-转股比例",
        "方案进度",
        "除权除息日",
        "股权登记日",
        "红股上市日",
    ]

    def iterrows(self):
        yield 0, {
            "现金分红-现金分红比例": "10.03",
            "送转股份-送股比例": "0",
            "送转股份-转股比例": "0",
            "方案进度": "实施分配",
            "除权除息日": "2026-09-10",
            "股权登记日": "2026-09-09",
            "红股上市日": "",
        }
        # 预案尚未落地，不能被当成今日除权除息事实写入报告。
        yield 1, {
            "现金分红-现金分红比例": "10.03",
            "送转股份-送股比例": "0",
            "送转股份-转股比例": "0",
            "方案进度": "预案",
            "除权除息日": "2026-09-11",
            "股权登记日": "2026-09-10",
            "红股上市日": "",
        }


class _FakeHistoryDividendDetailAkshare:
    def __init__(self) -> None:
        self.symbols: list[str] = []

    def stock_fhps_detail_em(
        self, *, symbol: str
    ) -> _FakeHistoryDividendDetailFrame:
        self.symbols.append(symbol)
        return _FakeHistoryDividendDetailFrame()


class _FakeStockFhpsDetailFrame:
    """AkShare ``stock_fhps_detail_em`` 的已实施分红送配记录。"""

    empty = False
    columns = [
        "报告期",
        "现金分红-现金分红比例",
        "现金分红-现金分红比例描述",
        "送转股份-送股比例",
        "送转股份-转股比例",
        "股权登记日",
        "除权除息日",
        "方案进度",
        "最新公告日期",
    ]

    def iterrows(self):
        yield 0, {
            "报告期": "2025-12-31",
            "现金分红-现金分红比例": "10.03",
            "现金分红-现金分红比例描述": "每10股派10.03元（含税）",
            "送转股份-送股比例": "0",
            "送转股份-转股比例": "1",
            "股权登记日": "2026-09-09",
            "除权除息日": "2026-09-10",
            "方案进度": "实施分配",
            "最新公告日期": "2026-09-03",
        }


class _FakeStockFhpsDetailAkshare:
    def stock_fhps_detail_em(self, *, symbol: str) -> _FakeStockFhpsDetailFrame:
        assert symbol == "600519"
        return _FakeStockFhpsDetailFrame()


@pytest.mark.asyncio
async def test_akshare_fetch_filters_requested_symbols_and_normalizes_dates() -> None:
    result = await fetch_akshare_actions(["600519"], provider=_FakeAkshare())

    assert result.state is ProviderState.AVAILABLE
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.symbol == "600519"
    assert action.ex_date == date(2026, 9, 8)
    assert action.record_date == date(2026, 9, 5)
    assert action.pay_date is None
    assert action.params == {"cashDividendRatio": "23.957"}


@pytest.mark.asyncio
async def test_akshare_fetch_uses_implemented_dividend_detail_rows_only() -> None:
    """已实施且有除权日的逐股票记录才可成为盘前事实。"""
    provider = _FakeHistoryDividendDetailAkshare()

    result = await fetch_akshare_actions(["600519"], provider=provider)

    assert provider.symbols == ["600519"]
    assert result.state is ProviderState.AVAILABLE
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.symbol == "600519"
    assert action.action_type == "CASH_DIVIDEND"
    assert action.ex_date == date(2026, 9, 10)
    assert action.record_date == date(2026, 9, 9)
    assert action.params == {"cashDividendRatio": "10.03"}
    assert action.source_ref == "akshare/stock_fhps_detail_em"


@pytest.mark.asyncio
async def test_akshare_fetch_uses_stock_fhps_detail_for_implemented_a_share_actions() -> None:
    """防止 A 股同步退回缺少送配字段的新旧详情接口。"""
    result = await fetch_akshare_actions(["600519"], provider=_FakeStockFhpsDetailAkshare())

    assert result.state is ProviderState.AVAILABLE
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.symbol == "600519"
    assert action.action_type == "CASH_DIVIDEND"
    assert action.ex_date == date(2026, 9, 10)
    assert action.record_date == date(2026, 9, 9)
    assert action.params == {
        "cashDividendRatio": "10.03",
        "cashDividendDescription": "每10股派10.03元（含税）",
        "transferShareRatio": "1",
    }
    assert action.source_ref == "akshare/stock_fhps_detail_em"


class _FakeFundDividendFrame:
    """AkShare ``fund_fh_em`` 的基金分红事实行。"""

    empty = False
    columns = ["基金代码", "基金简称", "权益登记日", "除息日期", "分红", "分红发放日"]

    def iterrows(self):
        yield 0, {
            "基金代码": "110022",
            "基金简称": "易方达消费行业",
            "权益登记日": "2026-09-10",
            "除息日期": "2026-09-11",
            "分红": "0.05",
            "分红发放日": "2026-09-14",
        }
        # 非持仓基金必须在 Provider 边界就被过滤，避免无关事实进入后续同步。
        yield 1, {
            "基金代码": "000001",
            "基金简称": "示例基金",
            "权益登记日": "2026-09-10",
            "除息日期": "2026-09-11",
            "分红": "0.01",
            "分红发放日": "2026-09-14",
        }


class _FakeFundDividendAkshare:
    def __init__(self) -> None:
        self.years: list[str] = []

    def fund_fh_em(
        self, *, year: str, typ: str = "", rank: str = "BZDM", sort: str = "asc", page: int = -1
    ) -> _FakeFundDividendFrame:
        assert typ == ""
        assert rank == "DJR"
        assert sort == "asc"
        assert page == -1
        self.years.append(year)
        return _FakeFundDividendFrame()


@pytest.mark.asyncio
async def test_akshare_fund_dividend_fetch_normalizes_held_fund_lifecycle_dates() -> None:
    """基金分红必须保留登记、除息和发放日期，且仅返回当前持仓基金。"""
    provider = _FakeFundDividendAkshare()
    fetch = getattr(corporate_actions, "fetch_akshare_fund_dividend_actions", None)
    assert callable(fetch)

    result = await fetch(
        ["110022"], as_of=date(2026, 9, 9), provider=provider
    )

    assert provider.years == ["2026"]
    assert result.state is ProviderState.AVAILABLE
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.symbol == "110022"
    assert action.action_type == "FUND_DIVIDEND"
    assert action.ex_date == date(2026, 9, 11)
    assert action.record_date == date(2026, 9, 10)
    assert action.pay_date == date(2026, 9, 14)
    assert action.params == {"cashPerUnit": "0.05"}
    assert action.source_ref == "akshare/fund_fh_em"
    assert action.source_event_id == "auto-corporate-actions:FUND:110022:2026-09-11"


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
