"""Deterministic POST_MARKET report and push contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.portfolio.scope import AssetScope
from backend.reports import post_market
from backend.reports import service as report_service


def _context() -> dict:
    return {
        "postMarket": {
            "performance": {
                "dailyPnl": "1250.50",
                "dailyPnlRate": "1.25",
                "quality": "FRESH",
                "asOf": "2026-09-07",
            },
            "contributions": {
                "items": [
                    {
                        "symbol": "600036",
                        "name": "招商银行",
                        "changePct": "2.10",
                        "contributionAmount": "820.20",
                        "quality": "FRESH",
                    },
                    {
                        "symbol": "510300",
                        "name": "沪深300ETF",
                        "changePct": "-0.80",
                        "contributionAmount": "-310.10",
                        "quality": "FRESH",
                    },
                ],
                "complete": True,
                "positionCount": 2,
                "attributedCount": 2,
            },
        },
        "positions": [
            {
                "name": "招商银行",
                "symbol": "600036",
                "quantity": "100",
                "weight": "45.00",
                "quality": "FRESH",
            },
            {
                "name": "估值过期基金",
                "symbol": "110022",
                "quantity": "200",
                "weight": "20.00",
                "quality": "STALE",
            },
        ],
        "policy": {"single_position_limit": "30.00"},
        "marketSentiment": {
            "totalUp": 3200,
            "totalDown": 1800,
            "qualityStatus": "FRESH",
            "tradeDate": "2026-09-07",
        },
    }


def test_post_market_push_answers_result_cause_and_tomorrow_under_300_chars() -> None:
    content = post_market.format_push_content(_context())

    assert "今日结果：组合+1.25%，盈亏+1,250.50元" in content
    assert "正贡献：招商银行+820.20元" in content
    assert "负贡献：沪深300ETF-310.10元" in content
    assert "明日关注：" in content
    assert "市场广度偏强" in content
    assert "110022估值过期" in content
    assert "600036仓位45.00%超30.00%" in content
    assert len(content) <= 300


def test_post_market_report_summary_and_items_use_the_same_structured_facts() -> None:
    report = post_market.build_report(_context())

    assert "1. 今日结果：" in report["summary"]
    assert "2. 盈亏原因：" in report["summary"]
    assert "3. 明日关注：" in report["summary"]
    assert [item["title"] for item in report["items"][:3]] == [
        "今日结果",
        "盈亏原因",
        "明日关注",
    ]
    assert report["quality"] == "STALE"
    assert report["items"][2]["quality"] == "STALE"
    assert report["policy_check_status"] == "WARNING"


def test_post_market_contribution_summary_caps_total_symbols_at_three() -> None:
    context = _context()
    context["postMarket"]["contributions"]["items"] = [
        {"name": "正一", "contributionAmount": "100", "quality": "FRESH"},
        {"name": "正二", "contributionAmount": "80", "quality": "FRESH"},
        {"name": "负一", "contributionAmount": "-90", "quality": "FRESH"},
        {"name": "负二", "contributionAmount": "-70", "quality": "FRESH"},
    ]
    context["postMarket"]["contributions"]["positionCount"] = 4
    context["postMarket"]["contributions"]["attributedCount"] = 4

    content = post_market.format_push_content(context)

    assert "正一" in content and "正二" in content and "负一" in content
    assert "负二" not in content


def test_post_market_missing_performance_and_contributions_fail_closed() -> None:
    context = _context()
    context["postMarket"] = {
        "performance": {
            "dailyPnl": None,
            "dailyPnlRate": None,
            "quality": "UNAVAILABLE",
        },
        "contributions": {
            "items": [],
            "complete": False,
            "positionCount": 2,
            "attributedCount": 0,
        },
    }

    content = post_market.format_push_content(context)

    assert "组合涨跌幅暂不可用，盈亏金额暂不可用" in content
    assert "正贡献：暂不可用（归因不完整）" in content
    assert "负贡献：暂不可用（归因不完整）" in content
    assert "+0.00" not in content
    assert "None" not in content


def test_post_market_marks_estimates_and_rejects_stale_daily_performance() -> None:
    context = _context()
    context["postMarket"]["performance"]["quality"] = "ESTIMATED"
    context["postMarket"]["contributions"]["items"][0]["quality"] = "ESTIMATED"

    estimated = post_market.format_push_content(context)

    assert "组合预估+1.25%，预估盈亏+1,250.50元" in estimated
    assert "招商银行(估)+820.20元" in estimated

    context["postMarket"]["performance"]["quality"] = "STALE"
    stale = post_market.format_push_content(context)

    assert "组合涨跌幅暂不可用，盈亏金额暂不可用" in stale
    assert "组合+1.25%" not in stale


def test_post_market_partial_attribution_never_claims_a_missing_sign_is_absent() -> (
    None
):
    context = _context()
    context["postMarket"]["contributions"]["items"] = [
        {"name": "已知正贡献", "contributionAmount": "100", "quality": "FRESH"}
    ]
    context["postMarket"]["contributions"].update(
        complete=False,
        positionCount=2,
        attributedCount=1,
    )

    content = post_market.format_push_content(context)

    assert "已知正贡献+100.00元" in content
    assert "负贡献：暂不可用（归因不完整）" in content
    assert "暂无负贡献" not in content


def test_post_market_does_not_present_stale_sentiment_as_today_signal() -> None:
    context = _context()
    context["marketSentiment"]["qualityStatus"] = "STALE"
    context["marketSentiment"]["tradeDate"] = "2026-09-05"

    content = post_market.format_push_content(context)

    assert "市场广度偏强" not in content
    assert "市场信号截至2026-09-05（非今日）" in content


@pytest.mark.asyncio
async def test_post_market_context_is_scoped_to_the_report_audience(
    monkeypatch,
) -> None:
    class Connection:
        async def fetchrow(self, _sql: str, *_args):
            return None

    scope = AssetScope(kind="MINE", owner_user_id="user-1")
    monkeypatch.setattr(
        report_service,
        "get_summary",
        AsyncMock(return_value={"dailyPnl": None}),
    )
    monkeypatch.setattr(
        report_service,
        "get_positions",
        AsyncMock(return_value=[{"instrumentId": "instrument-1", "quantity": "1"}]),
    )
    performance = AsyncMock(return_value={"dailyPnl": "10.00", "dailyPnlRate": "1.00"})
    contributions = AsyncMock(
        return_value={
            "items": [{"symbol": "600036"}],
            "complete": True,
            "positionCount": 1,
            "attributedCount": 1,
        }
    )
    monkeypatch.setattr(
        report_service.repository, "get_post_market_performance", performance
    )
    monkeypatch.setattr(
        report_service.repository, "get_post_market_contributions", contributions
    )
    monkeypatch.setattr(
        report_service.repository,
        "get_user_watchlist_context",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        report_service.market_service,
        "get_market_sentiment",
        AsyncMock(return_value={}),
    )

    context = await report_service.build_report_context(
        Connection(),
        "tenant-1",
        "POST_MARKET",
        audience_user_id="user-1",
        asset_scope=scope,
    )

    assert context["postMarket"]["performance"]["dailyPnl"] == "10.00"
    assert context["postMarket"]["contributions"]["items"] == [{"symbol": "600036"}]
    assert performance.await_args.kwargs["owner_user_id"] == "user-1"
    assert contributions.await_args.kwargs["owner_user_id"] == "user-1"
