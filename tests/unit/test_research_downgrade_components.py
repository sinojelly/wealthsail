"""Independent malformed components retain explicit read-only qualifications."""

from copy import deepcopy

import pytest

from backend.macro.alpha_research import _program_plan
from backend.macro.daily_research import validate_report
from backend.macro.research_batch import evaluate_research_batch
from tests.unit.test_research_cost_review75 import NOW, RUN, executed_checks, prepared


def assert_observation(direction):
    assert direction["signal"] == "WATCH"
    assert direction["plans"] == direction["quantitativeFundCodes"] == []
    assert direction.get("qualificationStatus") == "OBSERVATION_ONLY"
    assert direction.get("investmentEligible") is False
    assert "FUND_PLAN_INVALID" in direction.get("formalEligibilityReasons", [])


def test_bad_plan_and_scalar_topic_keep_explicit_observation_qualification():
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    raw["valueDirections"][0]["plans"][0]["track"] = "made_up"
    raw["topicCatalog"] = [1]
    before = deepcopy((packet, raw, checks))

    result = evaluate_research_batch(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )

    assert result["strictPass"] is True
    assert result["autoPublishAllowed"] is True
    direction = result["formalReport"]["valueDirections"][0]
    assert_observation(direction)
    assert direction["key"] == raw["valueDirections"][0]["key"]
    assert result["formalReport"]["summary"] == raw["summary"]
    assert result["repairSummary"]["changed"] is True
    topic = next(
        row for row in result["registry"]["items"] if row["key"] == direction["key"]
    )
    assert topic["status"] == "OBSERVATION_ONLY"
    assert topic["investmentEligible"] is False
    assert any(
        row["reasonCode"] == "TOPIC_SCHEMA_INVALID"
        for row in result["registry"]["items"]
    )
    assert (packet, raw, checks) == before


def test_plain_program_price_value_error_downgrades_fund_and_keeps_report():
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    proposal = raw["valueDirections"][0]["plans"][0]
    anchor = next(
        row
        for row in packet["discoveryActionableZones"]
        if row["subjectId"] == proposal["subjectId"]
    )
    anchor["currentPrice"] = "not-a-price"
    before = deepcopy((packet, raw, checks))
    with pytest.raises(ValueError, match="invalid program price") as error:
        _program_plan(anchor, proposal["track"], NOW, require_theme=False)
    assert type(error.value) is ValueError

    report = validate_report(raw, packet, checks, completed_at=NOW, research_id=RUN)
    assert_observation(report["valueDirections"][0])
    assert report["summary"] == raw["summary"]
    result = evaluate_research_batch(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert result["strictPass"] is True
    assert result["autoPublishAllowed"] is True
    assert_observation(result["formalReport"]["valueDirections"][0])
    assert result["repairSummary"]["changed"] is True
    assert (packet, raw, checks) == before


@pytest.mark.parametrize("failure", ["quantitative_retry", "plan_schema", "extra_fund_identity", "extra_market_identity"])
def test_downgrade_keeps_actual_fund_sector_and_its_confirmed_layoff_risk(failure):
    packet, raw, checks = prepared()
    direction = raw["valueDirections"][0]
    assert direction["reviewIndustryIds"] == ["RARE_EARTH", "RARE_EARTH_METALS"]
    # NONFERROUS comes from the server's actual fund catalog, rather than the
    # model's narrower declared sector list. Its distinct negative facts must
    # survive removal of that fund's investment authority.
    source = next(row for row in raw["sources"] if row["id"] == "s2")
    source["quote"] = "近期有色金属行业企业欠薪、裁员和倒闭。"
    checks["s2"] = {
        **source,
        "verification": "SOURCE_CHECKED",
        "quoteMatched": True,
        "retrievedAt": NOW.isoformat(),
        "contentHash": "b" * 64,
    }
    review = next(
        row for row in raw["industryCycleReviews"] if row["industry"] == "NONFERROUS"
    )
    review["sourceIds"] = ["s2"]
    layoffs = next(
        row for row in review["riskChecks"] if row["kind"] == "LAYOFFS_CLOSURES"
    )
    layoffs.update(status="FOUND", sourceIds=["s2"], summary=source["quote"])
    checks = executed_checks(packet, checks)
    baseline = validate_report(raw, packet, checks, completed_at=NOW, research_id=RUN)
    baseline_direction = baseline["valueDirections"][0]
    assert "NONFERROUS" in baseline_direction["reviewIndustryIds"]
    assert (
        "LAYOFFS_CLOSURES" in baseline_direction["industryCycle"]["confirmedRiskFlags"]
    )

    if failure == "quantitative_retry":
        direction["quantitativeFundCodes"] = ["999999"]
    elif failure == "plan_schema":
        direction["plans"][0]["entryZoneMin"] = "0.01"
    elif failure == "extra_fund_identity":
        direction["plans"][0]["fundCode"] = "999999"
    else:
        direction["plans"][0]["market"] = []
    before = deepcopy((packet, raw, checks))
    report = validate_report(raw, packet, checks, completed_at=NOW, research_id=RUN)
    downgraded = report["valueDirections"][0]
    assert_observation(downgraded)
    assert "NONFERROUS" in downgraded["reviewIndustryIds"]
    cycle = downgraded["industryCycle"]
    assert "LAYOFFS_CLOSURES" in cycle["confirmedRiskFlags"]
    assert any(
        row["kind"] == "LAYOFFS_CLOSURES" and row["status"] == "FOUND"
        for row in cycle["riskChecks"]
    )
    result = evaluate_research_batch(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert result["autoPublishAllowed"] is True
    delivered = result["formalReport"]["valueDirections"][0]
    assert "NONFERROUS" in delivered["reviewIndustryIds"]
    assert "LAYOFFS_CLOSURES" in delivered["industryCycle"]["confirmedRiskFlags"]
    assert (packet, raw, checks) == before
