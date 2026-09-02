"""P2: corporate actions (AkShare) + subscription/redemption status (cutoff_rules).

External providers (AkShare, the global snapshot) are mocked so the suite runs
offline and never hits the network. The contract stays fail-closed: any outage
degrades to empty data (rendered as 暂无), never to fabricated numbers.
"""

from __future__ import annotations

import types
import unittest.mock as mock

import pytest

from backend.integrations import market_data
from backend.reports import service


class _FakeDF:
    """Minimal pandas-less stand-in: supports empty/columns/iterrows/subscript."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.columns = list(rows[0].keys()) if rows else []
        self.empty = len(rows) == 0

    def iterrows(self):
        for i, row in enumerate(self._rows):
            yield i, row


@pytest.fixture(autouse=True)
def _clear_caches():
    market_data._reset_corporate_actions_cache()
    yield
    market_data._reset_corporate_actions_cache()


# --------------------------------------------------------------------------- #
# market_data layer: AkShare corporate-action normalization + filtering
# --------------------------------------------------------------------------- #


def test_normalize_corporate_actions_maps_columns_and_iso_dates() -> None:
    df = _FakeDF(
        [
            {
                "股票代码": "600519",
                "股票简称": "贵州茅台",
                "除权除息日": "2026-06-18",
                "派息日": "2026-06-24",
            },
            {
                "股票代码": "000001",
                "股票简称": "平安银行",
                "除权除息日": "2026-07-10",
                "派息日": None,
            },
        ]
    )
    fake_ak = types.SimpleNamespace(stock_dividend=lambda: df)
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = market_data._normalize_corporate_actions()

    assert len(items) == 2
    by_symbol = {item["symbol"]: item for item in items}
    kweichow = by_symbol["600519"]
    assert kweichow["name"] == "贵州茅台"
    assert kweichow["actionType"] == "CASH_DIVIDEND"
    assert kweichow["exDate"] == "2026-06-18"
    assert kweichow["payDate"] == "2026-06-24"
    assert kweichow["source"] == "akshare/stock_dividend"
    # Missing pay date must not break normalization.
    assert by_symbol["000001"]["payDate"] is None


def test_normalize_corporate_actions_degrades_on_akshare_error() -> None:
    with mock.patch.object(
        market_data, "_ak_import", side_effect=RuntimeError("akshare down")
    ):
        items = market_data._normalize_corporate_actions()
    assert items == []


def test_normalize_corporate_actions_degrades_on_unknown_columns() -> None:
    fake_ak = types.SimpleNamespace(stock_dividend=lambda: _FakeDF([{"foo": 1}]))
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = market_data._normalize_corporate_actions()
    assert items == []


@pytest.mark.asyncio
async def test_fetch_corporate_actions_filters_by_held_symbols() -> None:
    all_items = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "actionType": "CASH_DIVIDEND",
            "exDate": "2026-06-18",
            "payDate": "2026-06-24",
            "source": "akshare",
        },
        {
            "symbol": "000001",
            "name": "平安银行",
            "actionType": "CASH_DIVIDEND",
            "exDate": "2026-07-10",
            "payDate": None,
            "source": "akshare",
        },
    ]
    with mock.patch.object(
        market_data, "_get_all_corporate_actions", return_value=all_items
    ):
        held = await market_data.fetch_corporate_actions(["600519"])
    assert [item["symbol"] for item in held] == ["600519"]


@pytest.mark.asyncio
async def test_fetch_corporate_actions_empty_when_no_symbols() -> None:
    assert await market_data.fetch_corporate_actions([]) == []


# --------------------------------------------------------------------------- #
# service layer: AkShare merge + subscription/redemption derivation
# --------------------------------------------------------------------------- #


class _FakeConn:
    """Stand-in for an asyncpg connection: only the methods build_report_context
    touches directly (policy lookup) need to be async-ready."""

    def __init__(self) -> None:
        self.fetchrow = mock.AsyncMock(return_value=None)
        self.fetch = mock.AsyncMock(return_value=[])


def _stub_context_deps(monkeypatch, *, ak_items, db_actions, cutoff_rules):
    """Wire build_report_context's I/O to deterministic fakes."""
    conn = _FakeConn()
    monkeypatch.setattr(
        service, "get_summary", mock.AsyncMock(return_value={"totalAsset": "100"})
    )
    monkeypatch.setattr(
        service,
        "get_positions",
        mock.AsyncMock(
            return_value=[
                {
                    "instrumentId": "inst-kweichow",
                    "symbol": "600519.SH",
                    "name": "贵州茅台",
                    "type": "A股",
                    "quantity": "100",
                },
                {
                    "instrumentId": "inst-pingan",
                    "symbol": "000001",
                    "name": "平安银行",
                    "type": "A股",
                    "quantity": "200",
                },
                {
                    "instrumentId": "inst-fund",
                    "symbol": "FUND001",
                    "name": "某场外基金",
                    "type": "场外基金",
                    "quantity": "300",
                },
            ]
        ),
    )
    monkeypatch.setattr(
        service.repository,
        "get_pre_market_corporate_actions",
        mock.AsyncMock(return_value=db_actions),
    )
    monkeypatch.setattr(
        service.repository,
        "get_pre_market_cutoff_rules",
        mock.AsyncMock(return_value=cutoff_rules),
    )
    monkeypatch.setattr(
        service.market_data,
        "get_pre_market_global_snapshot",
        mock.AsyncMock(
            return_value={"usIndices": [], "a50Future": None, "economicEvents": []}
        ),
    )
    monkeypatch.setattr(
        service.market_service,
        "get_market_sentiment",
        mock.AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        service.market_data,
        "_get_all_corporate_actions",
        mock.AsyncMock(return_value=ak_items),
    )
    return conn


@pytest.mark.asyncio
async def test_akshare_actions_merged_with_instrument_id(monkeypatch) -> None:
    ak_items = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "actionType": "CASH_DIVIDEND",
            "exDate": "2026-06-18",
            "payDate": "2026-06-24",
            "source": "akshare/stock_dividend",
        },
    ]
    conn = _stub_context_deps(
        monkeypatch, ak_items=ak_items, db_actions=[], cutoff_rules=[]
    )

    ctx = await service.build_report_context(conn, "tenant-1", "PRE_MARKET")
    actions = ctx["preMarket"]["corporateActions"]

    assert len(actions) == 1
    merged = actions[0]
    assert merged["instrumentId"] == "inst-kweichow"  # mapped from 600519.SH
    assert merged["symbol"] == "600519"
    assert merged["exDate"] == "2026-06-18"
    assert merged["payDate"] == "2026-06-24"
    assert merged["source"] == "akshare/stock_dividend"


@pytest.mark.asyncio
async def test_akshare_dedup_against_db_corporate_actions(monkeypatch) -> None:
    ak_items = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "actionType": "CASH_DIVIDEND",
            "exDate": "2026-06-18",
            "payDate": "2026-06-24",
            "source": "akshare/stock_dividend",
        },
    ]
    db_actions = [
        {
            "instrumentId": "inst-kweichow",
            "symbol": "600519",
            "name": "贵州茅台",
            "actionType": "CASH_DIVIDEND",
            "exDate": "2026-06-18",
            "recordDate": None,
            "payDate": "2026-06-24",
            "params": {},
            "revision": 1,
        },
    ]
    conn = _stub_context_deps(
        monkeypatch, ak_items=ak_items, db_actions=db_actions, cutoff_rules=[]
    )

    ctx = await service.build_report_context(conn, "tenant-1", "PRE_MARKET")
    actions = ctx["preMarket"]["corporateActions"]
    # DB record already covers this (instrumentId, actionType, exDate): no dup.
    assert len(actions) == 1
    assert actions[0]["revision"] == 1


@pytest.mark.asyncio
async def test_subscription_changes_derived_from_cutoff_rules(monkeypatch) -> None:
    cutoff_rules = [
        {
            "instrumentId": "inst-fund",
            "symbol": "FUND001",
            "name": "某场外基金",
            "salesChannel": "DEFAULT",
            "cutoffTime": "14:30:00",
            "timezone": "Asia/Shanghai",
        },
    ]
    conn = _stub_context_deps(
        monkeypatch, ak_items=[], db_actions=[], cutoff_rules=cutoff_rules
    )

    ctx = await service.build_report_context(conn, "tenant-1", "PRE_MARKET")
    subs = ctx["preMarket"]["subscriptionRedemptionChanges"]

    assert len(subs) == 1
    change = subs[0]
    assert change["instrumentId"] == "inst-fund"
    assert change["summary"] == "默认渠道截止14:30（Asia/Shanghai）"
    assert change["title"] == "某场外基金 申赎截止14:30"


@pytest.mark.asyncio
async def test_subscription_changes_empty_when_no_cutoff(monkeypatch) -> None:
    conn = _stub_context_deps(monkeypatch, ak_items=[], db_actions=[], cutoff_rules=[])

    ctx = await service.build_report_context(conn, "tenant-1", "PRE_MARKET")
    assert ctx["preMarket"]["subscriptionRedemptionChanges"] == []


# --------------------------------------------------------------------------- #
# helper units
# --------------------------------------------------------------------------- #


def test_to_six_digit_handles_suffix_and_plain() -> None:
    assert service._to_six_digit("600519.SH") == "600519"
    assert service._to_six_digit("600519") == "600519"
    assert service._to_six_digit("000001.XSHE") == "000001"
    assert service._to_six_digit("ABC") == ""


def test_to_iso_date_handles_slash_and_time() -> None:
    assert service._to_iso_date("2026/06/18") == "2026-06-18"
    assert service._to_iso_date("2026-06-18 00:00:00") == "2026-06-18"
    assert service._to_iso_date("") == ""
    assert service._to_iso_date("not-a-date") == ""


def test_derive_subscription_change_labels_channel() -> None:
    change = service._derive_subscription_change(
        {
            "instrumentId": "i1",
            "symbol": "F1",
            "name": "基金A",
            "salesChannel": "TIANTIAN",
            "cutoffTime": "15:00:00",
            "timezone": "Asia/Shanghai",
        }
    )
    assert change["summary"] == "TIANTIAN截止15:00（Asia/Shanghai）"
    assert change["title"] == "基金A 申赎截止15:00"


@pytest.mark.asyncio
async def test_fund_profiles_flow_into_pre_market_context(monkeypatch) -> None:
    """reference.fund_profiles 的读取结果必须进入 preMarket.fundProfiles。"""
    conn = _stub_context_deps(monkeypatch, ak_items=[], db_actions=[], cutoff_rules=[])
    profiles = [
        {
            "instrumentId": "inst-fund",
            "symbol": "FUND001",
            "name": "某场外基金",
            "purchaseStatus": "OPEN",
            "redemptionStatus": "SUSPENDED",
        }
    ]
    monkeypatch.setattr(
        service.repository,
        "get_pre_market_fund_profiles",
        mock.AsyncMock(return_value=profiles),
    )

    ctx = await service.build_report_context(conn, "tenant-1", "PRE_MARKET")

    assert ctx["preMarket"]["fundProfiles"] == profiles
    assert ctx["preMarket"]["fundProfiles"][0]["redemptionStatus"] == "SUSPENDED"
