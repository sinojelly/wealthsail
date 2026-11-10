# Historical strict diagnostic policy only. Production uses sanitize-and-keep;
# see test_research_sanitize_keep.py for the current publication contract.
"""Incomplete investment checks preserve observations without granting plans."""

from copy import deepcopy

import pytest

from backend.macro.daily_research import ResearchValidationError, diagnose_report as validate_report
from tests.unit.test_research_cost_review75 import NOW, RUN, executed_checks, prepared


def validate(packet, raw, checks):
    return validate_report(raw, packet, checks, completed_at=NOW, research_id=RUN)


@pytest.mark.parametrize("signal", ["WATCH", "AVOID", "LEFT_VALUE"])
def test_incomplete_audit_retains_direction_without_formal_plan(signal):
    packet, raw, checks = prepared()
    raw["valueDirections"][0]["signal"] = signal
    before = deepcopy((packet, raw, checks))
    value = validate(packet, raw, checks)["valueDirections"][0]
    assert value["signal"] == ("AVOID" if signal == "AVOID" else "WATCH")
    assert value["qualificationStatus"] == "OBSERVATION_ONLY"
    assert value["investmentEligible"] is False
    assert value["plans"] == []
    assert value["quantitativeFundCodes"] == []
    assert (
        "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED" in value["formalEligibilityReasons"]
    )
    assert (packet, raw, checks) == before


@pytest.mark.parametrize("failure", ["unknown_product", "stale_price", "quantitative"])
def test_invalid_fund_downgrades_only_its_direction_after_complete_audit(failure):
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    original = raw["valueDirections"][0]
    healthy = deepcopy(original)
    healthy.update(key="independent-valid", title="独立有效方向")
    raw["valueDirections"].append(healthy)
    if failure == "unknown_product":
        original["plans"][0]["subjectId"] = "999999"
    elif failure == "stale_price":
        # Remove just this proposal's coordinate; the sibling uses another fund.
        invalid_id = original["plans"][0]["subjectId"]
        original["plans"] = original["plans"][:1]
        healthy["plans"] = [p for p in healthy["plans"] if p["subjectId"] != invalid_id]
        anchor = next(
            p
            for p in packet["discoveryActionableZones"]
            if p["subjectId"] == invalid_id
        )
        anchor["historyQuality"] = "STALE"
    else:
        original["quantitativeFundCodes"] = ["999999"]
    output = {
        row["key"]: row for row in validate(packet, raw, checks)["valueDirections"]
    }
    downgraded = output[original["key"]]
    assert downgraded["signal"] == "WATCH" and downgraded["plans"] == []
    assert downgraded["investmentEligible"] is False
    assert "FUND_PLAN_INVALID" in downgraded["formalEligibilityReasons"]
    assert output["independent-valid"]["plans"]


def test_observation_never_erases_trusted_negative_or_unverified_risk_checks():
    packet, raw, checks = prepared()
    review = next(
        r for r in raw["industryCycleReviews"] if r["industry"] == "NONFERROUS"
    )
    review["sourceIds"] = ["s2"]
    source = next(r for r in raw["sources"] if r["id"] == "s2")
    source["quote"] = "近期有色金属行业企业欠薪、裁员和倒闭。"
    checks["s2"] = {
        **source,
        "verification": "SOURCE_CHECKED",
        "quoteMatched": True,
        "retrievedAt": NOW.isoformat(),
        "contentHash": "b" * 64,
    }
    for check in review["riskChecks"]:
        check.update(status="UNVERIFIED", sourceIds=[])
        if check["kind"] == "LAYOFFS_CLOSURES":
            check.update(status="FOUND", sourceIds=["s2"])
    raw["valueDirections"][0]["industryCycle"] = None
    value = validate(packet, raw, checks)["valueDirections"][0]
    assert value["plans"] == [] and value["signal"] == "WATCH"
    cycle = value["industryCycle"]
    assert cycle["confirmedRiskFlags"]
    assert any(row["status"] == "UNVERIFIED" for row in cycle["riskChecks"])


def test_unsafe_direction_source_still_rejects_instead_of_becoming_trusted():
    packet, raw, checks = prepared()
    raw["valueDirections"][0]["sourceIds"] = ["invented-source"]
    with pytest.raises(ResearchValidationError):
        validate(packet, raw, checks)


@pytest.mark.parametrize(
    "change", [{"entryZoneMin": "0.01"}, {"subjectId": ""}, {"track": "invented"},
               {"subjectKind": []}, {"subjectId": {}}, {"market": []}]
)
def test_malformed_fund_proposal_cannot_veto_other_report_components(change):
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    raw["valueDirections"][0]["plans"][0].update(change)
    value = validate(packet, raw, checks)
    direction = value["valueDirections"][0]
    assert direction["plans"] == [] and direction["investmentEligible"] is False
    assert direction["signal"] == "WATCH"
    assert "FUND_PLAN_INVALID" in direction["formalEligibilityReasons"]
    assert value["summary"] == raw["summary"]
