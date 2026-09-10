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
            "avgCost": "38.82",
            "price": "39.15",
            "changePct": "0.84",
            "weight": "34.85",
            "quality": "FRESH",
            "changeQuality": "FRESH",
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


def test_personalized_report_prepends_holdings_table_without_changing_items() -> (
    None
):
    """The table extends the summary without altering auditable item records."""
    draft = pre_market_personalized.build_report(
        _context(positions=_positions(), watchlist=_watchlist())
    )

    assert draft["summary"].startswith(
        "## 持仓概览\n\n| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 仓位 |"
    )
    assert "| 600036 | 招商银行" in draft["summary"]
    assert [item["title"] for item in draft["items"]] == [
        "持仓今日影响",
        "关注标的动态",
        "今日操作提醒",
    ]


def test_personalized_report_uses_exact_no_holdings_copy_for_watchlist_only() -> (
    None
):
    """A watchlist-only member gets useful context without a fake empty table."""
    draft = pre_market_personalized.build_report(
        _context(positions=[], watchlist=_watchlist())
    )

    assert draft["summary"].startswith(
        "## 持仓概览\n\n暂无持仓，今日市场概况"
    )


def test_personalized_report_marks_missing_facts_as_missing() -> None:
    draft = pre_market_personalized.build_report(
        _context(positions=_positions(), watchlist=_watchlist())
    )

    bodies = {item["title"]: item["body"] for item in draft["items"]}
    # 有持仓、当日无匹配公司行动且估值健康 → 确定性结论（区别于"无数据"的「暂无」）。
    assert bodies["持仓今日影响"] == "今日无重大事件，持仓估值整体新鲜"
    assert "贵州茅台" in bodies["关注标的动态"]
    # 无风险条件时，今日操作提醒不再逐只枚举申赎状态。
    assert bodies["今日操作提醒"] == "今日无异常，持有场外基金15:00前可正常申赎。"
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
                        "actionDate": "2026-09-07",
                        "exDate": "2026-09-07",
                        "payDate": "2026-09-10",
                        "summary": "现金分红除息",
                    }
                ]
            },
        )
    )

    impact = draft["items"][0]["body"]
    assert "招商银行(600036)今日现金分红除权" in impact
    assert "2026-09-10" in impact
    assert draft["items"][0]["quality"] == "FRESH"


def test_personalized_report_keeps_stock_ex_rights_out_of_fund_operation_reminder() -> None:
    """股票除权保留在持仓影响，不占用基金操作提醒的有限位置。"""
    draft = pre_market_personalized.build_report(
        _context(
            positions=_positions(),
            pre_market={
                "reportDate": "2026-09-07",
                "corporateActions": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "600036",
                        "name": "招商银行",
                        "actionType": "CASH_DIVIDEND",
                        "actionDate": "2026-09-07",
                        "exDate": "2026-09-07",
                    }
                ],
            },
        )
    )

    reminders = {
        item["title"]: item["body"] for item in draft["items"]
    }
    assert reminders["今日操作提醒"] == "今日无异常，持有场外基金15:00前可正常申赎。"


def test_personalized_report_describes_fund_dividend_as_a_fund_event() -> None:
    """基金除息不能套用股票开盘价提醒，必须说明净值与到账口径。"""
    fund_position = {
        **_positions()[0],
        "instrumentId": _HELD_ID,
        "symbol": "110022",
        "name": "易方达消费行业",
        "type": "场外基金",
    }
    draft = pre_market_personalized.build_report(
        _context(
            positions=[fund_position],
            pre_market={
                "reportDate": "2026-09-11",
                "corporateActions": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "110022",
                        "name": "易方达消费行业",
                        "actionType": "FUND_DIVIDEND",
                        "actionDate": "2026-09-11",
                        "exDate": "2026-09-11",
                        "recordDate": "2026-09-10",
                        "payDate": "2026-09-14",
                    }
                ],
            },
        )
    )

    reminders = {item["title"]: item["body"] for item in draft["items"]}
    assert reminders["今日操作提醒"] == (
        "易方达消费行业(110022)今日基金分红除息，"
        "净值可能技术性调整，请关注红利到账。"
    )


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


def test_personalized_report_renders_all_four_days_and_action_categories() -> None:
    draft = pre_market_personalized.build_report(
        _context(
            positions=_positions(),
            watchlist=_watchlist(),
            pre_market={
                "reportDate": "2026-09-07",
                "corporateActions": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "600036",
                        "name": "招商银行",
                        "actionType": "EX_RIGHTS",
                        "actionDate": "2026-09-07",
                        "exDate": "2026-09-07",
                        "summary": "除权除息",
                    },
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "600036",
                        "name": "招商银行",
                        "actionType": "CASH_DIVIDEND",
                        "actionDate": "2026-09-08",
                        "payDate": "2026-09-08",
                        "summary": "现金红利到账",
                    },
                    {
                        "instrumentId": _WATCHED_ID,
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "actionType": "RIGHTS_ISSUE",
                        "actionDate": "2026-09-09",
                        "summary": "配股缴款开始",
                    },
                    {
                        "instrumentId": _WATCHED_ID,
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "actionType": "OTHER",
                        "actionDate": "2026-09-10",
                        "summary": "重大资产事项",
                    },
                ],
            },
        )
    )

    bodies = {item["title"]: item["body"] for item in draft["items"]}
    assert "招商银行(600036)今日除权除息" in bodies["持仓今日影响"]
    assert "招商银行(600036)09-08分红派息" in bodies["持仓今日影响"]
    assert "贵州茅台(600519)09-09配股" in bodies["关注标的动态"]
    assert "贵州茅台(600519)09-10其他重大公司行动" in bodies["关注标的动态"]


def test_personalized_push_limits_actions_to_three_and_reports_total() -> None:
    actions = [
        {
            "instrumentId": _HELD_ID,
            "symbol": f"60003{index}",
            "name": f"标的{index}",
            "actionType": "EX_RIGHTS" if index == 0 else "OTHER",
            "actionDate": f"2026-09-{7 + index:02d}",
            "summary": f"事件{index}",
        }
        for index in range(4)
    ]
    content = pre_market_personalized.format_push_content(
        _context(
            positions=_positions(),
            pre_market={"reportDate": "2026-09-07", "corporateActions": actions},
        )
    )

    assert "行动：" in content
    assert "标的0" in content and "标的1" in content and "标的2" in content
    assert "标的3" not in content
    # 股票除权由行动列表展示，不占用仅面向基金风险的今日操作提醒。
    assert content.count("标的0") == 1
    assert "等共4条" in content


def test_personalized_push_renders_every_holding_as_aligned_list() -> None:
    """The push keeps every holding in an aligned, non-table Markdown list."""
    positions = [
        {
            "instrumentId": str(index),
            "symbol": f"6000{index:02d}",
            "name": f"标的{index}",
            "type": "A股",
            "quantity": "100",
            "avgCost": "10.00",
            "price": "10.00",
            "changePct": str(index - 1),
            "weight": str(30 - index),
            "quality": "STALE" if index == 10 else "FRESH",
            "changeQuality": "FRESH",
        }
        for index in range(11)
    ]
    content = pre_market_personalized.format_push_content(
        _context(positions=positions)
    )

    assert content.splitlines()[:3] == [
        "**盘前持仓概览**",
        "```",
        "📉 标的0(600000) 仓位30.00% 当日-1.00%",
    ]
    assert "📈 标的2(600002) 仓位28.00% 当日+1.00%" in content
    assert "📈 标的10(600010) 仓位20.00% 当日+9.00%" in content
    assert all(f"6000{index:02d}" in content for index in range(11))
    assert "| 代码 |" not in content
    assert "⚠️" not in content


def test_personalized_report_uses_enriched_table_only_when_context_enables_it() -> None:
    positions = _positions()
    positions[0]["pnlAmount"] = "33.00"
    positions[0]["trendDisplay"] = "📈 连涨2天 (+3.00%)"
    draft = pre_market_personalized.build_report(
        _context(
            positions=positions,
            pre_market={"holdingsEnrichmentsEnabled": True},
        )
    )

    assert "| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 连续变化 | 仓位 |" in draft[
        "summary"
    ]
    assert "盈亏 +33.00" in draft["summary"]


def test_personalized_push_keeps_full_name_in_aligned_list() -> None:
    """The list preserves a long name instead of silently clipping it."""
    position = {
        "instrumentId": _HELD_ID,
        "symbol": "600036",
        "name": "这是一个非常非常长的持仓标的名称",
        "type": "A股",
        "quantity": "100",
        "avgCost": "10",
        "price": "11",
        "changePct": "1",
        "weight": "80",
        "quality": "FRESH",
        "changeQuality": "FRESH",
    }

    content = pre_market_personalized.format_push_content(
        _context(positions=[position])
    )

    assert "📈 这是一个非常非常长的持仓标的名称(600036) 仓位80.00% 当日+1.00%" in content


def test_personalized_push_watchlist_only_uses_no_holdings_copy() -> None:
    """A watchlist-only user is not shown a fake position row."""
    content = pre_market_personalized.format_push_content(
        _context(positions=[], watchlist=_watchlist())
    )

    assert content.splitlines()[1] == "暂无持仓，今日市场概况"
    assert len(content) <= 200


def test_personalized_push_uses_honest_empty_action_copy() -> None:
    content = pre_market_personalized.format_push_content(
        _context(positions=_positions(), pre_market={"reportDate": "2026-09-07"})
    )

    assert "行动：今日及未来3天暂无已录入公司行动" in content


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


def test_personalized_push_retains_reminders_and_actions_after_aligned_list() -> None:
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

    assert content.startswith("**盘前持仓概览**\n```")
    assert "提醒：" in content
    assert "行动：" in content


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
