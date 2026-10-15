"""Evidence coverage and limited research conclusions are separate judgments."""

from copy import deepcopy

import pytest

from backend.macro.direction_models import direction_model, profile


ASHARE = {"id": "a-share:broad", "market_id": "a-share"}


def index_evidence(identity="hs300", *, distance=0.1, momentum=0.2):
    return {
        "indexId": identity,
        "trend": {
            "state": "READY",
            "ma200": 100,
            "distanceMA200": distance,
            "momentum63": momentum,
        },
        "valuation": {"state": "READY"},
        "earnings": {"state": "READY"},
    }


def complete_inputs():
    context = {"items": [{"code": "PMI", "state": "READY", "value": 50, "unit": ""}]}
    series = [
        {
            "code": code,
            "state": "READY",
            "frequency": frequency,
            "unit": "%",
            "value": value,
            "changes": {"periodChange": 0, "change5Samples": 0, "changeUnit": unit},
        }
        for code, frequency, value, unit in (
            ("M1_YOY", "MONTHLY", 3, "pp"),
            ("TSF_STOCK_YOY", "MONTHLY", 7, "pp"),
            ("CN_T10", "DAILY", 2, "bp"),
        )
    ]
    return context, series, [index_evidence()]


def test_all_required_evidence_ready_does_not_keep_recommendations_as_gaps():
    result = direction_model(ASHARE, *complete_inputs())
    assert result["qualificationState"] == "READY"
    assert result["missingRequiredEvidence"] == result["missingEvidence"] == []
    assert result["recommendedChecks"]
    assert result["watchFor"] == result["recommendedChecks"]
    assert result["researchState"] == "PRIORITY_RESEARCH"


def test_empty_direction_has_actual_required_codes_and_neutral_insufficient_research():
    result = direction_model(ASHARE, {}, [], [])
    assert result["missingRequiredEvidence"] == [
        "PMI",
        "M1_YOY",
        "TSF_STOCK_YOY",
        "CN_T10",
        "INDEX_TREND",
        "INDEX_VALUATION",
        "INDEX_EARNINGS",
    ]
    assert result["qualificationState"] == "INCOMPLETE"
    assert result["researchState"] == "INSUFFICIENT"
    assert result["failedConditions"] == []
    assert not set(result["recommendedChecks"]) & set(result["missingEvidence"])


def test_current_positive_trend_is_a_limited_clue_despite_missing_earnings():
    evidence = index_evidence()
    evidence["earnings"]["state"] = "UNKNOWN"
    result = direction_model(ASHARE, {}, [], [evidence])
    assert result["researchState"] == "PRIORITY_RESEARCH"
    assert result["qualificationState"] == "INCOMPLETE"
    assert "INDEX_EARNINGS" in result["missingRequiredEvidence"]
    assert "INDEX_TREND" not in result["missingRequiredEvidence"]
    assert all(word not in str(result["supports"]) for word in ("由弱转强", "新增改善"))


@pytest.mark.parametrize(
    "distance,momentum", [(0, 0.2), (-0.1, 0.2), (0.1, 0), (0.1, -0.1)]
)
def test_known_failed_trend_is_a_condition_failure_not_missing_data(distance, momentum):
    context, series, _ = complete_inputs()
    result = direction_model(
        ASHARE, context, series, [index_evidence(distance=distance, momentum=momentum)]
    )
    assert result["missingRequiredEvidence"] == []
    assert result["qualificationState"] == "BLOCKED"
    assert result["researchState"] == "WAIT_CONDITIONS"
    assert result["failedConditions"] == [
        {
            "id": "INDEX_TREND",
            "label": "当前指数趋势",
            "reason": "当前尚未同时满足高于MA200和63期正动量。",
            "evidenceIds": ["hs300"],
        }
    ]


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf")])
def test_invalid_trend_scalar_is_missing_not_a_failed_rule(value):
    context, series, evidence = complete_inputs()
    evidence[0]["trend"]["momentum63"] = value
    result = direction_model(ASHARE, context, series, evidence)
    assert "INDEX_TREND" in result["missingRequiredEvidence"]
    assert result["failedConditions"] == []
    assert result["qualificationState"] == "INCOMPLETE"


def test_valid_index_can_be_studied_when_another_index_has_unknown_trend():
    evidence = [index_evidence(), index_evidence("other")]
    evidence[1]["trend"]["state"] = "UNKNOWN"
    result = direction_model(ASHARE, {}, [], evidence)
    assert result["researchState"] == "PRIORITY_RESEARCH"
    assert "INDEX_TREND" in result["missingRequiredEvidence"]
    assert any(row["evidenceIds"] == ["hs300"] for row in result["supports"])


def test_verified_counter_channel_does_not_certify_full_qualification():
    context, series, evidence = complete_inputs()
    series[-1]["changes"]["change5Samples"] = 45
    result = direction_model(ASHARE, context, series, evidence)
    assert result["researchState"] == "RISK_OBSERVATION"
    assert result["qualificationState"] == "BLOCKED"
    assert result["counterFactors"]


@pytest.mark.parametrize(
    "direction,extra",
    [
        ({"id": "gold:physical", "market_id": "gold"}, ["USD_FX"]),
        ({"id": "bond:government", "market_id": "bond"}, ["YIELD_CURVE_DURATION"]),
        (
            {"id": "bond:credit", "market_id": "bond"},
            ["YIELD_CURVE_DURATION", "CREDIT_SPREAD_DEFAULT"],
        ),
    ],
)
def test_unadapted_required_facts_remain_unknown_even_if_a_scalar_claims_ready(
    direction, extra
):
    items = [{"code": code, "state": "READY", "value": 1} for code in extra]
    result = direction_model(direction, {"items": items}, [], [])
    assert set(extra) <= set(result["requiredEvidence"])
    assert set(extra) <= set(result["missingRequiredEvidence"])
    assert result["qualificationState"] == "INCOMPLETE"
    if direction["id"] == "bond:government":
        assert "CREDIT_SPREAD_DEFAULT" not in result["requiredEvidence"]


def test_unsupported_direction_requires_real_local_evidence():
    result = direction_model({"id": "other:unknown"}, {}, [], [])
    assert result["missingRequiredEvidence"] == ["LOCAL_ASSET_EVIDENCE"]
    assert result["state"] == "UNKNOWN"
    assert result["researchState"] == "INSUFFICIENT"
    assert result["qualificationState"] == "INCOMPLETE"


def test_profile_and_inputs_are_not_mutated_between_requests():
    inputs = complete_inputs()
    before = deepcopy(inputs)
    first = direction_model(ASHARE, *inputs)
    first["requiredEvidence"].append("fake")
    assert inputs == before
    assert "fake" not in profile(ASHARE)[0]
