"""Deterministic PRE_MARKET report and push contracts."""

from __future__ import annotations

from datetime import date

import pytest

from backend.reports import pre_market, repository


def _context() -> dict:
    return {
        "positions": [
            {
                "instrumentId": "11111111-1111-1111-1111-111111111111",
                "symbol": "600036",
                "name": "招商银行",
                "type": "A股",
                "quantity": "100",
            },
            {
                "instrumentId": "22222222-2222-2222-2222-222222222222",
                "symbol": "110022",
                "name": "易方达消费行业",
                "type": "场外基金",
                "quantity": "1000",
            },
        ],
        "marketSentiment": {
            "tradeDate": "2026-08-31",
            "totalUp": 3200,
            "totalDown": 1800,
            "sentimentScore": "63.50",
            "qualityStatus": "FRESH",
        },
        "preMarket": {
            "usIndices": [],
            "a50Future": None,
            "economicEvents": [],
            "subscriptionRedemptionChanges": [],
            "corporateActions": [
                {
                    "instrumentId": "11111111-1111-1111-1111-111111111111",
                    "symbol": "600036",
                    "name": "招商银行",
                    "actionType": "CASH_DIVIDEND",
                    "exDate": "2026-08-31",
                    "payDate": "2026-09-04",
                    "params": {"cash_per_share": "0.20"},
                }
            ],
            "fundCutoffRules": [
                {
                    "instrumentId": "22222222-2222-2222-2222-222222222222",
                    "symbol": "110022",
                    "name": "易方达消费行业",
                    "salesChannel": "DEFAULT",
                    "cutoffTime": "15:00:00",
                    "timezone": "Asia/Shanghai",
                }
            ],
        },
    }


def test_full_report_has_five_grounded_sections_and_explicit_missing_data() -> None:
    draft = pre_market.build_report(_context())

    expected_titles = [
        "隔夜市场速览",
        "今日重点关注",
        "持仓今日影响",
        "今日操作提醒",
        "一句话总结",
    ]
    assert [item["title"] for item in draft["items"]] == expected_titles
    assert draft["summary"].splitlines() == [
        "1. 隔夜市场速览：美股主要指数：暂无；A50期货：暂无。",
        "2. 今日重点关注：经济数据发布：暂无；持仓公司行动：招商银行(600036)今日现金分红除权；申赎状态变化：暂无。",
        "3. 持仓今日影响：招商银行(600036)现金分红除权，价格可能出现技术性调整；现金到账日为2026-09-04，实际收益以到账和税费为准。",
        "4. 今日操作提醒：易方达消费行业(110022)默认渠道截止时间15:00（Asia/Shanghai）；仅作截止时间参考，能否申赎以销售渠道实际状态为准。",
        "5. 一句话总结：当前市场情绪偏强；今日重点关注持仓公司行动和基金交易截止时间。",
    ]
    assert draft["status"] == "PARTIAL"
    assert draft["quality"] == "FRESH"
    assert [item["quality"] for item in draft["items"]] == [
        "UNAVAILABLE",
        "FRESH",
        "FRESH",
        "FRESH",
        "FRESH",
    ]


def test_missing_fields_are_never_invented_and_push_stays_within_200_chars() -> None:
    context = {
        "positions": [],
        "marketSentiment": {"qualityStatus": "UNAVAILABLE"},
        "preMarket": {},
    }

    draft = pre_market.build_report(context)
    push = pre_market.format_push_content(context)

    assert draft["summary"].count("暂无") >= 8
    assert "None" not in draft["summary"]
    assert "null" not in draft["summary"]
    assert push.splitlines() == [
        "🌏 隔夜市场：美股暂无；A50期货暂无",
        "📌 今日关注：经济数据暂无；公司行动暂无；申赎状态暂无",
        "📊 持仓影响：暂无",
        "⏰ 今日提醒：暂无",
        "💡 一句话总结：暂无",
    ]
    assert len(push) <= 200


def test_push_keeps_available_actions_and_cutoff_without_claiming_trade_availability() -> (
    None
):
    push = pre_market.format_push_content(_context())

    assert "招商银行现金分红除权" in push
    assert "易方达消费行业15:00截止参考" in push
    assert "渠道状态为准" in push
    assert "可申购" not in push
    assert "可赎回" not in push
    assert len(push) <= 200


def test_stale_sentiment_is_dated_instead_of_presented_as_current() -> None:
    context = _context()
    context["marketSentiment"].update(
        {"qualityStatus": "STALE", "tradeDate": "2026-08-28"}
    )

    draft = pre_market.build_report(context)
    push = pre_market.format_push_content(context)

    assert "截至2026-08-28（非实时），市场情绪偏强" in draft["summary"]
    assert "截至08-28情绪偏强" in push
    assert "当前市场情绪偏强" not in draft["summary"]


def test_push_renders_real_overnight_data_with_natural_prefix() -> None:
    """P0 wiring: real US/A50 figures flow into the natural-language push."""
    context = {
        "positions": [],
        "marketSentiment": {"qualityStatus": "UNAVAILABLE"},
        "preMarket": {
            "usIndices": [{"name": "道琼斯", "changePct": "1.23"}],
            "a50Future": {"name": "富时中国A50期货", "changePct": "0.50"},
            "economicEvents": [],
            "subscriptionRedemptionChanges": [],
            "corporateActions": [],
            "fundCutoffRules": [],
        },
    }
    lines = pre_market.format_push_content(context).splitlines()
    assert lines[0].startswith("🌏 隔夜市场：美股")
    assert "道琼斯+1.23%" in lines[0]
    assert "A50期货+0.50%" in lines[0]
    assert lines[1].startswith("📌 今日关注：")
    assert lines[2].startswith("📊 持仓影响：")
    assert lines[3].startswith("⏰ 今日提醒：")
    assert lines[4].startswith("💡 一句话总结：")
    assert len(pre_market.format_push_content(context)) <= 200


class _QueryRecorder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch(self, query: str, *_args):
        self.queries.append(query)
        return []


@pytest.mark.asyncio
async def test_pre_market_reference_queries_reject_future_aliases_and_late_cutoffs() -> (
    None
):
    conn = _QueryRecorder()
    instrument_ids = ["11111111-1111-1111-1111-111111111111"]
    trade_date = date(2026, 8, 31)

    await repository.get_pre_market_corporate_actions(conn, instrument_ids, trade_date)
    await repository.get_pre_market_cutoff_rules(conn, instrument_ids, trade_date)

    assert len(conn.queries) == 2
    assert all("ia.effective_from <= $2" in query for query in conn.queries)
    assert "rule.cutoff_time <= TIME '15:00'" in conn.queries[1]
