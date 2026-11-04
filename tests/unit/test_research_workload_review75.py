"""Cost observations are bounded metadata, never false hard-limit claims."""

from backend.macro.research_acceptance_repository import safe_outcome_summary
from backend.macro.research_acceptance import rejected_outcome
from tests.unit.test_daily_research import NOW


def summary(value):
    return safe_outcome_summary({
        **rejected_outcome("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", NOW).to_dict(),
        **value,
    })


def test_safe_acceptance_retains_measured_workload_and_unknown_read_counts():
    raw = {
        "researchWorkload": {
            "version": "research-workload-v1",
            "promptBytes": 12000,
            "modelElapsedMs": 2500,
            "sourceVerificationElapsedMs": 1200,
            "executionCoverage": "PARTIAL",
            "observedSearchQueries": 51,
            "observedPageOpens": None,
            "observedPageFinds": None,
            "searchSoftLimitExceeded": True,
            "preDispatchEnforcement": False,
            "providerRetries": None,
            "inputTokens": 5000,
            "cachedInputTokens": 4000,
            "outputTokens": 300,
            "PRIVATE_QUERY": "must not leave frozen private records",
        }
    }
    saved = summary(raw)["researchWorkload"]
    assert saved["observedSearchQueries"] == 51
    assert saved["observedPageOpens"] is None
    assert saved["preDispatchEnforcement"] is False
    assert saved["providerRetries"] is None
    assert saved["inputTokens"] == 5000 and saved["cachedInputTokens"] == 4000
    assert "PRIVATE_QUERY" not in saved


def test_invalid_measurements_do_not_become_zero_or_hard_budget_guarantees():
    raw = {
        "researchWorkload": {
            "version": "research-workload-v1",
            "promptBytes": True,
            "modelElapsedMs": -1,
            "observedSearchQueries": "unknown",
            "preDispatchEnforcement": True,
        }
    }
    saved = summary(raw)["researchWorkload"]
    assert "promptBytes" not in saved
    assert "modelElapsedMs" not in saved
    assert saved["observedSearchQueries"] is None
    assert saved["preDispatchEnforcement"] is False


def test_legacy_acceptance_does_not_invent_workload():
    assert "researchWorkload" not in summary({})


def test_malformed_optional_workload_cannot_veto_formal_persistence():
    saved = summary({"researchWorkload": {"version": "research-workload-v1", "executionCoverage": []}})
    assert saved["researchWorkload"]["executionCoverage"] == "UNAVAILABLE"
