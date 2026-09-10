"""Deterministic FUND_1430 report contracts for persisted real-data estimates."""

from __future__ import annotations

from types import SimpleNamespace

from backend.reports import service as report_service


FUND_A_ID = "11111111-1111-1111-1111-111111111111"
FUND_B_ID = "22222222-2222-2222-2222-222222222222"
FUND_C_ID = "33333333-3333-3333-3333-333333333333"


def _generator():
    module = getattr(report_service, "fund_1430", None)
    assert module is not None, "FUND_1430 deterministic generator is not implemented"
    return module


def _context() -> dict:
    return {
        "positions": [
            {
                "instrumentId": FUND_A_ID,
                "symbol": "110011",
                "name": "易方达消费中",
                "type": "场外基金",
                "quantity": "1000",
            },
            {
                "instrumentId": FUND_B_ID,
                "symbol": "161725",
                "name": "招商中证白酒",
                "type": "场外基金",
                "quantity": "800",
            },
        ],
        # These are Worker-persisted ESTIMATED_NAV observations. Their metadata
        # comes from the real disclosure and quote snapshot used by the estimator.
        "fundEstimates": [
            {
                "instrumentId": FUND_A_ID,
                "symbol": "110011",
                "name": "易方达消费中",
                "value": "1.0410",
                "lowerValue": "1.0380",
                "upperValue": "1.0450",
                "quality": "ESTIMATED",
                "asOf": "2026-09-01T14:30:00+08:00",
                "snapshotId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "metadata": {
                    "coveragePct": "72.50",
                    "disclosedOn": "2026-06-30",
                    "confirmedNav": "1.0000",
                    "confirmedNavAsOf": "2026-08-31T00:00:00+08:00",
                },
            },
            {
                "instrumentId": FUND_B_ID,
                "symbol": "161725",
                "name": "招商中证白酒",
                "value": "0.9600",
                "lowerValue": "0.9550",
                "upperValue": "0.9660",
                "quality": "STALE",
                "asOf": "2026-09-01T14:29:00+08:00",
                "snapshotId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "metadata": {
                    "coveragePct": "68.00",
                    "disclosedOn": "2026-03-31",
                    "confirmedNav": "1.0000",
                    "confirmedNavAsOf": "2026-08-31T00:00:00+08:00",
                },
            },
        ],
        "fund1430": {
            "fundCutoffRules": [
                {
                    "instrumentId": FUND_A_ID,
                    "symbol": "110011",
                    "name": "易方达消费中",
                    "salesChannel": "DEFAULT",
                    "cutoffTime": "14:45:00",
                    "timezone": "Asia/Shanghai",
                }
            ],
            "fundProfiles": [],
        },
    }


def test_external_provider_estimate_explains_source_without_fake_coverage() -> None:
    generator = _generator()
    context = _context()
    context["fundEstimates"] = [
        {
            "instrumentId": FUND_A_ID,
            "symbol": "110011",
            "name": "易方达消费中",
            "value": "1.0200",
            "lowerValue": None,
            "upperValue": None,
            "quality": "ESTIMATED",
            "asOf": "2026-09-07T14:30:00+08:00",
            "snapshotId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "methodVersion": "sina/fund-estimate/v1",
            "metadata": {
                "estimateSourceKind": "EXTERNAL_PROVIDER",
                "estimateSource": "Sina-Fund-Estimate",
                "confirmedNav": "1.0000",
                "confirmedNavAsOf": "2026-09-04T00:00:00+00:00",
            },
        }
    ]

    report = generator.build_report(context)

    confidence = next(
        item["body"] for item in report["items"] if item["title"] == "估值可信度"
    )
    assert "盘中估算，非最终净值" in confidence
    assert "不提供持仓覆盖率" not in confidence
    assert "估值截至" not in confidence
    assert "来源不提供区间" not in report["summary"]
    assert "所有基金估值来源：新浪财经，仅供参考。" in report["summary"]


def test_non_trading_day_report_labels_latest_trading_day_as_historical() -> None:
    generator = _generator()
    context = _context()
    estimate = context["fundEstimates"][0]
    estimate["asOf"] = "2026-09-04T16:04:00+08:00"
    estimate["quality"] = "ESTIMATED"
    estimate["metadata"].update(
        {
            "valuationDate": "2026-09-04",
            "latestTradingDate": "2026-09-04",
            "valuationDateKind": "LATEST_TRADING_DAY",
            "nonTradingDayReference": True,
        }
    )
    context["fundEstimates"] = [estimate]

    report = generator.build_report(context)
    push = generator.format_push_content(context)

    assert report["items"][0]["title"] == "最近交易日估值概览"
    assert "2026-09-04" in report["summary"]
    assert "非交易日历史参考" in report["summary"]
    assert "今日预估" not in report["summary"]
    assert "09-04" in push


def test_report_uses_persisted_estimates_and_preserves_evidence() -> None:
    draft = _generator().build_report(_context())

    assert [item["title"] for item in draft["items"]] == [
        "今日估值概览",
        "最大亏损 Top 3",
        "最大盈利标的",
        "估值可信度",
        "申赎提醒",
    ]
    assert "基金估值（盘中估算，非最终净值" in draft["summary"]
    assert "| 基金名称 | 代码 | 当日预估 | 市值 | 累计盈亏 | 连续变化 |" in draft["summary"]
    assert "| 易方达消费中 | 110011 | +4.10% |" in draft["summary"]
    assert "来源不提供区间" not in draft["summary"]
    assert "覆盖率72.50%" not in draft["summary"]
    assert "基于2026-06-30披露持仓" not in draft["summary"]
    assert "14:45" in draft["summary"]
    assert "15:00" not in draft["summary"]
    assert "正常申赎" not in draft["summary"]
    assert all("估值明细" not in item["title"] for item in draft["items"])


def test_subscription_reminder_uses_verified_status_and_never_assumes_open() -> None:
    """A missing profile must remain explicit while real states reach both views."""

    context = _context()
    context["positions"].append(
        {
            "instrumentId": FUND_C_ID,
            "symbol": "001632",
            "name": "天弘中证食品饮料",
            "type": "场外基金",
            "quantity": "600",
        }
    )
    context["fund1430"]["fundProfiles"] = [
        {
            "instrumentId": FUND_A_ID,
            "symbol": "110011",
            "name": "易方达消费中",
            "purchaseStatus": "OPEN",
            "redemptionStatus": "OPEN",
        },
        {
            "instrumentId": FUND_B_ID,
            "symbol": "161725",
            "name": "招商中证白酒",
            "purchaseStatus": "OPEN",
            "redemptionStatus": "SUSPENDED",
        },
    ]

    report = _generator().build_report(context)
    reminder = next(item["body"] for item in report["items"] if item["title"] == "申赎提醒")
    push = _generator().format_push_content(context)

    expected_open = (
        "易方达消费中(110011)：状态正常；默认渠道14:45截止参考"
        "（天天基金状态，实际渠道为准）"
    )
    expected_suspended = "招商中证白酒(161725)：暂停赎回（天天基金状态，实际渠道为准）"
    expected_unknown = "天弘中证食品饮料(001632)：未取得可核验状态（天天基金状态未取得，实际渠道为准）"
    assert expected_open in reminder
    assert expected_suspended in reminder
    assert expected_unknown in reminder
    assert expected_suspended in push
    assert expected_unknown in push


def test_report_ignores_legacy_approximate_lookthrough_seed() -> None:
    context = _context()
    context["fund1430"].update(
        {
            "fundLookthrough": [
                {
                    "fundInstrumentId": FUND_A_ID,
                    "fundSymbol": "110011",
                    "fundName": "易方达消费中",
                    "holdingSymbol": "600519",
                    "weightPct": "100",
                    "reportPeriod": "2026-06-30",
                }
            ],
            "holdingQuoteBySymbol": {
                "600519": SimpleNamespace(symbol="600519", return_rate="99")
            },
        }
    )

    draft = _generator().build_report(context)

    assert "| 易方达消费中 | 110011 | +4.10% |" in draft["summary"]
    assert "99.00%" not in draft["summary"]


def test_quality_reflects_persisted_estimate_quality() -> None:
    draft = _generator().build_report(_context())
    confidence = next(item for item in draft["items"] if item["title"] == "估值可信度")

    assert "质量ESTIMATED" not in confidence["body"]
    assert "质量STALE" not in confidence["body"]
    assert "其中1只采用盘中估算" in confidence["body"]
    assert confidence["quality"] == "STALE"


def test_exact_three_percent_does_not_trigger_action_threshold() -> None:
    context = _context()
    context["fundEstimates"] = [
        {
            **context["fundEstimates"][0],
            "value": "1.0300",
            "lowerValue": "1.0200",
            "upperValue": "1.0400",
        },
        {
            **context["fundEstimates"][1],
            "value": "0.9700",
            "lowerValue": "0.9600",
            "upperValue": "0.9800",
        },
    ]

    draft = _generator().build_report(context)
    overview = next(item for item in draft["items"] if item["title"] == "今日估值概览")

    assert "未触发±3%估值偏离阈值" in overview["body"]
    assert all(item["title"] != "总结" for item in draft["items"])


def test_missing_data_is_explicit_and_never_fabricated() -> None:
    context = {"positions": [], "fundEstimates": [], "fund1430": {}}

    draft = _generator().build_report(context)
    push = _generator().format_push_content(context)
    rendered = f"{draft['summary']}\n{push}"

    assert "数据截至 暂无" in draft["summary"]
    assert "基金估值：数据不足。" in draft["summary"]
    assert "申赎提醒：暂无。" in draft["summary"]
    assert "None" not in rendered
    assert "null" not in rendered
    assert "--" not in rendered
    assert len(push) <= 200


def test_push_uses_persisted_estimate_and_remains_compact() -> None:
    push = _generator().format_push_content(_context())

    lines = push.splitlines()
    assert lines[0].startswith("📊 今日基金估值（数据截至 ")
    assert "易方达消费中(110011)（估）预估 +4.10%" in push
    assert "来源不提供区间" not in push
    assert "质量ESTIMATED" not in push
    assert "+4.10%" in push
    assert "14:45" in push
    assert "15:00" not in push
    assert "正常申赎" not in push
    assert len(push) <= 200


def test_push_uses_double_dash_when_official_nav_history_is_not_yet_sufficient() -> None:
    """Missing historical NAV must not be described as an estimated trend fact."""
    context = _context()
    context["fundTrends"] = {}

    push = _generator().format_push_content(context)

    assert "预估 +4.10% -- 盈亏 数据不足" in push


def test_push_formats_fund_estimates_once_with_trend_and_unrealized_pnl() -> None:
    """企微基金估值逐条只保留决策所需的增量信息。"""
    context = _context()
    context["positions"] = [
        {
            "instrumentId": FUND_A_ID,
            "symbol": "110011",
            "name": "易方达消费中",
            "type": "场外基金",
            "quantity": "1000",
            "avgCost": "1.0200",
        },
        {
            "instrumentId": FUND_B_ID,
            "symbol": "161725",
            "name": "招商中证白酒",
            "type": "场外基金",
            "quantity": "800",
            "avgCost": "1.0000",
        },
    ]
    for estimate in context["fundEstimates"]:
        estimate["asOf"] = "2026-09-09T11:30:00+08:00"
        estimate["metadata"].update(
            {
                "estimateSourceKind": "EXTERNAL_PROVIDER",
                "estimateSource": "新浪财经",
            }
        )
    context["fundTrends"] = {
        FUND_A_ID: "📉连跌2天 (-2.10%)",
        FUND_B_ID: "📈转涨",
    }

    push = _generator().format_push_content(context)

    assert push.startswith("📊 今日基金估值（数据截至 2026-09-09 11:30）：")
    assert "申赎提醒：易方达消费中(110011)：未取得可核验状态" in push
    assert "招商中证白酒(161725)：未取得可核验状态" in push
    assert "所有基金估值来源：新浪财经，仅供参考。" in push
    assert "估值截至2026-09-09 11:30" not in push
    assert "质量ESTIMATED" not in push
    assert "来源不提供区间" not in push


def test_audit_summary_uses_structured_fund_table_without_repeated_metadata() -> None:
    """网页审计摘要提供基金表格；企微仍由独立文本格式器负责。"""
    context = _context()
    context["positions"] = [
        {
            "instrumentId": FUND_A_ID,
            "symbol": "110011",
            "name": "易方达消费中",
            "type": "场外基金",
            "quantity": "1000",
            "avgCost": "1.0200",
        },
        {
            "instrumentId": FUND_B_ID,
            "symbol": "161725",
            "name": "招商中证白酒",
            "type": "场外基金",
            "quantity": "800",
            "avgCost": "1.0000",
        },
    ]
    for estimate in context["fundEstimates"]:
        estimate["asOf"] = "2026-09-09T14:51:00+08:00"
        estimate["metadata"].update(
            {
                "estimateSourceKind": "EXTERNAL_PROVIDER",
                "estimateSource": "新浪财经",
            }
        )
    context["fundTrends"] = {
        FUND_A_ID: "📉连跌2天(-2.10%)",
        FUND_B_ID: "📉连跌3天(-3.20%)",
    }

    report = _generator().build_report(context)

    assert report["summary"].startswith(
        "📊 今日基金估值（盘中估算，非最终净值；数据截至 14:51）："
    )
    assert "今日整体预估均值：+0.05%；上涨1只、下跌1只、平盘0只。" in report["summary"]
    assert "基金估值（盘中估算，非最终净值" in report["summary"]
    assert "| 基金名称 | 代码 | 当日预估 | 市值 | 累计盈亏 | 连续变化 |" in report["summary"]
    assert "| 招商中证白酒 | 161725 | -4.00% | 768.00 | -32.00 | 📉连跌3天(-3.20%) |" in report["summary"]
    assert "| 易方达消费中 | 110011 | +4.10% | 1,041.00 | +21.00 | 📉连跌2天(-2.10%) |" in report["summary"]
    assert report["summary"].index("| 招商中证白酒") < report["summary"].index(
        "| 易方达消费中"
    )
    assert "所有基金估值来源：新浪财经，仅供参考。" in report["summary"]
    assert "来源不提供区间" not in report["summary"]
    assert "估值截至" not in report["summary"]
    assert "最近正式净值截至" not in report["summary"]
    assert "不提供持仓覆盖率，质量 ESTIMATED" not in report["summary"]


def test_report_compacts_fund_item_cards_and_keeps_estimate_evidence() -> None:
    """A fund table replaces per-fund cards without discarding source snapshots."""

    context = _context()
    report = _generator().build_report(context)

    assert [item["title"] for item in report["items"]] == [
        "今日估值概览",
        "最大亏损 Top 3",
        "最大盈利标的",
        "估值可信度",
        "申赎提醒",
    ]
    assert "易方达消费中" not in report["items"][0]["body"]
    assert "招商中证白酒(161725) -4.00%" in report["items"][1]["body"]
    assert "易方达消费中(110011) +4.10%" in report["items"][2]["body"]
    assert "盘中估算，非最终净值" in report["items"][3]["body"]
    assert all("暂无" not in item["body"] for item in report["items"])
    assert report["items"][0]["_evidence"]["source_snapshot_id"] == (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )
