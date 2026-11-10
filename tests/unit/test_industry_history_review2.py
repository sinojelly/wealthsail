"""Company earnings recoveries must not erase independent sector risk clocks."""

from copy import deepcopy
from datetime import timedelta

import pytest

from test_daily_research import NOW
from test_industry_cycle import checks
from test_industry_risk_persistence import previous, validate_with_history


SECTOR_RISKS = (
    "FINANCING_COOLING",
    "LAYOFFS_CLOSURES",
    "ORDER_CUTS",
    "OVERCAPACITY_PRICE_WAR",
    "PRODUCTION_DELAY",
)
EARNINGS_RISKS = (
    "NO_CURRENT_EARNINGS_PROOF",
    "NO_EARNINGS_DELIVERY",
    "FORWARD_PLANS_ONLY",
    "EARLY_COMMERCIALIZATION",
)


def risk_history(flag, days=5):
    stamp = NOW - timedelta(days=days)
    record = previous(stamp)
    cycle = record["report"]["industryCycleReviews"][0]
    cycle.update(
        riskFlags=[flag],
        confirmedRiskFlags=[flag],
        confirmedRiskAt={flag: stamp.isoformat()},
        riskChecks=[
            {**row, "status": "FOUND", "sourceIds": ["old-source"]}
            if row["kind"] == flag
            else row
            for row in checks()
        ],
    )
    return record


def recovery_history():
    record = previous(NOW - timedelta(days=2))
    cycle = record["report"]["industryCycleReviews"][0]
    cycle.update(
        stage="EARNINGS_DELIVERY",
        riskLevel="NONE",
        riskFlags=[],
        confirmedRiskFlags=[],
        confirmedRiskAt={},
        leftDiscountScale=0,
        earningsVerified=True,
    )
    return record


@pytest.mark.parametrize("flag", SECTOR_RISKS)
def test_history_earnings_recovery_does_not_clear_sector_observation(flag):
    record = risk_history(flag)
    result = validate_with_history([record, recovery_history()])
    cycle = result["industryCycleReviews"][0]
    assert cycle["persistedRiskFlags"] == [flag]
    assert cycle["leftDiscountScale"] == 0.5
    assert cycle["confirmedRiskAt"][flag] == (NOW - timedelta(days=5)).isoformat()


def test_legacy_confirmed_sector_flag_survives_company_earnings_recovery():
    record = risk_history("FINANCING_COOLING")
    prior = record["report"]["industryCycleReviews"][0]
    for field in ("riskLevel", "confirmedRiskFlags", "suspectedRiskFlags"):
        prior.pop(field, None)
    cycle = validate_with_history([record, recovery_history()])["industryCycleReviews"][
        0
    ]
    assert cycle["persistedRiskFlags"] == ["FINANCING_COOLING"]


@pytest.mark.parametrize("flag", EARNINGS_RISKS)
def test_history_earnings_recovery_clears_only_earnings_flags(flag):
    result = validate_with_history([risk_history(flag), recovery_history()])
    cycle = result["industryCycleReviews"][0]
    assert not cycle.get("persistedRiskFlags")
    assert cycle["leftDiscountScale"] == 0


def test_mixed_history_clears_earnings_but_preserves_original_sector_clock():
    record = risk_history("ORDER_CUTS")
    prior = record["report"]["industryCycleReviews"][0]
    prior["riskFlags"].append("NO_CURRENT_EARNINGS_PROOF")
    prior["confirmedRiskFlags"].append("NO_CURRENT_EARNINGS_PROOF")
    prior["confirmedRiskAt"]["NO_CURRENT_EARNINGS_PROOF"] = prior["confirmedAt"]
    original = deepcopy(record)
    cycle = validate_with_history([record, recovery_history()])["industryCycleReviews"][
        0
    ]
    assert cycle["persistedRiskFlags"] == ["ORDER_CUTS"]
    assert record == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("forwardPlansOnly", True),
        ("evidenceGaps", ["CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING"]),
    ],
)
def test_recovered_history_cannot_regenerate_earnings_flags_from_old_metadata(
    field, value
):
    record = risk_history("FORWARD_PLANS_ONLY")
    record["report"]["industryCycleReviews"][0][field] = value
    cycle = validate_with_history([record, recovery_history()])["industryCycleReviews"][
        0
    ]
    assert not cycle.get("persistedRiskFlags")
    assert cycle["leftDiscountScale"] == 0


def test_newer_earnings_doubt_does_not_mask_older_sector_risk():
    recent = risk_history("FORWARD_PLANS_ONLY", days=1)
    recent["report"]["industryCycleReviews"][0].update(
        riskLevel="UNPROVEN",
        confirmedRiskFlags=[],
        confirmedRiskAt={},
        forwardPlansOnly=True,
    )
    cycle = validate_with_history([risk_history("FINANCING_COOLING"), recent])[
        "industryCycleReviews"
    ][0]
    assert "FINANCING_COOLING" in cycle["persistedRiskFlags"]
    assert (
        cycle["confirmedRiskAt"]["FINANCING_COOLING"]
        == (NOW - timedelta(days=5)).isoformat()
    )


@pytest.mark.parametrize("flag", SECTOR_RISKS + EARNINGS_RISKS)
def test_current_verified_direction_recovers_only_earnings_risks(monkeypatch, flag):
    from backend.macro import research_sanitize

    # Isolate the report's sector/direction orchestration; fact verification and
    # actual price propagation have separate production-path regression tests.
    monkeypatch.setattr(
        research_sanitize,
        "sanitize_value_directions",
        lambda *args, **kwargs: [
            {
                "key": "verified-company",
                "reviewIndustryIds": ["ROBOT"],
                "industryCycle": {
                    "stage": "EARNINGS_DELIVERY",
                    "earningsVerified": True,
                    "riskFlags": [],
                },
                "plans": [],
            }
        ],
    )
    cycle = validate_with_history([risk_history(flag)])["industryCycleReviews"][0]
    assert cycle.get("earningsVerified") is False  # never invent sector earnings
    if flag in SECTOR_RISKS:
        assert cycle["persistedRiskFlags"] == [flag]
        assert cycle["riskLevel"] == "SUSPECTED"
        assert cycle["leftDiscountScale"] == 0.5
        assert (
            "个别企业当期盈利已核实，但近14日行业级风险观察尚未到期解除"
            in cycle["evidenceSummary"]
        )
    else:
        assert not cycle.get("persistedRiskFlags")
        assert cycle["leftDiscountScale"] == 0


def test_public_projection_preserves_computed_quote_support():
    from backend.macro.daily_research import _public_industry_cycle

    actual = _public_industry_cycle(
        {"riskChecks": [{"kind": "ORDER_CUTS", "claimSupported": False}]}
    )
    assert actual["riskChecks"][0]["claimSupported"] is False
