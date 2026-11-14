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


def test_duplicate_policy_excerpts_keep_distinct_people_stances_dates_and_odds():
    from backend.macro.cycle_insights import policy_signals

    def source(title, content, time="2026-11-13T03:45:00+08:00"):
        return {
            "title": title,
            "content": content,
            "publishedAt": time,
            "sourceUri": "https://www.cls.cn/detail/123",
        }

    meeting = "美联储12月加息50个基点的概率为15.3%"
    draft = {
        "sourceItems": [
            source(
                "周五隔夜要闻",
                "9、克利夫兰联储行长贝丝·哈马克重申，在人工智能投资所带来的需求压力下，需要提高利率以抗击通胀",
            ),
            source(
                "克利夫兰联储行长称通胀正朝错误方向发展 利率需更具限制性",
                "【克利夫兰联储行长称通胀正朝错误方向发展 利率需更具限制性】财联社11月13日电，克利夫兰联储行长贝丝·哈马克重申，她认为在人工智能投资所带来的需求压力下，需要提高利率以抗击通胀。上月美联储决定将利率维持在3.75%-4%的区间不变，哈马克是异议官员之一。",
            ),
            source(
                "戴利称可能进一步加息",
                "10、旧金山联储行长玛丽·戴利表示，可能需要进一步加息以降低通胀，并称越来越多的企业正在考虑涨价。",
            ),
            source("隔夜观点", "哈马克指出，若经济继续走弱可能降息"),
            source("隔夜观点", "哈马克指出若工资增速持续超预期，可能进一步加息"),
            source(
                "隔夜观点",
                "哈马克指出人工智能投资需求压力需要提高利率",
                "2026-11-12T11:00:00+08:00",
            ),
            source(
                "FOMC时程",
                "美联储更新了2027年联邦公开市场委员会（FOMC）的会议日程，取消了10月的利率会议安排",
            ),
            source(
                meeting,
                f"【{meeting}】财联社11月13日电，据CME“美联储观察”：美联储12月加息25个基点的概率为84.7%，加息50个基点的概率为15.3%。美联储到明年1月累计加息25个基点的概率为24.4%，累计加息50个基点的概率为64.7%，累计加息75个基点的概率为10.9%",
            ),
            source(
                "据CME观察",
                "美联储12月加息25个基点的概率为80%，加息50个基点的概率为20%",
                "2026-11-13T06:45:00+08:00",
            ),
        ]
    }
    rows = policy_signals(draft, {}, NOW)
    remarks = [row for row in rows if row["kind"] == "CENTRAL_BANK_REMARK"]
    assert len(remarks) == 5
    assert (
        sum("哈马克" in row["text"] and "提高利率" in row["text"] for row in remarks)
        == 2
    )
    assert any("可能降息" in row["text"] for row in remarks)
    assert any("工资增速" in row["text"] for row in remarks)
    assert any("戴利" in row["text"] for row in remarks)
    odds = [row["text"] for row in rows if row["kind"] == "MARKET_RATE_ODDS"]
    assert len(odds) == 3
    assert any("84.7%" in line and "15.3%" in line for line in odds)
    assert any("明年1月" in line and "10.9%" in line for line in odds)
    assert any("80%" in line and "20%" in line for line in odds)


def test_fedwatch_aggregates_full_distributions_by_meeting_and_snapshot():
    from backend.macro.cycle_insights import project_cycle_insights

    def item(content, clock, title="据CME FedWatch"):
        return {
            "title": title,
            "content": content,
            "publishedAt": clock,
            "sourceUri": "https://www.cls.cn/detail/123",
        }

    packet = {
        "source_items": [
            item("12月加息50bp概率15.3%", "2026-11-13T06:45:00+08:00"),
            item(
                "12月加息25个基点概率84.7%，加息50个基点概率15.3%；到次年1月累计加息25bp概率24.4%，累计加息50bp概率64.7%，累计加息75bp概率10.9%。",
                "2026-11-13T06:00:00+08:00",
            ),
            item(
                "次年3月降息25个基点的概率为20%，维持利率不变的概率为70%，加息25bp的概率为10%。",
                "2026-11-13T06:01:00+08:00",
            ),
            item("12月降息50bp概率100%", "2026-11-13T08:00:00+08:00"),
            item("12月降息25bp概率100%", "2026-11-13T07:00:00+08:00", "隔夜要闻"),
            item("次年6月降息50bp概率25%", "2026-11-13T06:20:00+08:00"),
        ]
    }

    december, january, march, june = project_cycle_insights({}, packet, NOW)[
        "cycleInsights"
    ]["fedRateExpectations"]
    assert (
        december["meetingMonth"],
        december["hikeProbability"],
        december["expectedChangeBp"],
    ) == ("2026-12", 100, 28.8)
    assert december["mainOutcome"] == {"changeBp": 25, "probability": 84.7}
    assert december["asOf"] == "2026-11-13T06:00:00+08:00"
    assert (
        january["meetingMonth"],
        january["cumulative"],
        january["hikeProbability"],
        january["expectedChangeBp"],
    ) == ("2027-01", True, 100, 46.6)
    assert (
        march["cutProbability"],
        march["holdProbability"],
        march["hikeProbability"],
        march["expectedChangeBp"],
    ) == (20, 70, 10, -2.5)
    assert (june["complete"], june["expectedChangeBp"]) == (False, None)


def test_cycle_snapshot_uses_same_month_money_and_inflation_and_projects_audit_notes():
    from backend.macro.cycle_insights import project_cycle_insights

    def row(code, value, day, history=()):
        return {
            "code": code,
            "value": value,
            "asOf": day,
            "state": "READY",
            "history": [{"date": date, "value": value} for date, value in history],
        }

    frozen = {
        "macros": [
            row("PMI", 49.3, "2026-10-31"),
            row("CPI_YOY", 0.7, "2026-10-01"),
            row("PPI_YOY", 5.7, "2026-10-31"),
        ],
        "series": [
            row("M1_M2_GAP", -4, "2026-09-30"),
            row("M1_YOY", 4.4, "2026-10-31", [("2026-09-01", 3.1)]),
            row("M2_YOY", 7.1, "2026-10-01", [("2026-09-30", 7.1)]),
            row("US_T10_REAL", 2.91, "2026-11-12"),
        ],
    }
    report = {
        "macroCycle": "需求偏冷，外部利率高；M1新旧定义不跨断点串成趋势。9月M1-M2差为负。社融累计量不能替代信用脉冲。",
        "cycleInsights": {"globalLiquidity": {"summary": "油价高企。BLS抓取403。"}},
    }
    projected = project_cycle_insights(report, frozen, NOW)
    cycle = projected["cycleInsights"]
    assert projected["macroCycle"] == report["macroCycle"]
    assert cycle["displayMacroCycle"] == "需求偏冷，外部利率高；"
    assert cycle["globalLiquidity"]["summary"] == "油价高企。"
    assert set(cycle["auditNotes"]) == {
        "M1新旧定义不跨断点串成趋势",
        "社融累计量不能替代信用脉冲",
        "BLS抓取403",
    }
    snapshot = cycle["macroSnapshot"]
    spread = snapshot["moneySupplySpread"]
    assert (
        spread["referencePeriod"],
        spread["gap"],
        spread["previousGap"],
        spread["periodChange"],
    ) == ("2026-10", -2.7, -4, 1.3)
    gap = next(row for row in snapshot["series"] if row["code"] == "M1_M2_GAP")
    assert (gap["referencePeriod"], gap["value"]) == ("2026-10", -2.7)
    composite = snapshot["composite"]
    assert composite["inflationGap"] == 5
    assert composite["labels"] == ["需求偏冷", "上游成本挤压", "外部高息紧缩"]
