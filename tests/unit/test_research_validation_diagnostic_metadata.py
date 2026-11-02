"""Validation diagnostics expose only closed rule IDs and bounded item indices."""

from copy import deepcopy

import pytest

from backend.macro.daily_research import ResearchValidationError, validate_report
from backend.macro.value_directions import validate_value_directions
from backend.macro import industry_cycle
from test_daily_research import NOW, report
from test_industry_cycle import checks
from test_industry_cycle import cycle
from test_value_directions import direction, packet


def test_legacy_validation_constructor_retains_message_and_empty_metadata():
    error = ResearchValidationError("SOURCE_REFERENCE_INVALID", "private detail")
    assert str(error) == "private detail"
    assert error.reason_code == "SOURCE_REFERENCE_INVALID"
    assert error.rule_id is None
    assert error.item_index is None


@pytest.mark.parametrize("index", [True, False, -1, 10000, 1.0, "1", [], {}])
def test_item_index_constructor_rejects_unbounded_or_non_integer_metadata(index):
    with pytest.raises(ValueError):
        ResearchValidationError("SOURCE_REFERENCE_INVALID", item_index=index)


@pytest.mark.parametrize("rule", ["unknown", "SOURCE_SECRET\nLINE", 1, [], {}])
def test_rule_id_constructor_rejects_any_non_allowlisted_metadata(rule):
    with pytest.raises(ValueError):
        ResearchValidationError("SOURCE_REFERENCE_INVALID", rule_id=rule)


@pytest.mark.parametrize("reason", ["unsafe\nreason", "UNLISTED_ERROR", 2, []])
def test_reason_code_constructor_rejects_non_allowlisted_metadata(reason):
    with pytest.raises(ValueError):
        ResearchValidationError(reason)


@pytest.mark.parametrize("index", [0, 9999])
def test_item_index_constructor_accepts_inclusive_bounds(index):
    assert (
        ResearchValidationError("SOURCE_REFERENCE_INVALID", item_index=index).item_index
        == index
    )


def test_second_direction_source_failure_preserves_its_item_index():
    rows = [direction(), direction(key="second", sourceIds=["missing"])]
    with pytest.raises(ResearchValidationError) as caught:
        validate_value_directions(rows, packet(), NOW, {"s1": "source:one"})
    assert caught.value.reason_code == "SOURCE_REFERENCE_INVALID"
    assert caught.value.item_index == 1


@pytest.mark.parametrize(
    "update,rule",
    [
        ({"reviewIndustryIds": ["missing"]}, "DIRECTION_INDUSTRY_REVIEW_UNKNOWN"),
        ({"macroCodes": ["missing"]}, "DIRECTION_MACRO_REFERENCE_UNKNOWN"),
        ({"macroCodes": []}, "DIRECTION_EVIDENCE_INSUFFICIENT"),
    ],
)
def test_second_direction_known_rule_preserves_closed_rule_and_index(update, rule):
    rows = [direction(), direction(key="second", **update)]
    with pytest.raises(ResearchValidationError) as caught:
        validate_value_directions(rows, packet(), NOW, {"s1": "source:one"})
    assert caught.value.rule_id == rule
    assert caught.value.item_index == 1


def test_calibration_failure_preserves_its_direction_index():
    second = direction(key="second")
    second["plans"][0]["calibrationRuleKeys"] = ["unknown"]
    with pytest.raises(ResearchValidationError) as caught:
        validate_value_directions(
            [direction(), second], packet(), NOW, {"s1": "source:one"}
        )
    assert caught.value.rule_id == "DIRECTION_CALIBRATION_RULE_UNKNOWN"
    assert caught.value.item_index == 1


def test_second_direction_schema_failure_keeps_schema_reason_and_no_business_rule():
    rows = [direction(), direction(key="INVALID")]
    with pytest.raises(ResearchValidationError) as caught:
        validate_value_directions(rows, packet(), NOW, {"s1": "source:one"})
    assert caught.value.reason_code == "SCHEMA_VALIDATION_FAILED"
    assert caught.value.rule_id is None
    assert caught.value.item_index == 1


def test_nested_program_price_failure_keeps_direction_index_without_inventing_a_rule():
    second = direction(key="second")
    second["plans"][0]["track"] = "RIGHT_TREND"
    with pytest.raises(ResearchValidationError) as caught:
        validate_value_directions(
            [direction(), second], packet(), NOW, {"s1": "source:one"}
        )
    assert caught.value.reason_code == "PAYLOAD_VALIDATION_FAILED"
    assert caught.value.rule_id is None
    assert caught.value.item_index == 1
    assert str(caught.value) == "ambiguous program track"


def test_nested_quantitative_failure_keeps_direction_index_without_inventing_a_rule():
    public = packet()
    public["quantitativeScan"] = {
        "methodVersion": "fund-quant-scan-v2",
        "minimumHistorySamples": 120,
        "asOf": NOW.isoformat(),
        "items": [],
    }
    with pytest.raises(ResearchValidationError) as caught:
        validate_value_directions(
            [direction(), direction(key="second", quantitativeFundCodes=["005112"])],
            public,
            NOW,
            {"s1": "source:one"},
        )
    assert caught.value.reason_code == "FUND_PLAN_INVALID"
    assert caught.value.rule_id is None
    assert caught.value.item_index == 1
    assert str(caught.value) == "missing or ambiguous quantitative fund"


def test_top_review_schema_failure_keeps_review_index_without_a_business_rule():
    raw = report()
    raw["industryCycleReviews"] = [
        {"industry": "ROBOT", "sourceIds": [], "riskChecks": checks()},
        {"industry": "AI", "sourceIds": [], "riskChecks": checks()[:4]},
    ]
    with pytest.raises(ResearchValidationError) as caught:
        validate_report(raw, {"asOf": NOW.isoformat()}, {}, completed_at=NOW)
    assert caught.value.reason_code == "SCHEMA_VALIDATION_FAILED"
    assert caught.value.rule_id is None
    assert caught.value.item_index == 1


def test_top_direction_schema_failure_keeps_direction_index():
    raw = report()
    raw["valueDirections"] = [direction(), direction(key="INVALID")]
    with pytest.raises(ResearchValidationError) as caught:
        validate_report(
            raw,
            {"asOf": NOW.isoformat(), "discoveryUniverse": []},
            {},
            completed_at=NOW,
        )
    assert caught.value.reason_code == "SCHEMA_VALIDATION_FAILED"
    assert caught.value.rule_id is None
    assert caught.value.item_index == 1


def test_top_review_business_failure_preserves_review_index():
    raw = report()
    rows = {"industry": "ROBOT", "sourceIds": [], "riskChecks": checks()}
    raw["industryCycleReviews"] = [rows, deepcopy(rows)]
    with pytest.raises(ResearchValidationError) as caught:
        validate_report(raw, {"asOf": NOW.isoformat()}, {}, completed_at=NOW)
    assert caught.value.reason_code == "DIRECTION_VALIDATION_FAILED"
    assert caught.value.rule_id == "INDUSTRY_REVIEW_DUPLICATE"
    assert caught.value.item_index == 1


def test_missing_candidate_coverage_has_no_invented_item_index():
    raw = report()
    raw["valueDirections"] = []
    public = {
        "asOf": NOW.isoformat(),
        "industryCycleResearch": {"required": True},
        "discoveryUniverse": [{"discoverySector": "ROBOT"}],
    }
    with pytest.raises(ResearchValidationError) as caught:
        validate_report(raw, public, {}, completed_at=NOW)
    assert caught.value.rule_id == "INDUSTRY_REVIEW_COVERAGE_MISSING"
    assert caught.value.item_index is None


@pytest.mark.parametrize(
    "kind,rule",
    [
        ("missing_cycle", "INDUSTRY_RISK_CHECK_MISSING"),
        ("duplicate", "INDUSTRY_RISK_CHECK_DUPLICATE"),
        ("missing_source", "INDUSTRY_RISK_SOURCE_MISSING"),
        ("foreign_source", "INDUSTRY_CYCLE_SOURCE_INVALID"),
        ("unrelated_source", "INDUSTRY_CYCLE_SOURCE_INVALID"),
    ],
)
def test_known_industry_business_rules_use_a_typed_error(kind, rule):
    raw = cycle(stage="UNKNOWN", sourceIds=["s1"], evidence=[])
    if kind == "missing_cycle":
        raw = None
    elif kind == "duplicate":
        raw["riskChecks"][1] = deepcopy(raw["riskChecks"][0])
    elif kind == "missing_source":
        raw["riskChecks"][0]["status"] = "FOUND"
    elif kind == "foreign_source":
        raw["sourceIds"] = ["foreign"]
    else:
        raw["riskChecks"][0].update(status="FOUND", sourceIds=["s2"])
    with pytest.raises(ValueError) as caught:
        industry_cycle.normalize_industry_cycle(
            raw,
            require_checks=True,
            source_aliases={"s1": "source:one", "s2": "source:two"},
            now=NOW,
        )
    assert type(caught.value).__name__ == "IndustryCycleValidationError"
    assert caught.value.rule_id == rule


def test_direction_propagates_industry_business_rule_without_losing_its_index():
    second = direction(key="second", industryCycle=cycle(stage="UNKNOWN", evidence=[]))
    second["industryCycle"]["riskChecks"][0]["status"] = "FOUND"
    with pytest.raises(ResearchValidationError) as caught:
        validate_value_directions(
            [direction(), second], packet(), NOW, {"s1": "source:one"}
        )
    assert caught.value.rule_id == "INDUSTRY_RISK_SOURCE_MISSING"
    assert caught.value.item_index == 1


def test_top_review_propagates_industry_business_rule():
    raw = report()
    risk_rows = checks()
    risk_rows[0]["status"] = "FOUND"
    raw["industryCycleReviews"] = [
        {"industry": "ROBOT", "sourceIds": [], "riskChecks": risk_rows}
    ]
    with pytest.raises(ResearchValidationError) as caught:
        validate_report(raw, {"asOf": NOW.isoformat()}, {}, completed_at=NOW)
    assert caught.value.rule_id == "INDUSTRY_RISK_SOURCE_MISSING"
    assert caught.value.item_index == 0


def test_direct_cycle_schema_failure_is_not_mislabeled_as_missing_business_checks():
    from pydantic import ValidationError

    raw = cycle()
    raw["riskChecks"] = raw["riskChecks"][:4]
    with pytest.raises(ValidationError) as caught:
        industry_cycle.normalize_industry_cycle(raw, now=NOW)
    assert not hasattr(caught.value, "rule_id")
