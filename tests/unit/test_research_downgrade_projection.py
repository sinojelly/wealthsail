"""Historical strict downgrade metadata survives the bounded read-only DTO."""

from copy import deepcopy

import pytest

from backend.macro import research_acceptance_repository as repository
from backend.macro.daily_research import diagnose_report
from backend.macro.research_acceptance import _projection
from backend.macro.research_delivery import _observations
from tests.unit.test_research_autodowngrade_audit import KEY, RUN, public_failure


def assert_no_trade_coordinates(value):
    if isinstance(value, dict):
        assert (
            not {
                "plans",
                "actionablePlans",
                "quantitativeFundCodes",
                "tracks",
                "entryZoneMin",
                "entryZoneMax",
                "latestPrice",
                "price",
                "priceBasis",
            }
            & value.keys()
        )
        for row in value.values():
            assert_no_trade_coordinates(row)
    elif isinstance(value, list):
        for row in value:
            assert_no_trade_coordinates(row)


def test_real_strict_downgrade_survives_acceptance_and_delivery_with_risk_evidence():
    value, at = public_failure()
    before = deepcopy(value)
    report = diagnose_report(
        value["raw"],
        value["packet"],
        value["sourceChecks"],
        completed_at=at,
        research_id=RUN,
    )
    original = next(row for row in report["valueDirections"] if row["key"] == KEY)
    projected = _projection(report)
    accepted = repository._accepted(projected)
    delivered, _filtered = _observations(accepted, at)
    for result in (accepted, delivered):
        direction = next(row for row in result["valueDirections"] if row["key"] == KEY)
        assert direction["signal"] == "WATCH"
        assert direction["qualificationStatus"] == "OBSERVATION_ONLY"
        assert direction["investmentEligible"] is False
        assert (
            direction["formalEligibilityReasons"]
            == original["formalEligibilityReasons"]
        )
        cycle = direction["industryCycle"]
        assert cycle["searchExecutionVerified"] is False
        for field in ("confirmedRiskFlags", "riskFlags", "evidenceGaps"):
            assert cycle[field] == original["industryCycle"][field]
        assert [row["status"] for row in cycle["riskChecks"]] == [
            row["status"] for row in original["industryCycle"]["riskChecks"]
        ]
        assert_no_trade_coordinates(result)
    assert value == before


@pytest.mark.parametrize(
    "signal", ["LEFT_VALUE", "RIGHT_TREND", "TAKE_PROFIT", "WATCH", "AVOID"]
)
def test_read_only_direction_keeps_only_known_signal_and_false_eligibility(signal):
    result = repository._accepted(
        {
            "valueDirections": [
                {
                    "key": "observed",
                    "signal": signal,
                    "qualificationStatus": "OBSERVATION_ONLY",
                    "investmentEligible": False,
                    "formalEligibilityReasons": ["FUND_PLAN_INVALID"],
                    "plans": [{"price": "123.45"}],
                }
            ]
        }
    )["valueDirections"][0]
    assert result["signal"] == signal
    assert result["qualificationStatus"] == "OBSERVATION_ONLY"
    assert result["investmentEligible"] is False
    assert result["formalEligibilityReasons"] == ["FUND_PLAN_INVALID"]
    assert_no_trade_coordinates(result)


@pytest.mark.parametrize("invalid", [None, True, 0, "PRIVATE", [], {}, ["WATCH"]])
def test_untrusted_metadata_types_never_create_a_signal_or_investment_permission(
    invalid,
):
    direction = {
        "key": "observed",
        "signal": invalid,
        "qualificationStatus": invalid,
        "investmentEligible": invalid,
        "formalEligibilityReasons": invalid,
    }
    result = repository._accepted({"valueDirections": [direction]})["valueDirections"][
        0
    ]
    assert "signal" not in result
    assert "qualificationStatus" not in result
    assert "investmentEligible" not in result
    if isinstance(invalid, list):
        assert result.get("formalEligibilityReasons", []) == []
    else:
        assert "formalEligibilityReasons" not in result


def test_formal_input_cannot_grant_permission_through_the_read_only_dto():
    result = repository._accepted(
        {
            "valueDirections": [
                {
                    "signal": "LEFT_VALUE",
                    "qualificationStatus": "FORMAL",
                    "investmentEligible": True,
                }
            ]
        }
    )["valueDirections"][0]
    assert result.get("signal") == "LEFT_VALUE"
    assert "qualificationStatus" not in result
    assert "investmentEligible" not in result


def test_reasons_are_deduplicated_closed_server_codes_with_a_twelve_reason_limit():
    reasons = [
        "OBSERVATION_ONLY",
        "FUND_PLAN_INVALID",
        "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED",
        "DIRECTION_PLAN_SECTOR_UNREVIEWED",
        "DIRECTION_CALIBRATION_RULE_UNKNOWN",
        "NO_FORMAL_PROPOSAL",
        "FUND_PLAN_MISSING",
        "SOURCE_EVIDENCE_PENDING",
        "INDUSTRY_IDENTITY_PENDING",
        "INDUSTRY_AUDIT_PENDING",
        "FORMAL_VALIDATION_FAILED",
        "PUBLICATION_SAFETY_BLOCKED",
        "DIRECTION_EVIDENCE_INSUFFICIENT",
    ]
    original = {
        "valueDirections": [
            {
                "formalEligibilityReasons": [
                    "PRIVATE",
                    {},
                    [],
                    None,
                    True,
                    reasons[0],
                    *reasons,
                ],
            }
        ]
    }
    before = deepcopy(original)
    result = repository._accepted(original)["valueDirections"][0]
    assert result["formalEligibilityReasons"] == reasons[:12]
    assert original == before
