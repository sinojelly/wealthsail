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
