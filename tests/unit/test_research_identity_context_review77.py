"""Execution proof requires the caller's run identity at every strict boundary."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from backend.macro import daily_research as strict
from backend.macro import research_acceptance as acceptance
from backend.macro import research_acceptance_replay as offline
from backend.macro import research_repair as repair
from backend.macro.research_assurance import verified_tasks
from backend.macro.research_topics import frozen_manifest
from tests.unit.test_research_cost_review75 import NOW, RUN, executed_checks, prepared

OTHER_RUN = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def verified_case():
    packet, raw, checks = prepared()
    return packet, raw, executed_checks(packet, checks)


def assert_unqualified_observation(direction, *, projected=False):
    assert direction["signal"] == "WATCH"
    assert direction["qualificationStatus"] == "OBSERVATION_ONLY"
    assert direction["investmentEligible"] is False
    if projected:
        # The acceptance DTO excludes all plan/pricing fields even when its
        # underlying formal report has an independently qualified fund plan.
        assert "plans" not in direction
        assert "quantitativeFundCodes" not in direction
    else:
        assert direction["plans"] == direction["quantitativeFundCodes"] == []
    assert direction["industryCycle"]["searchExecutionVerified"] is False
    assert (
        "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED"
        in direction["formalEligibilityReasons"]
    )
    assert direction["industryCycle"]["confirmedRiskFlags"]


def test_v2_strict_without_expected_run_cannot_trust_receipt_self_declared_identity():
    packet, raw, checks = verified_case()
    before = deepcopy((packet, raw, checks))
    result = strict.validate_report(raw, packet, checks, completed_at=NOW)
    assert_unqualified_observation(result["valueDirections"][0])
    assert verified_tasks(checks, frozen_manifest(packet), NOW) == set()
    assert (packet, raw, checks) == before


@pytest.mark.parametrize("research_id", [OTHER_RUN, None])
def test_v2_strict_different_or_missing_run_cannot_authorize_not_found(research_id):
    packet, raw, checks = verified_case()
    before = deepcopy((packet, raw, checks))
    result = strict.validate_report(
        raw, packet, checks, completed_at=NOW, research_id=research_id
    )
    assert_unqualified_observation(result["valueDirections"][0])
    assert (
        verified_tasks(checks, frozen_manifest(packet), NOW, research_id=research_id)
        == set()
    )
    assert (packet, raw, checks) == before


def test_v2_strict_exact_expected_run_keeps_original_verified_prices():
    packet, raw, checks = verified_case()
    before = deepcopy((packet, raw, checks))
    result = strict.validate_report(
        raw, packet, checks, completed_at=NOW, research_id=RUN
    )
    assert result["valueDirections"][0]["plans"]
    assert (
        result["valueDirections"][0]["industryCycle"]["searchExecutionVerified"] is True
    )
    anchors = {row["subjectId"]: row for row in packet["discoveryActionableZones"]}
    for plan in result["valueDirections"][0]["plans"]:
        anchor = anchors[plan["subjectId"]]
        for field in ("currentPrice", "priceBasis", "unit", "sourceUri", "asOf"):
            assert plan[field] == anchor[field]
    assert verified_tasks(checks, frozen_manifest(packet), NOW, research_id=RUN) == {
        row["taskId"] for row in frozen_manifest(packet)["tasks"]
    }
    assert (packet, raw, checks) == before


def test_acceptance_for_another_runs_receipt_delivers_only_unqualified_observation():
    packet, raw, checks = verified_case()
    result = acceptance.validate_report_outcome(
        raw, packet, checks, completed_at=NOW, research_id=OTHER_RUN
    )
    # Content can be complete enough for read-only delivery; that decision never
    # certifies the unrelated run's searches or grants investment eligibility.
    assert result.formal_payload is not None
    assert result.to_dict()["actionable"] is False
    assert_unqualified_observation(
        result.accepted["valueDirections"][0], projected=True
    )
    assert_unqualified_observation(result.formal_payload["valueDirections"][0])
    assert result.counts["fundPlans"]["acceptedCount"] == 0
    assert (
        verified_tasks(checks, frozen_manifest(packet), NOW, research_id=OTHER_RUN)
        == set()
    )


def test_acceptance_passes_same_identity_to_strict_and_every_component_probe(
    monkeypatch,
):
    packet, raw, checks = verified_case()
    original = strict._validate_report_core
    core = Mock(wraps=original)
    monkeypatch.setattr(strict, "_validate_report_core", core)
    raw["valueDirections"][0]["reviewIndustryIds"] = ["UNKNOWN_MODEL_LABEL"]

    acceptance.validate_report_outcome(
        raw, packet, checks, completed_at=NOW, research_id=RUN
    )

    assert core.call_count > 1
    assert all(call.kwargs.get("research_id") == RUN for call in core.call_args_list)


def test_v1_strict_keeps_legacy_behavior_with_optional_identity():
    from tests.unit.test_industrial_metals_research_regression import case

    packet, raw, checks = case()
    legacy = strict.validate_report(raw, packet, checks, completed_at=NOW)
    assert (
        strict.validate_report(raw, packet, checks, completed_at=NOW, research_id=RUN)
        == legacy
    )


def test_v1_acceptance_keeps_legacy_injected_validator_signature():
    from tests.unit.test_industrial_metals_research_regression import case

    packet, raw, checks = case()

    def legacy_validator(raw, packet, checks, *, completed_at):
        return strict.validate_report(raw, packet, checks, completed_at=completed_at)

    result = acceptance.validate_report_outcome(
        raw,
        packet,
        checks,
        research_id=RUN,
        completed_at=NOW,
        _strict_validator=legacy_validator,
    )
    assert result.decision == "COMPLETE"


def test_legacy_repair_final_strict_revalidation_preserves_source_identity(monkeypatch):
    from tests.unit.test_research_repair import fixture

    packet, raw, checks = fixture()
    validation = Mock(wraps=repair.validate_report)
    monkeypatch.setattr(repair, "validate_report", validation)

    result = repair.repair_report(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )

    assert result["strictPass"] is True
    validation.assert_called_once()
    assert validation.call_args.kwargs.get("research_id") == RUN


@pytest.mark.asyncio
@pytest.mark.parametrize("research_id", [RUN, OTHER_RUN])
async def test_real_offline_acceptance_uses_requested_run_not_receipt_identity(
    research_id,
):
    packet, raw, checks = verified_case()
    result = await offline.run_offline_validation(
        {
            "raw": raw,
            "packet": packet,
            "sourceChecks": checks,
            "researchId": research_id,
            "validatedAt": NOW.isoformat(),
        }
    )
    assert result["formalPublishable"] is True
    assert result["actionable"] is False
    direction = result["accepted"]["valueDirections"][0]
    if research_id == RUN:
        assert result["counts"]["fundPlans"]["acceptedCount"] == 2
        assert "plans" not in direction
        assert "quantitativeFundCodes" not in direction
        assert direction["industryCycle"]["searchExecutionVerified"] is True
    else:
        assert_unqualified_observation(direction, projected=True)
        assert result["counts"]["fundPlans"]["acceptedCount"] == 0
        assert (
            verified_tasks(
                checks, frozen_manifest(packet), NOW, research_id=research_id
            )
            == set()
        )
