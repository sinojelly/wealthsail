"""Frozen research, executable conditions, and honest retrospective evidence."""

from datetime import datetime, timedelta
from importlib import import_module, util
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 17, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


def engine():
    assert util.find_spec("backend.investment.study"), "study engine is missing"
    return import_module("backend.investment.study")


def rule(**changes):
    return {
        "metric": "PMI",
        "operator": "GT",
        "threshold": "50",
        "effect": "STRENGTHEN",
        **changes,
    }


def observation(value=50.1, **changes):
    return {
        "metric": "PMI",
        "value": value,
        "unit": "指数点",
        "asOf": "2026-09",
        "retrievedAt": (NOW - timedelta(days=1)).isoformat(),
        "sourceUri": "https://www.stats.gov.cn/release",
        "verification": "VERIFIED",
        "state": "READY",
        **changes,
    }


def version(**changes):
    return {
        "createdAt": (NOW - timedelta(days=1)).isoformat(),
        "expiresOn": "2026-11-15",
        "conditions": [rule()],
        "observations": [observation(49.8)],
        **changes,
    }


def test_following_freezes_the_original_public_source_and_does_not_copy_private_fields():
    origin = {
        "originType": "DAILY_EVENT",
        "reportRunId": "public-run",
        "eventId": "event-1",
        "reportAsOf": (NOW - timedelta(days=1)).isoformat(),
        "event": {
            "title": "供应政策宣布",
            "summary": "许可调整仍需实际供应证明",
            "phase": "ANNOUNCED",
            "importance": "HIGH",
            "affectedDirectionIds": ["us:broad"],
            "unknowns": ["实际交付尚未核实"],
            "channels": ["供应→价格"],
            "watchFor": ["实际到货"],
            "scenarios": [
                {"condition": "到货增加", "impact": "成本或缓解", "counter": "运力限制"}
            ],
        },
        "sources": [
            {
                "id": "s1",
                "url": "https://ofac.treasury.gov/policy",
                "quote": "Original quotation.",
                "publishedAt": None,
            }
        ],
        "observations": [observation()],
        "tenantId": "private",
        "password": "secret",
    }
    result = engine().freeze_hypothesis(origin, now=NOW)
    origin["event"]["summary"] = "A later rewrite"
    assert result["thesis"] == "许可调整仍需实际供应证明"
    assert result["origin"]["reportRunId"] == "public-run"
    assert result["expiresOn"] == "2026-11-16"
    assert result["sources"][0]["quote"] == "Original quotation."
    assert "secret" not in str(result) and "private" not in str(result)
    assert result["invalidationConditions"] == []
    assert result["phase"] == "ANNOUNCED"
    assert result["importance"] == "HIGH"
    assert result["affectedDirectionIds"] == ["us:broad"]
    assert result["unknowns"] == ["实际交付尚未核实"]


def test_server_plan_is_frozen_and_survives_later_condition_only_revision():
    plan = {
        "subjectKind": "index",
        "subjectId": "hs300",
        "priceBasis": "INDEX_POINTS",
        "selectedTrack": "LEFT_VALUE",
        "currentPrice": "4000.125",
        "tracks": [
            {
                "track": "LEFT_VALUE",
                "entryZoneMin": "3900.000",
                "entryZoneMax": "4000.000",
                "stopLossPrice": "3800.000",
            }
        ],
    }
    origin = {"title": "左侧研究", "actionablePlan": plan}
    result = engine().freeze_hypothesis(origin, now=NOW)
    plan["tracks"][0]["entryZoneMin"] = "9999"
    assert result["actionablePlan"]["tracks"][0]["entryZoneMin"] == "3900.000"
    revised = engine().freeze_hypothesis({**result, "summary": "修改假设"}, now=NOW)
    assert revised["actionablePlan"] == result["actionablePlan"]


@pytest.mark.parametrize(
    "changes",
    [
        {"threshold": "NaN"},
        {"threshold": True},
        {"metric": "ARBITRARY_SCRIPT"},
        {"unit": "%"},
        {"metric": "INDEX_PE", "subjectKey": "unmapped"},
        {"metric": "INDEX_PE"},
        {"operator": "EXECUTE"},
    ],
)
def test_executable_conditions_reject_nonfinite_wrong_units_or_unknown_identity(
    changes,
):
    with pytest.raises(ValueError):
        engine().StudyCondition.model_validate(rule(**changes))


def test_index_condition_uses_percentage_points_without_guessing_units():
    condition = engine().StudyCondition.model_validate(
        rule(metric="INDEX_MOMENTUM63", subjectKey="hs300", threshold="3", unit="%")
    )
    assert condition.model_dump(by_alias=True)["threshold"] == 3


def test_explicitly_cleared_counter_factors_do_not_reappear_from_old_scenarios():
    original = engine().freeze_hypothesis(
        {"event": {"scenarios": [{"counter": "原反方条件"}]}}, now=NOW
    )
    assert original["counterFactors"] == ["原反方条件"]
    revised = engine().freeze_hypothesis({**original, "counterFactors": []}, now=NOW)
    assert revised["counterFactors"] == []


def test_first_observation_records_a_baseline_instead_of_a_newly_triggered_claim():
    result = engine().evaluate_conditions(version(), None, [observation()], now=NOW)
    assert result["baseline"] is True and result["state"] == "OBSERVING"
    assert result["conditionResults"][0]["result"] == "TRUE"


@pytest.mark.parametrize("field", ["retrievedAt", "availableAt"])
def test_older_revision_of_same_period_cannot_be_a_new_condition_trigger(field):
    old = observation(
        49,
        retrievedAt=(NOW - timedelta(hours=1)).isoformat(),
        availableAt=(NOW - timedelta(hours=2)).isoformat(),
    )
    baseline = engine().evaluate_conditions(version(), None, [old], now=NOW)
    older_revision = {
        **old,
        "value": "51",
        field: (NOW - timedelta(hours=3)).isoformat(),
    }
    result = engine().evaluate_conditions(
        version(), baseline, [older_revision], now=NOW
    )
    assert result["state"] == "OBSERVING"
    condition = result["conditionResults"][0]
    assert condition["result"] == "UNKNOWN"
    assert condition["lastKnownResult"] == "FALSE"
    assert condition["lastKnownObservation"]["value"] == "49"


def test_review_does_not_call_an_older_revision_the_latest_comparable_fact():
    initial = observation(49, retrievedAt=(NOW - timedelta(hours=1)).isoformat())
    old_revision = {
        **initial,
        "value": "51",
        "retrievedAt": (NOW - timedelta(hours=2)).isoformat(),
    }
    result = engine().review_evidence(
        version(createdAt=NOW.isoformat()), [initial], [old_revision], [], now=NOW
    )
    assert result["observations"][0]["state"] == "UNKNOWN"
    assert result["observations"][0]["change"] is None
    assert result["observations"][0]["currentValue"] is None


def test_new_verified_condition_can_strengthen_then_weaken_without_rewriting_previous_check():
    prior = engine().evaluate_conditions(
        version(), None, [observation(49.8, asOf="2026-08")], now=NOW
    )
    before = str(prior)
    positive = engine().evaluate_conditions(
        version(), prior, [observation(50.1)], now=NOW
    )
    negative = engine().evaluate_conditions(
        version(), positive, [observation(49.5, retrievedAt=NOW.isoformat())], now=NOW
    )
    assert positive["state"] == "STRENGTHENED"
    assert negative["state"] == "WEAKENED"
    assert str(prior) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"value": float("nan")},
        {"unit": "%"},
        {"asOf": "2026-11"},
        {"retrievedAt": (NOW + timedelta(seconds=1)).isoformat()},
        {"state": "STALE"},
        {"verification": "UNVERIFIED"},
        {"asOf": "2026-03"},
        {"asOf": "2026-08"},
    ],
)
def test_unknown_stale_future_or_regressed_evidence_cannot_weaken_or_invalidate(
    changes,
):
    v = version(conditions=[rule(effect="INVALIDATE")])
    previous = engine().evaluate_conditions(v, None, [observation(49.8)], now=NOW)
    result = engine().evaluate_conditions(
        v, previous, [observation(**changes)], now=NOW
    )
    assert result["conditionResults"][0]["result"] == "UNKNOWN"
    assert result["state"] == "OBSERVING"


def test_first_valid_value_after_unknown_is_still_a_baseline_not_a_trigger():
    previous = engine().evaluate_conditions(version(), None, [], now=NOW)
    result = engine().evaluate_conditions(version(), previous, [observation()], now=NOW)
    assert result["state"] == "OBSERVING"
    assert result["conditionResults"][0]["result"] == "TRUE"


def test_repeated_collection_time_does_not_create_a_new_observation_identity():
    one = engine().evaluate_conditions(version(), None, [observation()], now=NOW)
    two = engine().evaluate_conditions(
        version(), one, [observation(retrievedAt=NOW.isoformat())], now=NOW
    )
    assert one["inputHash"] == two["inputHash"]
    assert two["hasChange"] is False


def test_natural_language_and_expired_period_do_not_certify_hypothesis_invalidation():
    manual = engine().evaluate_conditions(
        version(conditions=[], watchFor=["航运恢复"]), None, [observation()], now=NOW
    )
    expired = engine().evaluate_conditions(
        version(expiresOn="2026-10-16"), None, [observation()], now=NOW
    )
    assert manual["quality"] == "MANUAL_REVIEW_REQUIRED"
    assert expired["state"] == "ENDED" and expired["state"] != "INVALIDATED"
    assert expired["conditionResults"][0]["result"] == "NOT_DUE"


def test_conflicting_observations_are_unknown_instead_of_selecting_convenient_support():
    result = engine().evaluate_conditions(
        version(), None, [observation(49), observation(51)], now=NOW
    )
    assert result["conditionResults"][0]["result"] == "UNKNOWN"


def test_research_review_compares_same_units_and_never_invents_investment_returns():
    result = engine().review_evidence(
        version(), [observation(49.8)], [observation(50.1)], [], now=NOW
    )
    assert result["observations"][0]["change"] == pytest.approx(0.3)
    assert result["performance"]["state"] == "UNKNOWN"
    assert result["performance"]["netReturn"] is None
    assert result["performance"]["excessReturn"] is None
    assert result["transactions"] == []


def test_review_does_not_call_an_unconfirmed_fill_a_real_trade_or_a_price_change_causal():
    result = engine().review_evidence(
        version(),
        [],
        [observation()],
        [{"status": "DRAFT", "quantity": 10, "price": 100}],
        now=NOW,
    )
    assert result["transactions"] == []
    assert result["causalAttribution"] == "UNVERIFIED"


def test_missing_check_does_not_erase_the_last_verified_rule_baseline():
    before = engine().evaluate_conditions(
        version(), None, [observation(49.8, asOf="2026-08")], now=NOW
    )
    missing = engine().evaluate_conditions(version(), before, [], now=NOW)
    after = engine().evaluate_conditions(
        version(), missing, [observation(50.1)], now=NOW
    )
    assert missing["conditionResults"][0]["result"] == "UNKNOWN"
    assert after["state"] == "STRENGTHENED"


def test_later_retrieval_cannot_become_evidence_available_at_the_frozen_version():
    late = observation(49.8, retrievedAt=NOW.isoformat())
    result = engine().review_evidence(
        version(), [late], [observation(50.1)], [], now=NOW
    )
    assert result["observations"][0]["initialValue"] is None
    assert result["observations"][0]["change"] is None


def test_unknown_check_keeps_the_previous_verified_state_and_original_state_time():
    before = engine().evaluate_conditions(
        version(), None, [observation(49.8, asOf="2026-08")], now=NOW
    )
    positive = engine().evaluate_conditions(
        version(), before, [observation(50.1)], now=NOW
    )
    unknown = engine().evaluate_conditions(
        version(), positive, [], now=NOW + timedelta(hours=1)
    )
    assert unknown["state"] == "STRENGTHENED" and unknown["quality"] == "UNKNOWN"
    assert unknown["stateAsOf"] == NOW.isoformat()


def test_invalidation_requires_a_new_true_predeclared_condition_and_is_version_terminal():
    v = version(conditions=[rule(effect="INVALIDATE")])
    baseline = engine().evaluate_conditions(
        v, None, [observation(49.8, asOf="2026-08")], now=NOW
    )
    invalid = engine().evaluate_conditions(v, baseline, [observation()], now=NOW)
    later = engine().evaluate_conditions(v, invalid, [observation(49.7)], now=NOW)
    assert invalid["state"] == "INVALIDATED" and later["state"] == "INVALIDATED"
