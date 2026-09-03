"""08:35 personalized pre-market report: four branches, no model, own facts only."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.reports import pre_market_personalized, service

_HELD_ID = "11111111-1111-1111-1111-111111111111"
_WATCHED_ID = "22222222-2222-2222-2222-222222222222"


def _positions() -> list[dict]:
    return [
        {
            "instrumentId": _HELD_ID,
            "symbol": "600036",
            "name": "招商银行",
            "quantity": "100",
            "type": "股票",
        }
    ]


def _watchlist() -> list[dict]:
    return [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "type": "STOCK",
            "instrumentId": _WATCHED_ID,
            "price": "1680.00",
            "changePct": "1.20",
            "quality": "FRESH",
            "asOf": None,
        }
    ]


def _context(
    *,
    positions: list[dict] | None = None,
    watchlist: list[dict] | None = None,
    pre_market: dict | None = None,
) -> dict:
    return {
        "session": "PRE_MARKET",
        "summary": {},
        "positions": [] if positions is None else positions,
        "policy": None,
        "fundEstimates": [],
        "watchlist": [] if watchlist is None else watchlist,
        "marketSentiment": {},
        "preMarket": pre_market or {},
    }


def test_personalized_report_has_only_the_three_required_sections() -> None:
    draft = pre_market_personalized.build_report(
        _context(positions=_positions(), watchlist=_watchlist())
    )

    assert [item["title"] for item in draft["items"]] == [
        "持仓今日影响",
        "关注标的动态",
        "今日操作提醒",
    ]


def test_personalized_report_marks_missing_facts_as_missing() -> None:
    draft = pre_market_personalized.build_report(
        _context(positions=_positions(), watchlist=_watchlist())
    )

    bodies = {item["title"]: item["body"] for item in draft["items"]}
    # 有持仓、当日无匹配公司行动且估值健康 → 确定性结论（区别于"无数据"的「暂无」）。
    assert bodies["持仓今日影响"] == "今日无重大事件，持仓估值整体新鲜"
    assert "贵州茅台" in bodies["关注标的动态"]
    # 有持仓基金但无申赎截止/状态数据 → 无提醒的确定性空态。
    assert bodies["今日操作提醒"] == "今日无申赎截止提醒"
    assert draft["quality"] == "FRESH"


def test_personalized_report_reports_held_corporate_action_impact() -> None:
    draft = pre_market_personalized.build_report(
        _context(
            positions=_positions(),
            pre_market={
                "corporateActions": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "600036",
                        "name": "招商银行",
                        "actionType": "CASH_DIVIDEND",
                        "payDate": "2026-09-10",
                    }
                ]
            },
        )
    )

    impact = draft["items"][0]["body"]
    assert "招商银行(600036)现金分红除权" in impact
    assert "2026-09-10" in impact
    assert draft["items"][0]["quality"] == "FRESH"


def test_personalized_report_reports_watched_corporate_action() -> None:
    draft = pre_market_personalized.build_report(
        _context(
            watchlist=_watchlist(),
            pre_market={
                "corporateActions": [
                    {
                        "instrumentId": _WATCHED_ID,
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "actionType": "SPLIT",
                    }
                ]
            },
        )
    )

    body = draft["items"][1]["body"]
    assert "贵州茅台(600519)今日拆股除权" in body
    assert draft["items"][1]["quality"] == "FRESH"


def test_personalized_report_renders_cutoff_reminder() -> None:
    draft = pre_market_personalized.build_report(
        _context(
            positions=_positions(),
            pre_market={
                "fundCutoffRules": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "110022",
                        "name": "易方达消费行业",
                        "salesChannel": "DEFAULT",
                        "cutoffTime": "15:00:00",
                        "timezone": "Asia/Shanghai",
                    }
                ]
            },
        )
    )

    assert "15:00" in draft["items"][2]["body"]
    assert draft["items"][2]["quality"] == "FRESH"


def test_personalized_push_stays_under_200_characters() -> None:
    content = pre_market_personalized.format_push_content(
        _context(
            positions=_positions(),
            watchlist=_watchlist(),
            pre_market={
                "fundCutoffRules": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "110022",
                        "name": "易方达消费行业",
                        "salesChannel": "DEFAULT",
                        "cutoffTime": "15:00:00",
                        "timezone": "Asia/Shanghai",
                    }
                ]
            },
        )
    )

    assert len(content) <= 200
    assert content.count("\n") == 2
    assert "持仓：" in content and "关注：" in content and "提醒：" in content


def _install(monkeypatch, *, positions: list[dict], watchlist: list[dict]) -> dict:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield object()

    async def build_report_context(_conn, _tenant_id, _session, **kwargs):
        captured["scope"] = kwargs.get("asset_scope")
        captured["audience"] = kwargs.get("audience_user_id")
        return _context(positions=positions, watchlist=watchlist)

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("personalized phase must never call a model")

    async def insert_report(_conn, **kwargs):
        captured["insert"] = kwargs
        return "personalized-1"

    outbox = AsyncMock(return_value=1)
    write_audit = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "tenant_transaction", transaction)
    monkeypatch.setattr(pre_market_personalized, "tenant_transaction", transaction)
    monkeypatch.setattr(service, "build_report_context", build_report_context)
    monkeypatch.setattr(service, "chat_completion", fail_if_called)
    monkeypatch.setattr(
        service.repository, "latest_report", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(service.repository, "insert_report", insert_report)
    monkeypatch.setattr(
        service.repository, "insert_report_items", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(service.repository, "insert_report_outbox_deliveries", outbox)
    monkeypatch.setattr(service, "write_audit", write_audit)
    monkeypatch.setattr(
        service, "get_report_detail", AsyncMock(return_value={"id": "personalized-1"})
    )
    captured["outbox"] = outbox
    captured["write_audit"] = write_audit
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("positions", "watchlist"),
    [
        (_positions(), _watchlist()),
        (_positions(), []),
        ([], _watchlist()),
    ],
)
async def test_personalized_phase_generates_for_every_non_empty_combination(
    monkeypatch, positions, watchlist
) -> None:
    captured = _install(monkeypatch, positions=positions, watchlist=watchlist)

    result = await pre_market_personalized.generate_pre_market_personalized(
        object(),
        TenantContext("bob", "tenant-1", tenant_role="OWNER"),
        Settings(_env_file=None),
    )

    assert result == {"id": "personalized-1"}
    inserted = captured["insert"]
    assert isinstance(inserted, dict)
    assert inserted["report_variant"] == "PRE_MARKET_PERSONALIZED"
    assert inserted["audience_user_id"] == "bob"
    assert captured["outbox"].await_args.kwargs["target_user_id"] == "bob"
    scope = captured["scope"]
    assert scope is not None
    assert (scope.kind, scope.owner_user_id) == ("MINE", "bob")
    details = captured["write_audit"].await_args.kwargs["details"]
    assert details["generationMode"] == "PRE_MARKET_PERSONALIZED_DETERMINISTIC"
    assert details["audienceUserId"] == "bob"


@pytest.mark.asyncio
async def test_personalized_phase_skips_members_without_positions_or_watchlist(
    monkeypatch,
) -> None:
    captured = _install(monkeypatch, positions=[], watchlist=[])

    result = await pre_market_personalized.generate_pre_market_personalized(
        object(),
        TenantContext("bob", "tenant-1", tenant_role="OWNER"),
        Settings(_env_file=None),
    )

    assert result is None
    assert "insert" not in captured
    captured["outbox"].assert_not_awaited()


def _quote_watch(
    *,
    symbol: str,
    name: str,
    asset_type: str,
    price: str | None = None,
    change: str | None = None,
    quality: str = "FRESH",
    as_of=None,
) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "type": asset_type,
        "instrumentId": _WATCHED_ID,
        "price": price,
        "changePct": change,
        "quality": quality,
        "asOf": as_of,
    }


def _watch_body(watch: dict, now: datetime | None = None) -> str:
    ctx = _context(watchlist=[watch])
    return pre_market_personalized.build_report(ctx, now=now)["items"][1]["body"]


def test_watchlist_fund_without_quote_calls_out_stale_source() -> None:
    """场外基金无净值 → 说明数据源未更新，而非笼统「暂无行情」。"""
    body = _watch_body(
        _quote_watch(symbol="000001", name="华夏成长", asset_type="FUND"),
        now=datetime(2026, 9, 3, 10, 0),
    )
    assert "华夏成长(000001)暂无行情（数据源未更新" in body


def test_watchlist_stock_without_quote_in_session_hints_suspension() -> None:
    """交易时段内无行情 → 提示可能停牌或数据延迟（不直接断言停牌）。"""
    body = _watch_body(
        _quote_watch(symbol="600519", name="贵州茅台", asset_type="STOCK"),
        now=datetime(2026, 8, 31, 10, 30),
    )
    assert "盘中暂无最新行情，标的可能停牌或数据源延迟" in body


def test_watchlist_stock_without_quote_off_hours_explains_window() -> None:
    body = _watch_body(
        _quote_watch(symbol="600519", name="贵州茅台", asset_type="STOCK"),
        now=datetime(2026, 8, 31, 16, 0),
    )
    assert "非交易时段：交易日 09:30-15:00 为连续竞价" in body


def test_watchlist_weekend_without_quote_is_not_trading() -> None:
    body = _watch_body(
        _quote_watch(symbol="600519", name="贵州茅台", asset_type="STOCK"),
        now=datetime(2026, 9, 6, 10, 30),
    )
    assert "非交易时段" in body


def test_watchlist_stale_quote_shows_data_cutoff_time() -> None:
    """有价格但数据过期 → 标注数据截至时间，便于判断时效。"""
    body = _watch_body(
        _quote_watch(
            symbol="600519",
            name="贵州茅台",
            asset_type="STOCK",
            price="1680.00",
            change="1.20",
            quality="STALE",
            as_of=datetime(2026, 8, 28, 4, 0, tzinfo=ZoneInfo("UTC")),
        ),
        now=datetime(2026, 9, 3, 9, 0),
    )
    assert "最新价1680.0000、涨跌+1.20%" in body
    assert "数据质量：STALE" in body
    assert "数据截至08-28 12:00" in body


def test_watchlist_fresh_quote_has_no_asof_noise() -> None:
    body = _watch_body(
        _quote_watch(
            symbol="600519",
            name="贵州茅台",
            asset_type="STOCK",
            price="1680.00",
            change="1.20",
            quality="FRESH",
            as_of=datetime(2026, 9, 3, 9, 0, tzinfo=ZoneInfo("UTC")),
        )
    )
    assert "（数据质量：FRESH）" in body
    assert "数据截至" not in body
