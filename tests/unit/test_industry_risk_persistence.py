"""A missed daily search cannot reset recent, proven sector risk."""

from datetime import timedelta

import pytest

from test_daily_research import NOW, report
from test_industry_cycle import checks


def confirmed_review(stamp):
    return {
        "industry": "ROBOT",
        "stage": "UNKNOWN",
        "riskLevel": "CONFIRMED",
        "leftDiscountScale": 1.0,
        "riskFlags": ["FINANCING_COOLING"],
        "confirmedRiskFlags": ["FINANCING_COOLING"],
        "suspectedRiskFlags": [],
        "confirmedRiskAt": {"FINANCING_COOLING": stamp.isoformat()},
        "confirmedAt": stamp.isoformat(),
        "sourceIds": ["old-source"],
        "earningsVerified": False,
        "evidenceSummary": "此前原文已核验融资降温。",
        "riskChecks": [
            {
                **row,
                "status": "FOUND",
                "verification": "SOURCE_CHECKED",
                "strength": "CONFIRMED",
                "sourceIds": ["old-source"],
            }
            if row["kind"] == "FINANCING_COOLING"
            else row
            for row in checks()
        ],
    }


def previous(stamp):
    return {
        "status": "READY",
        "runId": "prior-public-report",
        "completedAt": stamp.isoformat(),
        "report": {
            "asOf": stamp.isoformat(),
            "industryCycleReviews": [confirmed_review(stamp)],
            "valueDirections": [],
        },
    }


def validate_with_history(history):
    from backend.macro.daily_research import validate_report

    raw = {
        **report(),
        "sources": [],
        "events": [],
        "industryCycleReviews": [
            {"industry": "ROBOT", "sourceIds": [], "riskChecks": checks()}
        ],
    }
    public = {"asOf": NOW.isoformat(), "directions": [], "previousResearch": history}
    return validate_report(raw, public, {}, completed_at=NOW)


def test_recent_confirmed_risk_survives_today_not_found_without_claiming_a_new_fact():
    stamp = NOW - timedelta(days=3)
    result = validate_with_history([previous(stamp)])
    cycle = result["industryCycleReviews"][0]
    assert cycle["riskLevel"] == "SUSPECTED"
    assert cycle["leftDiscountScale"] == 0.5
    assert "PERSISTED_RISK_WATCH" in cycle["riskFlags"]
    assert "近14日" in cycle["evidenceSummary"]
    assert cycle["confirmedRiskAt"]["FINANCING_COOLING"] == stamp.isoformat()
    assert cycle["riskChecks"][0]["status"] == "NOT_FOUND"
    assert "old-source" not in cycle["sourceIds"]


@pytest.mark.parametrize("days,status", [(15, "READY"), (-1, "READY"), (3, "FAILED")])
def test_expired_future_and_failed_reports_cannot_seed_risk(days, status):
    old = previous(NOW - timedelta(days=days))
    old["status"] = status
    cycle = validate_with_history([old])["industryCycleReviews"][0]
    assert "PERSISTED_RISK_WATCH" not in cycle["riskFlags"]
    assert cycle["leftDiscountScale"] == 0


def test_packet_keeps_public_industry_history_without_private_payload_or_model_prices():
    from backend.macro.daily_research import make_research_packet

    old = previous(NOW - timedelta(days=4))
    old["report"]["portfolio"] = {"secret": "PRIVATE_NEVER_COPY"}
    old["report"]["industryCycleReviews"][0]["secret"] = "PRIVATE_NEVER_COPY"
    public = make_research_packet({}, [], [], [], now=NOW, previous={"history": [old]})
    states = public.get("previousIndustryRiskStates", [])
    assert states and states[0]["industry"] == "ROBOT"
    assert states[0]["industryCycle"]["confirmedRiskFlags"] == ["FINANCING_COOLING"]
    assert "PRIVATE_NEVER_COPY" not in str(public)


def test_public_projections_keep_risk_strength_persistence_and_technical_risk_fields():
    from backend.macro.daily_research import _public_actionable_plan
    from test_relative_strength_pipeline import benchmark_analysis
    from backend.indicators.actionable_zones import compute_actionable_price_plan

    points = benchmark_analysis()["points"]
    plan = compute_actionable_price_plan(
        points, subject_kind="index", subject_id="hs300", benchmark_points=points
    )
    cycle = confirmed_review(NOW - timedelta(days=1))
    cycle["riskChecks"][0]["independentSourceCount"] = 2
    plan["industryCycle"] = cycle
    plan["relativeStrength"].update(lowerHighs=False, trendWeakening=False)
    plan["industryRiskAdjustment"] = {
        "state": "APPLIED",
        "entryShiftScale": 0.5,
        "baseEntryShiftScale": 0.5,
        "relativeStrengthUpgrade": False,
        "riskLevel": "SUSPECTED",
    }
    result = _public_actionable_plan(plan, "hs300", NOW)
    assert result["industryCycle"]["riskLevel"] == "CONFIRMED"
    assert result["industryCycle"]["confirmedRiskAt"] == cycle["confirmedRiskAt"]
    assert result["industryCycle"]["riskChecks"][0]["independentSourceCount"] == 2
    assert result["relativeStrength"]["lowerHighs"] is False
    assert result["industryRiskAdjustment"]["baseEntryShiftScale"] == 0.5


def test_direction_prices_and_scan_keep_half_protection_before_price_revision_is_assessed():
    from backend.macro.daily_research import validate_report
    from test_value_directions import packet, direction
    from test_industry_cycle import cycle
    from test_industry_cycle_priority import real_anchor

    public = packet()
    public["asOf"] = NOW.isoformat()
    anchor = real_anchor("005112")
    anchor.update(
        market="FUND_CN",
        unit="CNY",
        historyQuality="READY",
        sourceUri="https://fund.eastmoney.com/005112.html",
        retrievedAt=NOW.isoformat(),
        availableAt=NOW.isoformat(),
    )
    public["discoveryActionableZones"] = [anchor]
    public["directions"] = [{"id": "a-share:broad"}]
    public["previousResearch"] = [previous(NOW - timedelta(days=2))]
    raw = report()
    raw["industryCycleReviews"] = [
        {"industry": "ROBOT", "sourceIds": [], "riskChecks": checks()}
    ]
    raw["valueDirections"] = [
        direction(
            industryCycle=cycle(stage="UNKNOWN", sourceIds=[], evidence=[]),
            reviewIndustryIds=["ROBOT"],
        )
    ]
    result = validate_report(raw, public, {}, completed_at=NOW)
    actual = result["valueDirections"][0]
    plan = actual["plans"][0]
    assert actual["industryCycle"]["riskLevel"] == "SUSPECTED"
    assert plan["industryRiskAdjustment"]["entryShiftScale"] == 0.5
    assert plan["scoreBreakdown"]["nominalIndustryCyclePenalty"] == -10
    assert plan["priceRevisionStatus"] == "INITIAL"
    assert float(plan["tracks"][0]["entryZoneMax"]) < float(
        anchor["tracks"][0]["entryZoneMax"]
    )


def test_verified_company_recovery_in_history_keeps_older_sector_risk_observation():
    old = previous(NOW - timedelta(days=5))
    recovered = previous(NOW - timedelta(days=2))
    recovered["report"]["industryCycleReviews"][0].update(
        stage="EARNINGS_DELIVERY",
        riskLevel="NONE",
        riskFlags=[],
        confirmedRiskFlags=[],
        leftDiscountScale=0,
        earningsVerified=True,
    )
    actual = validate_with_history([old, recovered])["industryCycleReviews"][0]
    assert "PERSISTED_RISK_WATCH" in actual["riskFlags"]
    assert actual["persistedRiskFlags"] == ["FINANCING_COOLING"]


def test_new_completion_timestamp_cannot_renew_an_old_report_confirmation():
    old = previous(NOW)
    old["report"]["asOf"] = (NOW - timedelta(days=30)).isoformat()
    old["report"]["industryCycleReviews"][0].pop("confirmedAt")
    old["report"]["industryCycleReviews"][0].pop("confirmedRiskAt")
    actual = validate_with_history([old])["industryCycleReviews"][0]
    assert "PERSISTED_RISK_WATCH" not in actual["riskFlags"]


@pytest.mark.asyncio
async def test_history_query_keeps_each_calendar_day_despite_same_day_debug_bursts():
    import sqlite3
    from backend.macro.daily_repository import read_recent_public_research

    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute("ATTACH DATABASE ':memory:' AS research")
    database.execute(
        "CREATE TABLE research.daily_research_runs (run_id TEXT,slot_date TEXT,dispatched_at TEXT,run_mode TEXT)"
    )
    database.execute(
        "CREATE TABLE research.daily_research_results (run_id TEXT,status TEXT,recorded_at TEXT,payload TEXT,error_class TEXT,"
        "failure_stage TEXT,failure_reason_code TEXT,failure_rule_id TEXT,failure_item_index INTEGER)"
    )
    for index in range(22):
        stamp = (
            NOW - timedelta(minutes=index) if index < 21 else NOW - timedelta(days=5)
        )
        identity = f"report-{index}"
        database.execute(
            "INSERT INTO research.daily_research_runs VALUES (?,?,?,?)",
            (
                identity,
                str(stamp.date()),
                str(stamp),
                "DEBUG" if index < 21 else "SCHEDULED",
            ),
        )
        database.execute(
            "INSERT INTO research.daily_research_results (run_id,status,recorded_at,payload,error_class) VALUES (?,?,?,?,?)",
            (identity, "READY", str(stamp), "{}", None),
        )

    class Connection:
        async def fetch(self, query, *args):
            return [
                dict(row)
                for row in database.execute(
                    query,
                    {str(index): str(value) for index, value in enumerate(args, 1)},
                ).fetchall()
            ]

    try:
        actual = await read_recent_public_research(Connection(), NOW)
        assert "report-21" in {row["runId"] for row in actual}
        assert len(actual) <= 30
    finally:
        database.close()
