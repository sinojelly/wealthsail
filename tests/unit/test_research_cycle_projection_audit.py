"""Persisted acceptance projections retain proof without repairing bad structure."""

from copy import deepcopy
import json

import pytest

from backend.investment.fund_observation import _audit
from backend.macro.industry_cycle import RISK_CHECKS
from backend.macro.research_acceptance_repository import safe_outcome_summary
from tests.unit.test_research_acceptance_repository import outcome


def review():
    checks = [
        {"kind": kind, "status": "NOT_FOUND", "searchExecutionVerified": True}
        for kind in RISK_CHECKS
    ]
    checks[0] = {
        "kind": "FINANCING_COOLING",
        "status": "FOUND",
        "verification": "SOURCE_CHECKED",
        "claimSupported": True,
        "industryScopeVerified": True,
        "eventTimeVerification": "RECENT",
        "sourceIds": ["public-risk-source"],
        "privatePayload": "PRIVATE",
    }
    return {"industry": "ROBOT", "riskChecks": checks}


def persisted_reviews(value):
    # Saving and reading both apply the real projection. JSON simulates jsonb's
    # value boundary; no unprojected check is injected into the audit consumer.
    saved = safe_outcome_summary(outcome(accepted={"industryCycleReviews": [value]}))
    loaded = safe_outcome_summary(json.loads(json.dumps(saved)))
    return {"ROBOT": loaded["accepted"]["industryCycleReviews"]}


def test_persisted_five_checks_close_audit_with_verified_found_and_not_found_proof():
    projected = persisted_reviews(review())
    assert _audit(["ROBOT"], {"ROBOT"}, projected, {"searchExecutionVerified": True}) == (
        "STRUCTURE_AND_EXECUTION_VERIFIED", []
    )
    assert "PRIVATE" not in json.dumps(projected)


@pytest.mark.parametrize(
    "change",
    ["missing", "duplicate", "sixth", "unverified_found", "unverified_search"],
)
def test_projection_does_not_upgrade_incomplete_or_invalid_check_proof(change):
    value = deepcopy(review())
    checks = value["riskChecks"]
    if change == "missing":
        checks.pop()
    elif change == "duplicate":
        checks[-1] = deepcopy(checks[0])
    elif change == "sixth":
        checks.append({"kind": "UNKNOWN", "status": "NOT_FOUND"})
    elif change == "unverified_found":
        checks[0]["industryScopeVerified"] = False
    else:
        checks[1]["searchExecutionVerified"] = False
    status, missing = _audit(
        ["ROBOT"], {"ROBOT"}, persisted_reviews(value), {"searchExecutionVerified": True}
    )
    assert status == "PENDING_PROOF" and missing
