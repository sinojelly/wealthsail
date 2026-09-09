"""P2: corporate actions (AkShare) + subscription/redemption status (cutoff_rules).

External providers (AkShare, the global snapshot) are mocked so the suite runs
offline and never hits the network. The contract stays fail-closed: any outage
degrades to empty data (rendered as 暂无), never to fabricated numbers.
"""

from __future__ import annotations

import types
import unittest.mock as mock
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from backend.integrations import market_data
from backend.config import Settings
from backend.portfolio.scope import AssetScope
from backend.reports import service
from backend.reports.holding_trends import HoldingTrend


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


def _stub_context_deps(
    monkeypatch, *, ak_items, db_actions, cutoff_rules, watchlist=None
):
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
                    "price": "1680.00",
                },
                {
                    "instrumentId": "inst-pingan",
                    "symbol": "000001",
                    "name": "平安银行",
                    "type": "A股",
                    "quantity": "200",
                    "price": "12.50",
                },
                {
                    "instrumentId": "inst-fund",
                    "symbol": "FUND001",
                    "name": "某场外基金",
                    "type": "场外基金",
                    "quantity": "300",
                    "price": "1.40",
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
        "get_user_watchlist_context",
        mock.AsyncMock(return_value=watchlist or []),
    )
    monkeypatch.setattr(
        service.repository,
        "get_pre_market_cutoff_rules",
        mock.AsyncMock(return_value=cutoff_rules),
    )
    monkeypatch.setattr(
        service.repository,
        "get_pre_market_position_quotes",
        mock.AsyncMock(return_value={}),
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
    monkeypatch.setattr(
        service.market_data,
        "fetch_today_company_announcements",
        mock.AsyncMock(return_value=[]),
    )
    return conn


@pytest.mark.asyncio
async def test_pre_market_context_enriches_positions_with_aligned_quote(
    monkeypatch,
) -> None:
    """The personalized report receives only price-aligned persisted changes."""
    conn = _stub_context_deps(
        monkeypatch, ak_items=[], db_actions=[], cutoff_rules=[]
    )
    quote_as_of = datetime(
        2026, 9, 5, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    service.repository.get_pre_market_position_quotes.return_value = {
        "inst-kweichow": {
            "changePct": "0.84",
            "quality": "STALE",
            "sourceAsOf": quote_as_of,
        }
    }

    context = await service.build_report_context(
        conn, "tenant-1", "PRE_MARKET", audience_user_id="user-1"
    )

    position = context["positions"][0]
    assert position["changePct"] == "0.84"
    assert position["changeQuality"] == "STALE"
    assert position["changeSourceAsOf"] == quote_as_of
    called_prices = service.repository.get_pre_market_position_quotes.await_args.args[1]
    assert called_prices == {
        "inst-kweichow": "1680.00",
        "inst-pingan": "12.50",
        "inst-fund": "1.40",
    }


@pytest.mark.asyncio
async def test_context_reads_persisted_actions_for_holdings_and_watchlist_window(
    monkeypatch,
) -> None:
    db_actions = [
        {
            "instrumentId": "inst-kweichow",
            "symbol": "600519",
            "name": "贵州茅台",
            "actionType": "EX_RIGHTS",
            "actionDate": "2026-09-08",
            "exDate": "2026-09-08",
            "summary": "除权除息",
            "sourceEventId": "manual-csv:event-1",
            "revision": 1,
        }
    ]
    conn = _stub_context_deps(
        monkeypatch,
        ak_items=[],
        db_actions=db_actions,
        cutoff_rules=[],
        watchlist=[
            {
                "instrumentId": "inst-watch",
                "symbol": "300750",
                "name": "宁德时代",
                "type": "STOCK",
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "now_shanghai",
        lambda: datetime(2026, 9, 7, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    async def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("production reports must only use persisted actions")

    monkeypatch.setattr(service.market_data, "fetch_corporate_actions", provider_must_not_run)

    ctx = await service.build_report_context(
        conn, "tenant-1", "PRE_MARKET", audience_user_id="user-1"
    )
    actions = ctx["preMarket"]["corporateActions"]

    assert actions == db_actions
    call = service.repository.get_pre_market_corporate_actions.await_args
    assert set(call.args[1]) == {
        "inst-kweichow",
        "inst-pingan",
        "inst-fund",
        "inst-watch",
    }
    assert call.args[2].isoformat() == "2026-09-07"
    assert call.args[3].isoformat() == "2026-09-10"


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


@pytest.mark.asyncio
async def test_personalized_context_adds_trend_and_pnl_when_enabled(monkeypatch) -> None:
    conn = _stub_context_deps(monkeypatch, ak_items=[], db_actions=[], cutoff_rules=[])
    awaitable_trends = mock.AsyncMock(
        return_value={
            "inst-kweichow": HoldingTrend(
                display="📈 连涨2天 (+3.00%)",
                previous_direction="UP",
                streak_direction="UP",
                streak_days=2,
                streak_change_pct=Decimal("3"),
                history_start_date=None,
                history_end_date=None,
                history_field_code="CLOSE",
                quality="FRESH",
            )
        }
    )
    monkeypatch.setattr(service.repository, "resolve_instrument_trends", awaitable_trends)
    service.get_positions.return_value = [
        {
            "instrumentId": "inst-kweichow",
            "symbol": "600519",
            "name": "贵州茅台",
            "type": "A股",
            "quantity": "2",
            "avgCost": "100",
            "price": "717.28",
        }
    ]
    service.repository.get_pre_market_position_quotes.return_value = {
        "inst-kweichow": {
            "changePct": "1.0",
            "quality": "FRESH",
            "sourceAsOf": None,
        }
    }

    context = await service.build_report_context(
        conn,
        "tenant-1",
        "PRE_MARKET",
        audience_user_id="user-1",
        asset_scope=AssetScope(kind="MINE", owner_user_id="user-1"),
        settings=Settings(
            _env_file=None, pre_market_holdings_enrichments_enabled=True
        ),
    )

    assert context["positions"][0]["trendDisplay"] == "📈 连涨2天 (+3.00%)"
    assert context["positions"][0]["pnlAmount"] == "1234.56"
    assert context["preMarket"]["holdingsEnrichmentsEnabled"] is True
    awaitable_trends.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_context_never_resolves_personal_holding_trends(monkeypatch) -> None:
    conn = _stub_context_deps(monkeypatch, ak_items=[], db_actions=[], cutoff_rules=[])
    resolve_trends = mock.AsyncMock()
    monkeypatch.setattr(service.repository, "resolve_instrument_trends", resolve_trends)

    await service.build_report_context(
        conn,
        "tenant-1",
        "PRE_MARKET",
        settings=Settings(_env_file=None),
    )

    resolve_trends.assert_not_awaited()
