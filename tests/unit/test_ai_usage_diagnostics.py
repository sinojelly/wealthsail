"""Explain unavailable quota attribution without exposing account identity."""

import pytest

from backend.ai_usage.calculation import aggregate_task, quota_delta


def snapshot(**changes):
    return {
        "status": "READY",
        "accountFingerprint": "private-account-fingerprint",
        "authGeneration": "private-auth-generation",
        "observationId": "before-id",
        "observedAt": "2026-10-28T00:32:00+00:00",
        "planType": "plus",
        "windows": [
            {
                "key": "codex:primary",
                "label": "5小时",
                "usedPercent": 1,
                "windowMinutes": 300,
                "resetsAt": 1999999999,
            }
        ],
        "credits": None,
        **changes,
    }


def pair():
    before = snapshot()
    after = snapshot(observationId="after-id", observedAt="2026-10-28T00:36:00+00:00")
    after["windows"][0]["usedPercent"] = 7
    return before, after


def call(quota):
    return {"model": "codex", "status": "SUCCEEDED", "quota": quota}


@pytest.mark.parametrize("missing", ["before", "after", "both"])
def test_legacy_empty_capture_identifies_boundary_without_inventing_timeout(missing):
    before, after = pair()
    if missing in {"before", "both"}:
        before = {}
    if missing in {"after", "both"}:
        after = {}
    quota = quota_delta(before, after)
    reason = f"{missing.upper()}_MISSING"
    assert quota["credits"]["reason"] == reason
    if missing != "both":
        assert quota["windows"][0]["reason"] == reason
        assert quota["windows"][0]["consumedPercent"] is None
    assert quota["snapshots"]["before"]["status"] == (
        "MISSING" if not before else "READY"
    )
    assert "TIMEOUT" not in str(quota)


@pytest.mark.parametrize("stage", ["before", "after"])
@pytest.mark.parametrize(
    "reason", ["TIMEOUT", "UNAVAILABLE", "NOT_FRESH", "ERROR", "CANCELLED"]
)
def test_saved_capture_failure_is_explained_and_survives_single_call_aggregation(
    stage, reason
):
    before, after = pair()
    failure = {
        "status": "UNAVAILABLE",
        "diagnostic": {"reason": reason, "elapsedMs": 15001},
    }
    if stage == "before":
        before = failure
    else:
        after = failure
    quota = quota_delta(before, after)
    expected = f"{stage.upper()}_{reason}"
    assert quota["windows"][0]["reason"] == expected
    result = aggregate_task({}, [call(quota)])
    assert result["quota"]["windows"][0]["status"] == "UNKNOWN"
    assert result["quota"]["windows"][0]["reason"] == expected
    assert result["quota"]["credits"]["reason"] == expected
    assert "snapshots" not in result["quota"]
    assert quota["snapshots"][stage] == {
        "status": "UNAVAILABLE",
        "reason": reason,
        "observedAt": None,
        "elapsedMs": 15001,
    }


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("identity", "IDENTITY_MISSING"),
        ("time", "OBSERVATION_TIME_INVALID"),
        ("reused", "OBSERVATION_REUSED"),
        ("missing_window", "WINDOW_MISSING"),
        ("invalid_window", "WINDOW_VALUE_INVALID"),
    ],
)
def test_known_fields_explain_unknown_instead_of_flattening_to_incomplete(
    problem, reason
):
    before, after = pair()
    if problem == "identity":
        before.pop("accountFingerprint")
    elif problem == "time":
        after["observedAt"] = before["observedAt"]
    elif problem == "reused":
        after["observationId"] = before["observationId"]
    elif problem == "missing_window":
        after["windows"] = []
    else:
        after["windows"][0]["usedPercent"] = None
    quota = quota_delta(before, after)
    assert quota["windows"][0]["status"] == "UNKNOWN"
    assert quota["windows"][0]["reason"] == reason
    assert quota["windows"][0]["consumedPercent"] is None


def test_remaining_99_to_93_measures_six_points_independent_of_missing_credits():
    quota = quota_delta(*pair())
    assert quota["windows"][0]["consumedPercent"] == 6
    assert quota["windows"][0]["status"] == "ESTIMATED"
    assert quota["credits"] == {
        "status": "UNKNOWN",
        "consumed": None,
        "reason": "CREDITS_UNAVAILABLE",
    }
    result = aggregate_task({}, [call(quota)])
    assert result["quota"]["windows"][0]["consumedPercent"] == 6
    assert result["quota"]["credits"]["reason"] == "CREDITS_UNAVAILABLE"


def test_diagnostic_dto_whitelists_reasons_times_and_duration():
    before, after = pair()
    before.update(
        email="must-not-leak@example.test",
        raw="secret",
        diagnostic={
            "reason": "private supplier response",
            "elapsedMs": -1,
            "token": "secret",
        },
    )
    after["diagnostic"] = {"reason": None, "elapsedMs": 12003.5}
    quota = quota_delta(before, after)
    assert quota["snapshots"]["before"] == {
        "status": "READY",
        "reason": None,
        "observedAt": before["observedAt"],
        "elapsedMs": None,
    }
    assert quota["snapshots"]["after"]["elapsedMs"] is None
    safe = str(quota["snapshots"])
    for private in (
        "must-not-leak",
        "private-account",
        "private-auth",
        "secret",
        "supplier",
    ):
        assert private not in safe


@pytest.mark.parametrize("reason", [[], {}, True, 1.5])
def test_non_text_diagnostic_reason_does_not_break_history(reason):
    before, after = pair()
    before["diagnostic"] = {"reason": reason}
    quota = quota_delta(before, after)
    assert quota["snapshots"]["before"]["reason"] is None
    assert quota["windows"][0]["consumedPercent"] == 6


@pytest.mark.parametrize(
    "status", ["ACCOUNT_CHANGED", "PERIOD_CHANGED", "RESET_OR_TOPUP", "CONCURRENT"]
)
def test_aggregate_retains_specific_unattributable_status(status):
    quota = quota_delta(*pair())
    quota["windows"][0].update(status=status, consumedPercent=None)
    quota["credits"].update(status=status, consumed=None)
    quota["credits"].pop("reason", None)
    result = aggregate_task({}, [call(quota)])
    assert result["quota"]["windows"][0]["status"] == status
    assert result["quota"]["credits"]["status"] == status


def test_different_failure_reasons_require_expansion_instead_of_mislabeling_task():
    before, after = pair()
    first = quota_delta({}, after)
    second = quota_delta(before, {})
    result = aggregate_task({}, [call(first), call(second)])
    assert result["quota"]["windows"][0]["status"] == "INCOMPLETE"
    assert result["quota"]["windows"][0].get("reason") is None
    assert result["quota"]["windows"][0]["consumedPercent"] is None
