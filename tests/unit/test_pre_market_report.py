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
        "4. 今日操作提醒：易方达消费行业(110022)默认渠道截止时间15:00（Asia/Shanghai），今日15:00前可提交申购/赎回（以渠道状态为准）。",
        "5. 一句话总结：情绪偏强；招商银行除权；1只基金15:00前可提交。",
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

    assert "暂无持仓" in draft["summary"]
    assert "今日无申赎截止提醒" in draft["summary"]
    assert draft["summary"].count("暂无") >= 6
    assert "None" not in draft["summary"]
    assert "null" not in draft["summary"]
    assert push.splitlines() == [
        "🌏 隔夜市场：美股暂无；A50期货暂无",
        "📌 今日关注：经济数据暂无；公司行动暂无；申赎状态暂无",
        "📊 持仓影响：暂无持仓",
        "⏰ 今日提醒：今日无申赎截止提醒",
        "💡 一句话总结：暂无",
    ]
    assert len(push) <= 200


def test_push_states_cutoff_window_with_channel_caveat() -> None:
    """今日提醒给出 15:00 前的操作窗口，但不断言渠道一定开放。"""
    push = pre_market.format_push_content(_context())

    assert "招商银行今日除权，预计低开" in push
    assert "易方达消费行业今日15:00前可提交申购/赎回" in push
    assert "（以渠道状态为准）" in push
    assert len(push) <= 200


def test_holding_impact_healthy_when_positions_untouched() -> None:
    """有持仓、当日无公司行动且估值/集中度健康 → 确定性结论，而非「暂无」。"""
    context = _context()
    context["preMarket"]["corporateActions"] = []

    assert pre_market.holding_impact_text(context) == "今日无重大事件，持仓估值整体新鲜"
    assert pre_market.impact_push_text(context) == "今日无重大事件，持仓估值整体新鲜"


def test_holding_impact_is_missing_without_positions() -> None:
    """没有持仓时无法归因，必须降级为「暂无持仓」，不得臆断影响。"""
    context = _context()
    context["positions"] = []

    assert pre_market.holding_impact_text(context) == "暂无持仓"
    assert pre_market.impact_push_text(context) == "暂无持仓"


def test_holding_impact_ignores_actions_on_unheld_instruments() -> None:
    """公司行动必须落在持仓上才计入影响（今日关注仍可展示非持仓标的）。"""
    context = _context()
    context["preMarket"]["corporateActions"] = [
        {
            "instrumentId": "99999999-9999-9999-9999-999999999999",
            "symbol": "601318",
            "name": "中国平安",
            "actionType": "CASH_DIVIDEND",
            "exDate": "2026-08-31",
            "payDate": "2026-09-10",
        }
    ]

    assert pre_market.holding_impact_text(context) == "今日无重大事件，持仓估值整体新鲜"
    push = pre_market.format_push_content(context)
    assert "中国平安今日除权" not in push


def test_holding_impact_flags_stale_valuation() -> None:
    """持仓估值过期（quality=STALE）必须作为风险项优先展示。"""
    context = _context()
    context["preMarket"]["corporateActions"] = []
    context["positions"][0]["quality"] = "STALE"
    context["positions"][0]["weight"] = "12.50"

    assert (
        pre_market.holding_impact_text(context)
        == "招商银行估值过期，持仓数据非最新，请核对后再决策。"
    )
    assert pre_market.impact_push_text(context) == "招商银行估值过期"


def test_holding_impact_flags_missing_valuation() -> None:
    """估值缺失（quality=MISSING）同样作为风险，措辞区别于「过期」。"""
    context = _context()
    context["preMarket"]["corporateActions"] = []
    context["positions"][0]["quality"] = "MISSING"
    context["positions"][0]["weight"] = "12.50"

    assert "招商银行估值缺失" in pre_market.holding_impact_text(context)
    assert pre_market.impact_push_text(context) == "招商银行估值缺失"


def test_holding_impact_flags_concentration_breach() -> None:
    """单一持仓占比超过政策上限必须作为集中度风险展示。"""
    context = _context()
    context["preMarket"]["corporateActions"] = []
    context["policy"] = {"single_position_limit": "30.00"}
    context["positions"][0]["quality"] = "FRESH"
    context["positions"][0]["weight"] = "35.00"

    assert (
        "招商银行占比35.00%，超过政策上限30.00%，建议关注集中度风险"
        in pre_market.holding_impact_text(context)
    )
    assert pre_market.impact_push_text(context) == "招商银行占比35.00%超上限30.00%"


def test_holding_impact_combines_action_and_risk() -> None:
    """既有公司行动又有估值过期时，行动在前、风险在后并列展示。"""
    context = _context()
    context["positions"][0]["quality"] = "STALE"
    context["positions"][0]["weight"] = "12.50"

    # 报告正文：行动短语 + 风险短语
    detailed = pre_market.holding_impact_text(context)
    assert "招商银行(600036)现金分红除权" in detailed
    assert "招商银行估值过期" in detailed

    # 推送短句：两项折叠到上限后用「等N项」
    push_impact = pre_market.impact_push_text(context)
    assert "招商银行今日除权，预计低开" in push_impact
    assert "招商银行估值过期" in push_impact


def test_holding_impact_valuation_takes_priority_over_concentration() -> None:
    """估值过期优先于集中度超限展示（风险项排序）。"""
    context = _context()
    context["preMarket"]["corporateActions"] = []
    context["policy"] = {"single_position_limit": "10.00"}
    context["positions"][0]["quality"] = "STALE"
    context["positions"][0]["weight"] = "35.00"

    risks = pre_market._position_health_risks(context, concise=True)
    assert risks[0] == "招商银行估值过期"
    assert risks[1] == "招商银行占比35.00%超上限10.00%"


def test_summary_combines_sentiment_holdings_and_events() -> None:
    """一句话总结 = 市场情绪 + 持仓影响 + 今日事件，缺项省略不编造。"""
    # 三项齐全
    assert pre_market.summary_clauses(_context()) == [
        "情绪偏强",
        "招商银行除权",
        "1只基金15:00前可提交",
    ]

    # 只有情绪：其余片段直接省略
    lean = _context()
    lean["positions"] = []
    lean["preMarket"]["fundCutoffRules"] = []
    assert pre_market.summary_clauses(lean) == ["情绪偏强"]

    # 全部缺失 → 暂无
    empty = {"positions": [], "marketSentiment": {}, "preMarket": {}}
    assert pre_market.summary_text(empty) == "暂无"


def test_summary_falls_back_to_mood_only_when_sentiment_missing() -> None:
    """情绪数据缺失时，仍应输出持仓影响与今日事件，而不是整句「暂无」。"""
    context = _context()
    context["marketSentiment"] = {}

    assert pre_market.summary_text(context) == "招商银行除权；1只基金15:00前可提交。"


def test_stale_sentiment_is_dated_instead_of_presented_as_current() -> None:
    context = _context()
    context["marketSentiment"].update(
        {"qualityStatus": "STALE", "tradeDate": "2026-08-28"}
    )

    draft = pre_market.build_report(context)
    push = pre_market.format_push_content(context)

    assert "一句话总结：截至08-28情绪偏强" in draft["summary"]
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


# --------------------------------------------------------------------------- #
# 今日提醒：基金申赎状态（可正常/暂停）语义
# --------------------------------------------------------------------------- #

_FUND_ID = "fund-110022"


def _fund_position() -> dict:
    return {
        "instrumentId": _FUND_ID,
        "symbol": "110022",
        "name": "易方达消费行业",
        "type": "场外基金",
        "quantity": "1000",
        "quality": "FRESH",
        "weight": "8.00",
    }


def _profile(
    *,
    purchase: str = "OPEN",
    redemption: str = "OPEN",
    instrument_id: str = _FUND_ID,
    name: str = "易方达消费行业",
    symbol: str = "110022",
) -> dict:
    return {
        "instrumentId": instrument_id,
        "symbol": symbol,
        "name": name,
        "purchaseStatus": purchase,
        "redemptionStatus": redemption,
    }


def _rule(
    *,
    cutoff: str = "15:00:00",
    instrument_id: str = _FUND_ID,
    name: str = "易方达消费行业",
    symbol: str = "110022",
) -> dict:
    return {
        "instrumentId": instrument_id,
        "symbol": symbol,
        "name": name,
        "salesChannel": "DEFAULT",
        "cutoffTime": cutoff,
        "timezone": "Asia/Shanghai",
    }


def _reminder_context(*, positions: list[dict], profiles=(), rules=()) -> dict:
    return {
        "positions": positions,
        "policy": {"single_position_limit": "30.00"},
        "marketSentiment": {
            "tradeDate": "2026-09-01",
            "totalUp": 3200,
            "totalDown": 1800,
            "qualityStatus": "FRESH",
        },
        "preMarket": {
            "usIndices": [],
            "a50Future": None,
            "economicEvents": [],
            "subscriptionRedemptionChanges": [],
            "corporateActions": [],
            "fundCutoffRules": list(rules),
            "fundProfiles": list(profiles),
        },
    }


def test_reminder_no_fund_holdings() -> None:
    """无持仓基金 → 「今日无申赎截止提醒」；非持仓基金的截止规则也不展示。"""
    stock = {
        "instrumentId": "s1",
        "symbol": "600036",
        "name": "招商银行",
        "type": "A股",
        "quantity": "100",
    }
    context = _reminder_context(
        positions=[stock],
        rules=[_rule(instrument_id="other-fund")],
    )
    assert pre_market.cutoff_text(context) == "今日无申赎截止提醒"
    assert pre_market.cutoff_push_text(context) == "今日无申赎截止提醒"
    assert pre_market.reminder_has_data(context) is False


def test_reminder_held_fund_without_status_data() -> None:
    """有持仓基金但无（截止规则/申赎档案）状态数据 → 「今日无申赎截止提醒」。"""
    context = _reminder_context(positions=[_fund_position()])
    assert pre_market.cutoff_text(context) == "今日无申赎截止提醒"
    assert pre_market.cutoff_push_text(context) == "今日无申赎截止提醒"


def test_reminder_open_fund_with_cutoff_shows_window() -> None:
    """档案 OPEN + 有截止规则 → 展示今日截止窗口（带渠道限定）。"""
    context = _reminder_context(
        positions=[_fund_position()],
        profiles=[_profile()],
        rules=[_rule()],
    )
    detailed = pre_market.cutoff_text(context)
    assert "易方达消费行业(110022)默认渠道截止时间15:00（Asia/Shanghai）" in detailed
    assert "今日15:00前可提交申购/赎回" in detailed
    assert "（以渠道状态为准）" in detailed
    push = pre_market.cutoff_push_text(context)
    assert "易方达消费行业今日15:00前可提交申购/赎回" in push
    assert "（以渠道状态为准）" in push


def test_reminder_suspended_purchase() -> None:
    """档案申购=SUSPENDED → 「今日暂停申购」，优先于截止规则。"""
    context = _reminder_context(
        positions=[_fund_position()],
        profiles=[_profile(purchase="SUSPENDED")],
        rules=[_rule()],
    )
    detailed = pre_market.cutoff_text(context)
    assert "易方达消费行业(110022)今日暂停申购" in detailed
    assert "（以基金公告为准）" in detailed
    assert pre_market.cutoff_push_text(context).startswith("易方达消费行业今日暂停申购")


def test_reminder_suspended_purchase_and_redemption() -> None:
    """申购、赎回均 SUSPENDED → 「今日暂停申购/赎回」。"""
    context = _reminder_context(
        positions=[_fund_position()],
        profiles=[_profile(purchase="SUSPENDED", redemption="SUSPENDED")],
    )
    assert "易方达消费行业(110022)今日暂停申购/赎回" in pre_market.cutoff_text(context)
    assert pre_market.cutoff_push_text(context).startswith(
        "易方达消费行业今日暂停申购/赎回"
    )


def test_reminder_open_fund_without_cutoff_says_normal() -> None:
    """档案 OPEN 但无 15:00 前截止 → 「今日可正常申购/赎回」，不编造截止。"""
    context = _reminder_context(
        positions=[_fund_position()],
        profiles=[_profile()],
    )
    detailed = pre_market.cutoff_text(context)
    assert "易方达消费行业(110022)今日可正常申购/赎回" in detailed
    assert "暂无15:00前截止" in detailed
    assert pre_market.cutoff_push_text(context).startswith(
        "易方达消费行业今日可正常申购/赎回"
    )


def test_reminder_suspended_ranked_before_cutoff() -> None:
    """暂停基金排在截止窗口基金之前。"""
    suspended_fund = {
        "instrumentId": "fund-a",
        "symbol": "000001",
        "name": "华夏成长",
        "type": "场外基金",
        "quantity": "100",
        "quality": "FRESH",
        "weight": "5.00",
    }
    normal_fund = {
        "instrumentId": "fund-b",
        "symbol": "000002",
        "name": "嘉实增长",
        "type": "场外基金",
        "quantity": "100",
        "quality": "FRESH",
        "weight": "5.00",
    }
    context = _reminder_context(
        positions=[suspended_fund, normal_fund],
        profiles=[
            _profile(purchase="SUSPENDED", instrument_id="fund-a", name="华夏成长")
        ],
        rules=[
            _rule(
                instrument_id="fund-b",
                name="嘉实增长",
                symbol="000002",
            )
        ],
    )
    text = pre_market.cutoff_push_text(context)
    assert text.index("华夏成长今日暂停申购") < text.index(
        "嘉实增长今日15:00前可提交申购/赎回"
    )


def test_reminder_event_clause_flags_suspension() -> None:
    """一句话总结：有暂停 → 「N只基金暂停申赎」，不输出截止口径。"""
    context = _reminder_context(
        positions=[_fund_position()],
        profiles=[_profile(purchase="SUSPENDED")],
        rules=[_rule()],
    )
    assert "1只基金暂停申赎" in pre_market.summary_clauses(context)


@pytest.mark.asyncio
async def test_fund_profiles_query_is_effective_window_scoped() -> None:
    conn = _QueryRecorder()
    instrument_ids = ["11111111-1111-1111-1111-111111111111"]
    trade_date = date(2026, 8, 31)

    await repository.get_pre_market_fund_profiles(conn, instrument_ids, trade_date)

    assert len(conn.queries) == 1
    query = conn.queries[0]
    assert "reference.fund_profiles" in query
    assert "profile.effective_from <= $2" in query
    assert "DISTINCT ON (profile.instrument_id)" in query
