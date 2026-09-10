"""Deterministic PRE_MARKET report and push contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

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


def test_holdings_markdown_sorts_formats_marks_and_escapes() -> None:
    """The table is stable, typed, safe, and honest about stale facts."""
    context = {
        "positions": [
            {
                "instrumentId": "fund",
                "symbol": "110022",
                "name": "基金|成长\nA",
                "type": "场外基金",
                "quantity": "118420.52",
                "avgCost": "1.73",
                "price": None,
                "changePct": None,
                "weight": None,
                "quality": "FRESH",
                "changeQuality": "UNAVAILABLE",
            },
            {
                "instrumentId": "stock",
                "symbol": "600036",
                "name": "招商银行",
                "type": "A股",
                "quantity": "8500",
                "avgCost": "38.82",
                "price": "39.15",
                "marketValue": "332775.00",
                "pnlRate": "0.85",
                "changePct": "0.84",
                "weight": "34.85",
                "quality": "FRESH",
                "changeQuality": "STALE",
            },
            {
                "instrumentId": "etf",
                "symbol": "510300",
                "name": "沪深300ETF",
                "type": "场内ETF",
                "quantity": "62000",
                "avgCost": "4.14",
                "price": "4.12",
                "marketValue": "255440.00",
                "pnlRate": "-0.48",
                "changePct": "-0.31",
                "weight": "29.68",
                "quality": "FRESH",
                "changeQuality": "FRESH",
            },
        ]
    }

    lines = pre_market.holdings_markdown(context).splitlines()

    assert lines[0] == "| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 仓位 |"
    assert lines[2].startswith("| 600036 | 招商银行⚠️ | 8,500股 |")
    assert lines[3].startswith("| 510300 | 沪深300ETF | 62,000份 |")
    assert lines[4].startswith(r"| 110022 | 基金\|成长 A⚠️ | 118,420.52份 |")
    assert "| 39.15（持仓 +0.85%） | 332,775 | +0.84% | 34.85% |" in lines[2]
    assert "| 4.12（持仓 -0.48%） | 255,440 | -0.31% | 29.68% |" in lines[3]
    assert "| --（持仓 --） | -- | 暂不可用 | -- |" in lines[4]


def test_holdings_table_derives_complete_fund_values_from_summary() -> None:
    """Fund rows must not lose market value, return, or weight when derivable."""
    context = {
        "summary": {"totalAsset": "1000.00"},
        "positions": [
            {
                "symbol": "110022",
                "name": "易方达消费行业",
                "type": "场外基金",
                "quantity": "100",
                "avgCost": "1.50",
                "price": "2.00",
                "marketValue": None,
                "pnlRate": None,
                "changePct": "1.20",
                "changeSourceAsOf": datetime(
                    2026, 9, 7, 0, 0, tzinfo=ZoneInfo("UTC")
                ),
                "weight": None,
                "quality": "STALE",
                "changeQuality": "FRESH",
            }
        ],
    }

    row = pre_market.position_table_rows(context)[0]
    markdown = pre_market.holdings_markdown(context)

    assert row["marketValue"] == 200
    assert row["pnlRate"].quantize(Decimal("0.01")) == Decimal("33.33")
    assert row["weight"] == 20
    assert "| 110022 | 易方达消费行业⚠️ | 100份 | 1.5 |" in markdown
    assert "| 2（持仓 +33.33%） | 200 | +1.20%（9/7） | 20.00% |" in markdown
    assert "基金“当日”采用最近披露净值涨跌幅" in markdown
    assert "⚠️ 表示价格或涨跌数据质量不是 FRESH" in markdown


def test_personalized_markdown_has_trend_and_market_value_pnl() -> None:
    context = {
        "preMarket": {"holdingsEnrichmentsEnabled": True},
        "positions": [
            {
                "symbol": "600036",
                "name": "招商银行",
                "type": "A股",
                "quantity": "100",
                "avgCost": "26.80",
                "price": "39.15",
                "marketValue": "3915",
                "pnlAmount": "1234.56",
                "changePct": "0.84",
                "trendDisplay": "📈 连涨3天 (+3.50%)",
                "weight": "34.85",
                "quality": "FRESH",
                "changeQuality": "FRESH",
            }
        ],
    }

    markdown = pre_market.personalized_holdings_markdown(context)

    assert "| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 连续变化 | 仓位 |" in markdown
    assert "3,915（盈亏 +1,234.56）" in markdown
    assert "📈 连涨3天 (+3.50%)" in markdown


def test_standard_markdown_keeps_eight_columns() -> None:
    markdown = pre_market.holdings_markdown(_context())

    assert "连续变化" not in markdown.splitlines()[0]


def test_holdings_warning_is_shown_for_every_non_fresh_quality() -> None:
    """ESTIMATED is not FRESH and therefore needs the same explained marker."""
    markdown = pre_market.holdings_markdown(
        {
            "positions": [
                {
                    "symbol": "110022",
                    "name": "易方达消费行业",
                    "type": "场外基金",
                    "quantity": "100",
                    "quality": "ESTIMATED",
                    "changeQuality": "FRESH",
                }
            ]
        }
    )

    assert "易方达消费行业⚠️" in markdown
    assert "⚠️ 表示价格或涨跌数据质量不是 FRESH" in markdown


def test_fund_day_change_without_persisted_nav_is_explicitly_unavailable() -> None:
    markdown = pre_market.holdings_markdown(
        {
            "positions": [
                {
                    "symbol": "110022",
                    "name": "易方达消费行业",
                    "type": "场外基金",
                    "quantity": "100",
                    "quality": "FRESH",
                    "changeQuality": "UNAVAILABLE",
                }
            ]
        }
    )

    assert "| 暂不可用 |" in markdown


def test_position_table_rows_use_symbol_as_stable_tie_break() -> None:
    """Equal weights cannot make report hashes depend on database row order."""
    context = {
        "positions": [
            {
                "symbol": symbol,
                "name": symbol,
                "type": "A股",
                "quantity": "1",
                "weight": "10",
                "quality": "FRESH",
                "changeQuality": "FRESH",
            }
            for symbol in ("600519", "000001")
        ]
    }

    assert [row["symbol"] for row in pre_market.position_table_rows(context)] == [
        "000001",
        "600519",
    ]


def test_holdings_markdown_renders_non_finite_values_as_missing() -> None:
    """Non-finite numbers must not leak into an auditable report."""
    context = {
        "positions": [
            {
                "symbol": "600036",
                "name": "招商银行",
                "type": "A股",
                "quantity": "Infinity",
                "avgCost": "NaN",
                "price": None,
                "changePct": None,
                "weight": None,
                "quality": "MISSING",
                "changeQuality": "UNAVAILABLE",
            }
        ]
    }

    row = pre_market.holdings_markdown(context).splitlines()[2]
    assert row == (
        "| 600036 | 招商银行⚠️ | -- | -- | --（持仓 --） | -- | 暂不可用 | -- |"
    )


def test_holdings_markdown_neutralizes_inline_markdown_in_asset_names() -> None:
    """An imported asset name cannot inject a link into the audit report."""
    context = {
        "positions": [
            {
                "symbol": "600036",
                "name": "[查看](https://evil.example)",
                "type": "A股",
                "quantity": "1",
                "quality": "FRESH",
                "changeQuality": "FRESH",
            }
        ]
    }

    markdown = pre_market.holdings_markdown(context)

    assert r"\[查看\]\(https://evil.example\)" in markdown


def test_holdings_markdown_empty_state() -> None:
    assert pre_market.holdings_markdown({"positions": []}) == (
        "暂无持仓，今日市场概况"
    )


def test_full_report_has_seven_grounded_sections_and_explicit_missing_data() -> None:
    draft = pre_market.build_report(_context())

    expected_titles = [
        "隔夜市场速览",
        "今日焦点事件",
        "今日重点关注",
        "持仓今日影响",
        "操作参考",
        "今日操作提醒",
        "一句话总结",
    ]
    assert [item["title"] for item in draft["items"]] == expected_titles
    assert draft["summary"].startswith(
        "## 持仓概览\n\n| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 仓位 |"
    )
    assert draft["summary"].splitlines()[-7:] == [
        "1. 隔夜市场速览：美股主要指数：暂无；A50期货：暂无。",
        "2. 今日焦点事件：招商银行今日除权，预计低开。",
        "3. 今日重点关注：经济数据发布：暂无；持仓公司行动：招商银行(600036)今日现金分红除权；申赎状态变化：暂无。",
        "4. 持仓今日影响：招商银行(600036)现金分红除权，价格可能出现技术性调整；现金到账日为2026-09-04，实际收益以到账和税费为准。",
        "5. 操作参考：招商银行今日除权，若除权后价格偏低，可关注填权行情；易方达消费行业今日15:00前可申赎，请留意截止时间。",
        "6. 今日操作提醒：今日无异常，持有场外基金15:00前可正常申赎。",
        "7. 一句话总结：情绪偏强；招商银行除权；1只基金15:00前可提交。",
    ]
    assert draft["status"] == "PARTIAL"
    assert draft["quality"] == "FRESH"
    assert [item["quality"] for item in draft["items"]] == [
        "UNAVAILABLE",
        "FRESH",
        "FRESH",
        "FRESH",
        "FRESH",
        "FRESH",
        "FRESH",
    ]


def test_missing_fields_are_never_invented_and_push_stays_within_700_chars() -> None:
    context = {
        "positions": [],
        "marketSentiment": {"qualityStatus": "UNAVAILABLE"},
        "preMarket": {},
    }

    draft = pre_market.build_report(context)
    push = pre_market.format_push_content(context)

    assert "暂无持仓" in draft["summary"]
    assert "今日无异常，持有场外基金15:00前可正常申赎。" in draft["summary"]
    assert draft["summary"].count("暂无") >= 6
    assert "None" not in draft["summary"]
    assert "null" not in draft["summary"]
    assert push.splitlines() == [
        "🌏 隔夜市场：美股暂无；A50期货暂无",
        "📌 今日关注：经济数据暂无；公司行动今日及未来3天暂无已录入公司行动；申赎状态暂无",
        "📊 持仓影响：暂无持仓",
        "⏰ 今日提醒：今日无异常，持有场外基金15:00前可正常申赎。",
        "🎯 今日焦点：今日暂无重大事件关注",
        "🧭 操作参考：今日无明确操作参考",
        "💡 一句话总结：暂无",
    ]
    assert len(push) <= 700


def test_standard_push_dates_future_actions_and_folds_after_three() -> None:
    context = _context()
    context["preMarket"]["reportDate"] = "2026-09-07"
    context["preMarket"]["corporateActions"] = [
        {
            "instrumentId": context["positions"][0]["instrumentId"],
            "symbol": f"60003{index}",
            "name": f"标的{index}",
            "actionType": "OTHER",
            "actionDate": f"2026-09-{7 + index:02d}",
            "summary": f"事件{index}",
        }
        for index in range(4)
    ]

    attention = pre_market.format_push_content(context).splitlines()[1]

    assert "标的0(600030)今日其他重大公司行动" in attention
    assert "标的1(600031)09-08其他重大公司行动" in attention
    assert "标的2(600032)09-09其他重大公司行动" in attention
    assert "标的3" not in attention
    assert "等共4条" in attention


def test_push_uses_actionable_operation_reminder_instead_of_cutoff_enumeration() -> None:
    """推送只保留可执行提醒，不再逐只展示正常基金截止窗口。"""
    push = pre_market.format_push_content(_context())

    assert "招商银行今日除权，预计低开" in push
    assert "今日无异常，持有场外基金15:00前可正常申赎。" in push
    assert len(push) <= 700


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

    # 无具体估值日期时给出「过期 + 影响」而非笼统的非最新提示。
    assert (
        pre_market.holding_impact_text(context)
        == "招商银行估值已过期，将影响估值与盈亏归因，请核对后再决策。"
    )
    assert pre_market.impact_push_text(context) == "招商银行估值过期"


def test_holding_impact_groups_multiple_stale_off_exchange_funds() -> None:
    """多个场外基金过期时只保留一条汇总风险，避免逐只刷屏。"""
    context = _context()
    context["preMarket"]["corporateActions"] = []
    context["positions"] = [
        {
            "instrumentId": "fund-1",
            "symbol": "110022",
            "name": "易方达消费行业",
            "type": "场外基金",
            "quantity": "100",
            "quality": "STALE",
            "valuationDate": "2026-09-07",
            "weight": "20",
        },
        {
            "instrumentId": "fund-2",
            "symbol": "001632",
            "name": "天弘中证食品饮料",
            "type": "场外基金",
            "quantity": "100",
            "quality": "STALE",
            "valuationDate": "2026-09-07",
            "weight": "15",
        },
    ]

    expected = (
        "2只场外基金估值数据已过期（最新净值日为2026-09-07），"
        "将影响估值与盈亏归因，请核对后再决策。"
    )
    assert pre_market.holding_impact_text(context) == expected
    assert pre_market.impact_push_text(context) == (
        "2只场外基金估值数据已过期（最新净值日为2026-09-07），"
        "将影响估值与盈亏归因，请核对后再决策"
    )


def test_holding_impact_keeps_one_stale_off_exchange_fund_individual() -> None:
    """仅一只场外基金过期时保留名称和净值日期，方便用户核对。"""
    context = _context()
    context["preMarket"]["corporateActions"] = []
    context["positions"] = [
        {
            "instrumentId": "fund-1",
            "symbol": "110022",
            "name": "易方达消费行业",
            "type": "场外基金",
            "quantity": "100",
            "quality": "STALE",
            "valuationDate": "2026-09-07",
            "weight": "20",
        }
    ]

    assert pre_market.holding_impact_text(context) == (
        "易方达消费行业估值已过期（2026-09-07 以来未更新），"
        "将影响估值与盈亏归因，请核对后再决策。"
    )


def test_holding_impact_stale_valuation_shows_date_when_available() -> None:
    """估值过期且知道最近估值日期时，文案显示具体日期增强可执行性。"""
    context = _context()
    context["preMarket"]["corporateActions"] = []
    context["positions"][0]["quality"] = "STALE"
    context["positions"][0]["weight"] = "12.50"
    context["positions"][0]["valuationDate"] = "2026-08-28"

    text = pre_market.holding_impact_text(context)
    assert "招商银行估值已过期（2026-08-28 以来未更新）" in text
    assert "将影响估值与盈亏归因，请核对后再决策" in text


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
    assert "招商银行估值已过期" in detailed

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
    assert lines[4].startswith("🎯 今日焦点：")
    assert lines[5].startswith("🧭 操作参考：")
    assert lines[6].startswith("💡 一句话总结：")
    assert len(pre_market.format_push_content(context)) <= 700


class _QueryRecorder:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.args: list[tuple[object, ...]] = []

    async def fetch(self, query: str, *args):
        self.queries.append(query)
        self.args.append(args)
        return []


class _CoverageConnection:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.query = ""

    async def fetch(self, query: str, *_args):
        self.query = query
        return self.rows


@pytest.mark.asyncio
async def test_pre_market_position_quotes_require_price_alignment_and_report_cutoff():
    """A day change must belong to the persisted price shown in the report."""
    as_of = datetime(2026, 9, 8, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai"))
    conn = _CoverageConnection(
        [
            {
                "instrument_id": "11111111-1111-1111-1111-111111111111",
                "change_pct": "0.84",
                "quality_status": "STALE",
                "source_as_of": as_of,
            }
        ]
    )

    result = await repository.get_pre_market_position_quotes(
        conn,
        {"11111111-1111-1111-1111-111111111111": "39.15"},
        as_of,
    )

    assert result == {
        "11111111-1111-1111-1111-111111111111": {
            "changePct": "0.84",
            "quality": "STALE",
            "sourceAsOf": as_of,
        }
    }
    assert "o.as_of <= $3" in conn.query
    assert "o.value = target.market_price" in conn.query
    assert "o.field_code IN ('CLOSE', 'NAV', 'ESTIMATED_NAV')" in conn.query
    assert "previous_nav" in conn.query
    assert "OFF_EXCHANGE_FUND" in conn.query


@pytest.mark.asyncio
async def test_pre_market_position_quotes_return_latest_official_fund_nav_change():
    """OTC funds use the latest two persisted official NAV rows, with date."""
    nav_as_of = datetime(2026, 9, 7, 0, 0, tzinfo=ZoneInfo("UTC"))
    conn = _CoverageConnection(
        [
            {
                "instrument_id": "22222222-2222-2222-2222-222222222222",
                "change_pct": "-1.03",
                "quality_status": "STALE",
                "source_as_of": nav_as_of,
            }
        ]
    )

    result = await repository.get_pre_market_position_quotes(
        conn,
        {"22222222-2222-2222-2222-222222222222": "1.73"},
        datetime(2026, 9, 8, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result["22222222-2222-2222-2222-222222222222"] == {
        "changePct": "-1.03",
        "quality": "STALE",
        "sourceAsOf": nav_as_of,
    }


@pytest.mark.asyncio
async def test_pre_market_position_quotes_skip_database_for_empty_input():
    """A member without priced holdings must not issue an unnecessary query."""
    conn = _CoverageConnection([])

    result = await repository.get_pre_market_position_quotes(
        conn,
        {},
        datetime(2026, 9, 8, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result == {}
    assert conn.query == ""
@pytest.mark.asyncio
async def test_pre_market_reference_queries_reject_future_aliases_and_late_cutoffs() -> (
    None
):
    conn = _QueryRecorder()
    instrument_ids = ["11111111-1111-1111-1111-111111111111"]
    trade_date = date(2026, 8, 31)

    await repository.get_pre_market_corporate_actions(
        conn, instrument_ids, trade_date, date(2026, 9, 3)
    )
    await repository.get_pre_market_cutoff_rules(conn, instrument_ids, trade_date)

    assert len(conn.queries) == 2
    assert all("ia.effective_from <= $2" in query for query in conn.queries)
    assert "WITH latest_revision AS" in conn.queries[0]
    assert "action_date BETWEEN $2 AND $3" in conn.queries[0]
    assert "quality_status" in conn.queries[0]
    assert "verified_by" in conn.queries[0]
    assert conn.args[0][1:] == (trade_date, date(2026, 9, 3))
    assert "rule.cutoff_time <= TIME '15:00'" in conn.queries[1]


def test_company_action_display_filters_unverified_conflicts_and_marks_stale_data() -> None:
    context = _context()
    context["preMarket"]["corporateActions"] = [
        {
            "instrumentId": context["positions"][0]["instrumentId"],
            "symbol": "600036",
            "name": "招商银行",
            "actionType": "CASH_DIVIDEND",
            "actionDate": "2026-08-31",
            "qualityStatus": "FRESH",
            "verifiedBy": [
                "AKSHARE_CORPORATE_ACTIONS",
                "BAOSTOCK_CORPORATE_ACTIONS",
            ],
        },
        {
            "instrumentId": context["positions"][0]["instrumentId"],
            "symbol": "600036",
            "name": "招商银行",
            "actionType": "OTHER",
            "actionDate": "2026-09-01",
            "qualityStatus": "STALE",
            "verifiedBy": ["AKSHARE_CORPORATE_ACTIONS"],
        },
        {
            "instrumentId": context["positions"][0]["instrumentId"],
            "symbol": "600036",
            "name": "招商银行",
            "actionType": "RIGHTS_ISSUE",
            "actionDate": "2026-09-02",
            "qualityStatus": "CONFLICT",
        },
        {
            "instrumentId": context["positions"][0]["instrumentId"],
            "symbol": "600036",
            "name": "招商银行",
            "actionType": "RIGHTS_ISSUE",
            "actionDate": "2026-09-03",
            "qualityStatus": "UNAVAILABLE",
        },
    ]

    text = pre_market._attention(context["preMarket"])

    assert "来源：AKSHARE_CORPORATE_ACTIONS、BAOSTOCK_CORPORATE_ACTIONS" in text
    assert "数据可能过期；来源：AKSHARE_CORPORATE_ACTIONS" in text
    assert "配股" not in text


def test_conflicting_corporate_actions_do_not_make_pre_market_data_fresh() -> None:
    context = _context()
    context["marketSentiment"]["qualityStatus"] = "UNAVAILABLE"
    context["preMarket"]["subscriptionRedemptionChanges"] = []
    context["preMarket"]["fundCutoffRules"] = []
    context["preMarket"]["corporateActions"] = [
        {
            "instrumentId": context["positions"][0]["instrumentId"],
            "symbol": "600036",
            "name": "招商银行",
            "actionType": "RIGHTS_ISSUE",
            "actionDate": "2026-09-02",
            "qualityStatus": "CONFLICT",
        }
    ]

    assert pre_market._quality(context) == "UNAVAILABLE"


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
        "summary": {"totalAsset": "100000", "cashBalance": "10000"},
        "policy": {"single_position_limit": "30.00"},
        "marketSentiment": {
            "tradeDate": "2026-09-01",
            "totalUp": 3200,
            "totalDown": 1800,
            "qualityStatus": "FRESH",
        },
        "preMarket": {
            "reportDate": "2026-09-01",
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
    """有持仓基金但无（截止规则/申赎档案）状态数据 → 空态附默认 15:00 提示。"""
    context = _reminder_context(positions=[_fund_position()])
    expected = (
        "今日无申赎截止提醒；持有场外基金默认 15:00 前可提交申购/赎回（以渠道状态为准）"
    )
    assert pre_market.cutoff_text(context) == expected
    assert pre_market.cutoff_push_text(context) == expected


def test_operation_reminder_uses_one_normal_state_when_no_risk_rule_matches() -> None:
    """防止今日操作提醒重新逐只罗列正常申赎状态。"""
    context = _reminder_context(
        positions=[_fund_position()],
        profiles=[_profile()],
        rules=[_rule()],
    )

    expected = "今日无异常，持有场外基金15:00前可正常申赎。"
    assert pre_market.operation_reminder_text(context) == expected
    assert pre_market.operation_reminder_push_text(context) == expected


@pytest.mark.parametrize(
    ("name", "context", "expected"),
    [
        (
            "fund_valuation_drop",
            _reminder_context(
                positions=[{**_fund_position(), "changePct": "-2.50", "changeQuality": "ESTIMATED"}]
            ),
            "易方达消费行业(110022)预估下跌2.50%，请关注估值波动，避免情绪化赎回",
        ),
        (
            "fund_concentration",
            _reminder_context(positions=[{**_fund_position(), "weight": "35.00"}]),
            "易方达消费行业(110022)仓位35.00%，超过30%阈值，建议评估集中度并避免继续加仓",
        ),
        (
            "fund_downward_streak",
            _reminder_context(
                positions=[
                    {
                        **_fund_position(),
                        "trendDirection": "DOWN",
                        "trendDays": 3,
                        "trendChangePct": "-4.20",
                        "trendQuality": "FRESH",
                    }
                ]
            ),
            "易方达消费行业(110022)已连跌3天（累计-4.20%），请关注趋势是否延续",
        ),
        (
            "low_cash",
            {
                **_reminder_context(positions=[_fund_position()]),
                "summary": {"totalAsset": "100000", "cashBalance": "3000"},
            },
            "现金占比3.00%，低于5%阈值，建议预留流动性，避免新增高波动仓位",
        ),
        (
            "fund_dividend",
            {
                **_reminder_context(positions=[_fund_position()]),
                "preMarket": {
                    **_reminder_context(positions=[_fund_position()])["preMarket"],
                    "corporateActions": [
                        {
                            "instrumentId": _FUND_ID,
                            "symbol": "110022",
                            "name": "易方达消费行业",
                            "actionType": "FUND_DIVIDEND",
                            "actionDate": "2026-09-01",
                            "exDate": "2026-09-01",
                        }
                    ],
                },
            },
            "易方达消费行业(110022)今日基金分红除息，净值可能技术性调整，请关注红利到账",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_operation_reminder_emits_each_actionable_rule(
    name: str, context: dict, expected: str
) -> None:
    """每个可操作条件都必须产生确定性提醒，而非回退到申赎枚举。"""
    assert expected in pre_market.operation_reminder_text(context)


def test_operation_reminder_keeps_only_the_three_highest_priority_actions() -> None:
    """防止多个条件命中时重新刷屏，且同一基金不得重复占用提醒位置。"""
    dividend = {**_fund_position(), "instrumentId": "fund-dividend", "symbol": "110023", "name": "分红基金"}
    valuation = {**_fund_position(), "instrumentId": "fund-valuation", "symbol": "110024", "name": "估值基金", "changePct": "-2.50", "changeQuality": "ESTIMATED"}
    concentration = {**_fund_position(), "instrumentId": "fund-concentration", "symbol": "110025", "name": "集中基金", "weight": "35.00"}
    streak = {**_fund_position(), "instrumentId": "fund-streak", "symbol": "110026", "name": "连跌基金", "trendDirection": "DOWN", "trendDays": 4, "trendChangePct": "-5.10", "trendQuality": "FRESH"}
    context = _reminder_context(positions=[dividend, valuation, concentration, streak])
    context["summary"] = {"totalAsset": "100000", "cashBalance": "3000"}
    context["preMarket"]["corporateActions"] = [
        {
            "instrumentId": "fund-dividend",
            "symbol": "110023",
            "name": "分红基金",
            "actionType": "FUND_DIVIDEND",
            "actionDate": "2026-09-01",
            "exDate": "2026-09-01",
        }
    ]

    reminder = pre_market.operation_reminder_text(context)
    assert reminder.count("；") == 2
    assert "分红基金(110023)今日基金分红除息" in reminder
    assert "估值基金(110024)预估下跌2.50%" in reminder
    assert "集中基金(110025)仓位35.00%" in reminder
    assert "连跌基金" not in reminder
    assert "现金占比" not in reminder


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


@pytest.mark.asyncio
async def test_fund_profiles_decodes_json_metadata_returned_as_text() -> None:
    """A JSONB text codec must not make either report generator crash."""

    conn = _CoverageConnection(
        [
            {
                "instrument_id": "11111111-1111-1111-1111-111111111111",
                "symbol": "110022",
                "name": "易方达消费行业",
                "purchase_status": "OPEN",
                "redemption_status": "SUSPENDED",
                "source_ref": "tiantian:fund_purchase_em",
                "source_metadata": '{"purchaseRaw":"开放申购","redemptionRaw":"暂停赎回"}',
                "retrieved_at": datetime(2026, 9, 10, 6, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
            }
        ]
    )

    profiles = await repository.get_pre_market_fund_profiles(
        conn,
        ["11111111-1111-1111-1111-111111111111"],
        date(2026, 9, 10),
    )

    assert profiles == [
        {
            "instrumentId": "11111111-1111-1111-1111-111111111111",
            "symbol": "110022",
            "name": "易方达消费行业",
            "purchaseStatus": "OPEN",
            "redemptionStatus": "SUSPENDED",
            "sourceRef": "tiantian:fund_purchase_em",
            "sourceMetadata": {
                "purchaseRaw": "开放申购",
                "redemptionRaw": "暂停赎回",
            },
            "retrievedAt": "2026-09-10T06:05:00+08:00",
        }
    ]


# --------------------------------------------------------------------------- #
# 今日焦点事件：公告 > 除权 > 宏观，只展示今天，每条 ≤ 30 字
# --------------------------------------------------------------------------- #


def _focus_context(
    *, announcements=(), actions=(), economics=(), report_date=""
) -> dict:
    return {
        "positions": [
            {
                "instrumentId": "a",
                "symbol": "600036",
                "name": "招商银行",
                "type": "A股",
                "quantity": "100",
                "weight": "30.00",
            },
            {
                "instrumentId": "b",
                "symbol": "600519",
                "name": "贵州茅台",
                "type": "A股",
                "quantity": "10",
                "weight": "60.00",
            },
        ],
        "marketSentiment": {"qualityStatus": "FRESH", "totalUp": 100, "totalDown": 100},
        "preMarket": {
            "reportDate": report_date,
            "announcements": list(announcements),
            "corporateActions": list(actions),
            "economicEvents": list(economics),
            "usIndices": [],
            "a50Future": None,
            "subscriptionRedemptionChanges": [],
            "fundCutoffRules": [],
            "fundProfiles": [],
        },
    }


def test_focus_prioritises_announcement_over_dividend_over_macro() -> None:
    """优先级：公告 > 除权 > 宏观；公告内按持仓权重降序。"""
    context = _focus_context(
        report_date="2026-09-02",
        announcements=[
            {
                "instrumentId": "a",
                "symbol": "600036",
                "name": "招商银行",
                "title": "招商银行2026年半年度报告",
                "date": "2026-09-02",
            },
            {
                "instrumentId": "b",
                "symbol": "600519",
                "name": "贵州茅台",
                "title": "贵州茅台关于分红方案的公告",
                "date": "2026-09-02",
            },
        ],
        actions=[
            {
                "instrumentId": "a",
                "symbol": "600036",
                "name": "招商银行",
                "actionType": "CASH_DIVIDEND",
                "exDate": "2026-09-02",
            }
        ],
        economics=[
            {
                "name": "中国制造业PMI",
                "title": "中国制造业PMI",
                "value": "49.5",
                "unit": "%",
                "date": "2026年8月",
            }
        ],
    )
    phrases = pre_market._focus_event_phrases(context, limit=3)
    assert phrases == [
        "贵州茅台发布关于分红方案的公告",  # 权重更高 → 排最前
        "招商银行发布2026年半年度报告",
        "招商银行今日除权，预计低开",
    ]


def test_focus_drops_non_today_actions_and_limits_phrases() -> None:
    """非今日除权不计入焦点；每条焦点 ≤ 30 字。"""
    context = _focus_context(
        report_date="2026-09-02",
        actions=[
            {
                "instrumentId": "a",
                "symbol": "600036",
                "name": "招商银行",
                "actionType": "CASH_DIVIDEND",
                "exDate": "2026-06-18",  # 非今日 → 排除
            }
        ],
        economics=[
            {
                "name": "中国CPI同比",
                "title": "中国CPI同比",
                "value": "0.5",
                "unit": "%",
                "date": "2026年07月",
            }
        ],
    )
    phrases = pre_market._focus_event_phrases(context, limit=3)
    assert phrases == ["中国CPI同比（2026年07月）0.5%（已发布）"]
    assert all(len(phrase) <= 30 for phrase in phrases)


def test_focus_none_message_when_no_events() -> None:
    assert pre_market.focus_event_text(_focus_context()) == "今日暂无重大事件关注"


def test_focus_announcement_title_keeps_company_short_form() -> None:
    """标题去掉公司名前缀后直述，不加工任何数字。"""
    context = _focus_context(
        report_date="2026-09-02",
        announcements=[
            {
                "instrumentId": "b",
                "symbol": "600519",
                "name": "贵州茅台",
                "title": "贵州茅台2026年半年度报告",
                "date": "2026-09-02",
            }
        ],
    )
    assert pre_market.focus_event_text(context) == "贵州茅台发布2026年半年度报告"


# --------------------------------------------------------------------------- #
# 操作参考：规则引擎（除权 > 公告 > 估值过期 > 基金申赎），同标取最高
# --------------------------------------------------------------------------- #


def _advice_context(
    *,
    positions=None,
    actions=(),
    announcements=(),
    profiles=(),
    rules=(),
    report_date="",
) -> dict:
    return {
        "positions": positions
        if positions is not None
        else [
            {
                "instrumentId": "a",
                "symbol": "600036",
                "name": "招商银行",
                "type": "A股",
                "quantity": "100",
                "quality": "FRESH",
                "weight": "30.00",
            },
            {
                "instrumentId": "b",
                "symbol": "110022",
                "name": "易方达消费行业",
                "type": "场外基金",
                "quantity": "1000",
                "quality": "FRESH",
                "weight": "8.00",
            },
        ],
        "marketSentiment": {"qualityStatus": "FRESH"},
        "preMarket": {
            "reportDate": report_date,
            "corporateActions": list(actions),
            "announcements": list(announcements),
            "fundProfiles": list(profiles),
            "fundCutoffRules": list(rules),
            "usIndices": [],
            "a50Future": None,
            "economicEvents": [],
            "subscriptionRedemptionChanges": [],
        },
    }


def test_advice_keeps_only_highest_priority_event_per_instrument() -> None:
    """同一标的（除权+公告+估值过期）只保留优先级最高的除权。"""
    context = _advice_context(
        report_date="2026-09-02",
        actions=[
            {
                "instrumentId": "a",
                "symbol": "600036",
                "name": "招商银行",
                "actionType": "CASH_DIVIDEND",
                "exDate": "2026-09-02",
            }
        ],
        announcements=[
            {
                "instrumentId": "a",
                "symbol": "600036",
                "name": "招商银行",
                "title": "招商银行2026年半年度报告",
                "date": "2026-09-02",
            }
        ],
    )
    context["positions"][0]["quality"] = "STALE"
    assert pre_market.operation_advice_text(context) == (
        "招商银行今日除权，若除权后价格偏低，可关注填权行情"
    )


def test_advice_fund_suspension_and_deadline() -> None:
    """基金暂停申购 → 建议勿操作；正常+截止 → 留意截止时间。"""
    suspended = _advice_context(
        positions=[
            {
                "instrumentId": "f",
                "symbol": "110022",
                "name": "易方达消费行业",
                "type": "场外基金",
                "quantity": "1",
            }
        ],
        profiles=[
            {
                "instrumentId": "f",
                "symbol": "110022",
                "name": "易方达消费行业",
                "purchaseStatus": "SUSPENDED",
                "redemptionStatus": "OPEN",
            }
        ],
    )
    assert (
        "易方达消费行业今日暂停申购，建议暂勿操作"
        in pre_market.operation_advice_text(suspended)
    )

    normal = _advice_context(
        positions=[
            {
                "instrumentId": "f",
                "symbol": "110022",
                "name": "易方达消费行业",
                "type": "场外基金",
                "quantity": "1",
            }
        ],
        rules=[
            {
                "instrumentId": "f",
                "symbol": "110022",
                "name": "易方达消费行业",
                "salesChannel": "DEFAULT",
                "cutoffTime": "15:00:00",
                "timezone": "Asia/Shanghai",
            }
        ],
    )
    assert (
        "易方达消费行业今日15:00前可申赎，请留意截止时间"
        in pre_market.operation_advice_text(normal)
    )


def test_advice_fallback_watch_and_no_data() -> None:
    """有持仓无触发 → 保持观望；无持仓（数据不足）→ 今日无明确操作参考。"""
    with_positions = _advice_context(
        positions=[
            {
                "instrumentId": "a",
                "symbol": "600036",
                "name": "招商银行",
                "type": "A股",
                "quantity": "1",
            }
        ]
    )
    assert (
        pre_market.operation_advice_text(with_positions)
        == "今日无明确操作参考，可保持观望"
    )
    assert (
        pre_market.operation_advice_text(_advice_context(positions=[]))
        == "今日无明确操作参考"
    )


def test_advice_limits_to_two_and_caps_phrase_length() -> None:
    """最多输出 2 条、每条 ≤ 30 字。"""
    context = _advice_context(
        report_date="2026-09-02",
        actions=[
            {
                "instrumentId": "a",
                "symbol": "600036",
                "name": "招商银行",
                "actionType": "CASH_DIVIDEND",
                "exDate": "2026-09-02",
            }
        ],
        announcements=[
            {
                "instrumentId": "b",
                "symbol": "600519",
                "name": "贵州茅台",
                "title": "贵州茅台关于回购股份的进展公告",
                "date": "2026-09-02",
            }
        ],
        rules=[
            {
                "instrumentId": "110022-fund",
                "symbol": "110022",
                "name": "易方达消费行业",
                "salesChannel": "DEFAULT",
                "cutoffTime": "15:00:00",
                "timezone": "Asia/Shanghai",
            }
        ],
    )
    text = pre_market.operation_advice_text(context, limit=2)
    assert text.count("；") <= 1
    assert all(len(part) <= 30 for part in text.split("；"))
