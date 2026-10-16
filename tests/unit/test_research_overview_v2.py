"""Business quotas preserve global leads without inventing personal conclusions."""

from copy import deepcopy
from datetime import timedelta

import pytest

from test_daily_research import NOW, engine
from test_daily_research_overview import macro_change
from test_research_board import build, trend


def daily(*, count=1, importance="HIGH", direction="us:broad"):
    return {
        "state": "READY",
        "reportRunId": "ready-run",
        "reportCompletedAt": NOW.isoformat(),
        "report": {
            "asOf": NOW.isoformat(),
            "sources": [
                {
                    "id": "source:a",
                    "verification": "SOURCE_CHECKED",
                    "retrievedAt": NOW.isoformat(),
                    "publishedDate": "2026-10-15",
                }
            ],
            "events": [
                {
                    "id": f"event:{i}",
                    "title": f"跨市场事件{i}",
                    "summary": "仅为有来源的条件线索。",
                    "importance": importance,
                    "phase": "ANNOUNCED",
                    "sourceIds": ["source:a"],
                    "affectedDirectionIds": [direction],
                    "channels": ["需观察利率传导"],
                    "watchFor": ["核查下一次正式公告"],
                    "marketReaction": "UNVERIFIED",
                }
                for i in range(count)
            ],
        },
    }


def overview(*, context=None, evidence=None, board=None, report=None):
    evidence = [] if evidence is None else evidence
    return engine().build_research_overview(
        context or {"directionId": "a-share:broad"},
        [],
        evidence,
        {},
        board if board is not None else build(evidence=evidence),
        report or {"state": "NOT_RUN", "report": None},
        now=NOW,
    )


def group(result, identity):
    return next(row for row in result["groups"] if row["id"] == identity)


def test_global_high_lead_survives_macro_changes_and_foreign_selection():
    board = macro_change("PMI")
    for index in range(3):
        board["items"].append({**board["items"][0], "id": f"macro:{index}"})
    result = overview(board=board, report=daily())
    assert result["methodVersion"] == "research-overview-v2"
    item = next(row for row in result["highlights"] if row["id"] == "event:0")
    assert item["scope"] == "GLOBAL" and item["stage"] == "EVENT_LEAD"
    assert item["phase"] == "ANNOUNCED" and item["tone"] != "RED"
    assert item["asOf"] == "2026-10-15"  # source period, never report completion
    assert item["nextAction"]["target"] == "research-global"
    assert "模型" in item["impact"] and "已证实" not in result["summary"]


def test_quotas_are_per_business_group_and_global_selection_union_deduplicates():
    report = daily(count=5, direction="a-share:broad")
    report["report"]["sources"][0]["publishedDate"] = "2026-10-15"
    report["report"]["events"].append(deepcopy(report["report"]["events"][0]))
    evidence = [trend(), {**trend(), "indexId": "zz500", "label": "中证500"}]
    result = overview(evidence=evidence, report=report)
    assert {row["id"] for row in result["groups"]} == {
        "RISKS",
        "OPPORTUNITIES",
        "HOLDINGS",
        "UPCOMING",
    }
    assert all(len(row["items"]) <= 2 for row in result["groups"])
    ids = [row["id"] for row in result["highlights"]]
    assert len(ids) == len(set(ids)) and ids.count("event:0") == 1
    assert len(group(result, "OPPORTUNITIES")["items"]) == 2


def test_empty_business_groups_do_not_fill_with_ordinary_macro_numbers():
    result = overview(
        context={
            "items": [
                {
                    "code": "CPI_YOY",
                    "value": 0.9,
                    "state": "READY",
                    "valueVerification": "VERIFIED",
                    "referencePeriod": "2026-09",
                }
            ]
        }
    )
    assert result["highlights"] == []
    holdings = group(result, "HOLDINGS")
    assert holdings["state"] == "UNKNOWN" and holdings["items"] == []
    assert holdings["nextAction"]["href"] == "/dashboard?scope=MINE"
    assert "未知" in holdings["summary"] and "无风险" not in str(result)
    assert result["evidenceIds"] == []


def test_useful_summary_identifies_real_condition_and_next_step_with_references():
    row = trend(positive=False)
    result = overview(evidence=[row])
    assert "尚未满足" in result["summary"]
    assert "MA200" in result["summary"] and "63" in result["summary"]
    assert "hs300" in result["evidenceIds"]
    item = group(result, "OPPORTUNITIES")["items"][0]
    assert item["stage"] == "INDEX_CONDITION" and item["scope"] == "SELECTION"
    assert "建仓" in result["summary"]


@pytest.mark.parametrize("bad", ["confidence", "holdingRelevance", "factVerification"])
def test_personal_red_retains_all_existing_guards(bad):
    board = build(
        evidence=[],
        decision={"state": "POSITION_REVIEW", "gates": []},
        portfolio={
            "state": "READY",
            "heldSelected": True,
            "budgetViolation": True,
            "budgetState": "BLOCKED",
        },
    )
    next(row for row in board["items"] if row["key"] == "personal-budget")[bad] = (
        "UNKNOWN"
    )
    result = overview(board=board, report=daily())
    assert all(row["tone"] != "RED" for row in result["highlights"])


def test_plan_is_separate_group_with_real_future_date_and_past_is_excluded():
    report = daily()
    report["report"]["upcoming"] = [
        {
            "id": "plan:future",
            "title": "正式计划",
            "phase": "PLANNED",
            "scheduledDate": (NOW + timedelta(days=3)).date().isoformat(),
            "sourceIds": ["source:a"],
            "affectedDirectionIds": ["us:broad"],
        },
        {
            "id": "plan:past",
            "title": "旧计划",
            "phase": "PLANNED",
            "scheduledDate": (NOW - timedelta(days=1)).date().isoformat(),
            "sourceIds": ["source:a"],
            "affectedDirectionIds": [],
        },
    ]
    result = overview(report=report)
    plans = group(result, "UPCOMING")["items"]
    assert [row["id"] for row in plans] == ["plan:future"]
    assert plans[0]["phase"] == "PLANNED" and plans[0]["scope"] == "GLOBAL"
    assert plans[0]["asOf"] == (NOW + timedelta(days=3)).date().isoformat()


def test_complete_existing_qualification_is_execution_stage_without_invented_new_gates():
    board = {
        "items": [
            {
                "id": "scope:qualification:entry",
                "key": "qualification:entry",
                "category": "OPPORTUNITY",
                "factVerification": "VERIFIED",
                "confidence": "VERIFIED",
                "summary": "当前已满足规定的完整研究资格。",
                "impact": "仍需人工复核。",
                "evidenceIds": ["value", "trend", "portfolio"],
            }
        ]
    }
    result = overview(board=board)
    item = group(result, "OPPORTUNITIES")["items"][0]
    assert item["stage"] == "EXECUTION_CONDITION"
    assert "仍需人工复核" in result["summary"]


def test_equal_importance_prefers_checked_source_then_actual_recency_over_input_order():
    report = daily(count=3)
    report["report"]["sources"] += [
        {
            "id": "source:b",
            "verification": "SOURCE_CHECKED",
            "publishedDate": "2026-10-14",
            "retrievedAt": NOW.isoformat(),
        },
        {
            "id": "source:c",
            "verification": "UNVERIFIED",
            "publishedDate": "2026-10-16",
            "retrievedAt": NOW.isoformat(),
        },
    ]
    report["report"]["events"][0]["sourceIds"] = ["source:c"]
    report["report"]["events"][1]["sourceIds"] = ["source:b"]
    result = overview(report=report)
    assert [row["id"] for row in group(result, "RISKS")["items"]] == [
        "event:2",
        "event:1",
    ]


def test_unchecked_claimed_publication_day_is_not_an_actual_evidence_date():
    report = daily()
    report["report"]["sources"][0]["verification"] = "UNVERIFIED"
    item = overview(report=report)["highlights"][0]
    assert item["asOf"] is None and "待核对" in item["summary"]


def test_multiple_real_source_dates_remain_visible_without_one_fictitious_event_day():
    report = daily()
    report["report"]["sources"].append(
        {
            "id": "source:b",
            "verification": "SOURCE_CHECKED",
            "publishedDate": "2026-10-14",
            "retrievedAt": NOW.isoformat(),
        }
    )
    report["report"]["events"][0]["sourceIds"].append("source:b")
    item = overview(report=report)["highlights"][0]
    assert item["asOf"] is None
    assert "2026-10-14" in item["summary"] and "2026-10-15" in item["summary"]


def test_personal_budget_assessment_does_not_use_board_projection_as_observation_day():
    board = build(
        evidence=[],
        decision={"state": "POSITION_REVIEW", "gates": []},
        portfolio={
            "state": "READY",
            "heldSelected": True,
            "budgetViolation": True,
            "budgetState": "BLOCKED",
        },
    )
    item = overview(board=board)["highlights"][0]
    assert item["scope"] == "HOLDINGS" and item["tone"] == "RED"
    assert item["asOf"] is None  # portfolio observation date is absent from this DTO
