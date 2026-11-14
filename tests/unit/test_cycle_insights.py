"""Optional cycle insights enrich research without becoming an acceptance gate."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.macro.daily_research import (
    build_prompt,
    report_schema_for_packet,
    validate_report,
)

NOW = datetime(2026, 11, 13, 7, 8, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_cycle_research_tasks_reach_model_without_invented_current_quotes():
    from backend.integrations.daily_sources import macro_cycle_research_tasks
    from backend.macro.model_projection import model_packet

    tasks = macro_cycle_research_tasks(NOW)
    assert tasks["version"] == "macro-cycle-research-v1"
    assert len(tasks["queries"]) == 3
    assert "CME" in str(tasks) and "美债" in str(tasks) and "财政" in str(tasks)
    assert "100%" not in str(tasks)
    assert model_packet({"macroCycleResearch": tasks})["macroCycleResearch"] == tasks
    prompt = build_prompt({"asOf": NOW.isoformat()})
    assert "macroCycleResearch" in prompt
    assert "市场预期" in prompt and "不决定牛熊" in prompt


def test_optional_cycle_envelope_never_becomes_a_schema_gate():
    schema = report_schema_for_packet({})
    assert "cycleInsights" in schema["properties"]
    assert "cycleInsights" not in schema["required"]
    for raw in [None, [], 42, "结构偏差仍保留宏观总纲", {"marketRegime": "震荡整理"}]:
        report = validate_report(
            {"summary": "主报告保留", "cycleInsights": raw}, {}, {}, completed_at=NOW
        )
        assert report["executionStatus"] == "COMPLETED"
        assert report["summary"] == "主报告保留"


def test_cycle_insights_preserve_custom_industry_and_separate_expectation_from_fact():
    original = {
        "summary": "完整报告",
        "sources": [
            {
                "id": "fed",
                "url": "https://www.federalreserve.gov/monetarypolicy/openmarket.htm",
                "title": "政策利率",
                "quote": "政策利率目标区间",
                "publishedDate": "2026-11-12",
            }
        ],
        "cycleInsights": {
            "globalLiquidity": {
                "stage": "高利率平台期",
                "summary": "会议前期货定价发生变化",
                "evidenceKind": "MARKET_EXPECTATION",
                "asOf": "2026-11-12",
                "sourceIds": ["fed", "missing"],
            },
            "domesticCycle": "政策托底、信用修复仍需观察",
            "marketRegime": {
                "stage": "结构分化",
                "summary": "看盈利与资金面",
                "action": "按实际买区分批观察",
            },
            "keyFactors": [
                {
                    "title": "下次议息预期",
                    "summary": "区分预期与正式决议",
                    "evidenceKind": "MARKET_EXPECTATION",
                },
                "长端利率变化",
            ],
            "industries": [
                {
                    "industryId": "NEW_CHIP",
                    "label": "新型芯片",
                    "stage": "订单扩张",
                    "driver": "真实订单仍需兑现",
                    "sourceIds": ["fed"],
                }
            ],
        },
    }
    report = validate_report(original, {}, {}, completed_at=NOW)
    cycle = report["cycleInsights"]
    assert cycle["globalLiquidity"]["evidenceKind"] == "MARKET_EXPECTATION"
    assert cycle["globalLiquidity"]["asOf"] == "2026-11-12"
    assert cycle["globalLiquidity"]["sourceIds"] == [report["sources"][0]["id"]]
    assert cycle["globalLiquidity"]["factVerification"] == "MODEL_UNVERIFIED"
    assert cycle["domesticCycle"]["summary"] == "政策托底、信用修复仍需观察"
    assert cycle["marketRegime"]["action"] == "按实际买区分批观察"
    assert len(cycle["keyFactors"]) == 2
    assert cycle["industries"][0]["industryId"] == "NEW_CHIP"
    assert original["cycleInsights"]["globalLiquidity"]["sourceIds"] == [
        "fed",
        "missing",
    ]


@pytest.mark.parametrize("invalid", ["2999-01-01", "not-a-date", {"wrong": "type"}])
def test_cycle_time_defects_clear_only_clock_and_keep_view(invalid):
    report = validate_report(
        {
            "cycleInsights": {
                "globalLiquidity": {"summary": "完整保留周期观点", "asOf": invalid}
            }
        },
        {},
        {},
        completed_at=NOW,
    )
    assert report["cycleInsights"]["globalLiquidity"]["summary"] == "完整保留周期观点"
    assert report["cycleInsights"]["globalLiquidity"]["asOf"] is None
    assert any(
        row["path"] == "cycleInsights.globalLiquidity.asOf"
        for row in report["validationWarnings"]
    )


def test_legacy_report_does_not_acquire_fabricated_cycle_assessment():
    report = validate_report(
        {"summary": "原有观点", "macroCycle": "政策托底阶段"}, {}, {}, completed_at=NOW
    )
    assert "cycleInsights" not in report
    assert report["macroCycle"] == "政策托底阶段"


def test_saved_cycle_projection_uses_only_same_report_sources_and_prices():
    from backend.macro.cycle_insights import project_cycle_insights

    def note(content, as_of="2026-11-12"):
        return {
            "title": "隔夜全球要闻",
            "content": content,
            "asOf": as_of,
            "sourceUri": "https://www.cls.cn/detail/123",
        }

    def fund(code, value):
        return {
            "fundCode": code,
            "priceBasis": "UNIT_NAV",
            "currentPrice": value,
            "indicators": {"navPercentile2y": 15},
            "tracks": [dict(entryZoneMin=1, entryZoneMax=1.1, sellZoneMin=1.3)],
        }

    report = {
        "valueDirections": [
            {
                "title": "银行",
                "thesis": "分红预期支撑",
                "plans": [fund("008298", 1.4)],
                "industryCycle": {
                    "stage": "UNKNOWN",
                    "evidenceSummary": "旧报告未执行产业周期反方核查",
                },
            },
            {
                "title": "消费",
                "quantitativeFundCodes": ["001632"],
                "catalysts": [{"summary": "需求回补"}],
            },
        ],
        "quantitativeScan": {"items": [fund("004069", 1.5), fund("001632", 1.02)]},
    }
    packet = {
        "alphaPublic": {
            "catalysts": [
                note(
                    "哈马克称美联储可能加息。英国第三季度GDP增长。高盛预计次年全球GDP增长2.8%"
                ),
                note("美联储公布降息", "2026-11-14"),
            ]
        },
        "series": [
            {
                "code": "US_T2_NOMINAL",
                "state": "READY",
                "value": 4.55,
                "asOf": "2026-11-12",
                "changes": {"change5Samples": 32, "changeUnit": "bp"},
            },
            {
                "code": "US_FED_FUNDS",
                "state": "READY",
                "value": 4.1,
                "asOf": "2026-11-12",
            },
        ],
    }
    cycle = project_cycle_insights(report, packet, NOW)["cycleInsights"]
    assert "cycleInsights" not in report
    assert {row["kind"] for row in cycle["policySignals"]} == {
        "CENTRAL_BANK_REMARK",
        "INSTITUTION_FORECAST",
    }
    assert [row["fundCode"] for row in cycle["industries"]] == ["008298", "001632"]
    assert [row["stage"] for row in cycle["industries"]] == [
        "高位兑现·入卖区",
        "低分位·已入买区",
    ]
    assert cycle["industries"][0]["driver"] == "分红预期支撑"
    assert cycle["rateExpectation"]["spreadBp"] == 45
    assert cycle["rateExpectation"]["us2yChange5Bp"] == 32
    assert len(cycle["macroSnapshot"]["series"]) == 2


def test_frozen_cycle_history_excludes_later_data_and_does_not_enter_model_request():
    from backend.macro.daily_research import make_research_packet
    from backend.macro.model_projection import model_packet
    from backend.macro.cycle_insights import project_cycle_insights

    pmi = {
        "code": "PMI",
        "value": 49.3,
        "asOf": "2026-10-01",
        "history": [
            {
                "asOf": "2026-09-01",
                "value": 50.1,
                "retrievedAt": "2026-10-01T09:00:00+08:00",
            },
            {
                "asOf": "2026-10-01",
                "value": 49.3,
                "retrievedAt": "2026-11-14T09:00:00+08:00",
            },
        ],
    }
    packet = make_research_packet({"items": [pmi]}, [], [], [], now=NOW)
    assert [row["value"] for row in packet["macros"][0]["history"]] == [50.1]
    assert "history" not in model_packet(packet)["macros"][0]
    chart = project_cycle_insights({}, packet, NOW)["cycleInsights"]["macroSnapshot"]
    assert [row["value"] for row in chart["items"][0]["history"]] == [50.1]
