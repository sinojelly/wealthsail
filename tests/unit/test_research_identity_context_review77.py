"""Execution proof requires the caller's run identity at every strict boundary."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from backend.macro import daily_research as strict
from backend.macro import research_acceptance as acceptance
from backend.macro import research_acceptance_replay as offline
from backend.macro import research_repair as repair
from tests.unit.test_research_cost_review75 import NOW, RUN, executed_checks, prepared

OTHER_RUN = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def verified_case():
    packet, raw, checks = prepared()
    return packet, raw, executed_checks(packet, checks)


def test_v2_strict_without_expected_run_cannot_trust_receipt_self_declared_identity():
    packet, raw, checks = verified_case()
    with pytest.raises(strict.ResearchValidationError) as rejected:
        strict.validate_report(raw, packet, checks, completed_at=NOW)
    assert rejected.value.rule_id == "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED"


@pytest.mark.parametrize("research_id", [OTHER_RUN, None])
def test_v2_strict_different_or_missing_run_cannot_authorize_not_found(research_id):
    packet, raw, checks = verified_case()
    with pytest.raises(strict.ResearchValidationError) as rejected:
        strict.validate_report(
            raw, packet, checks, completed_at=NOW, research_id=research_id
        )
    assert rejected.value.rule_id == "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED"


def test_v2_strict_exact_expected_run_keeps_original_verified_prices():
    packet, raw, checks = verified_case()
    before = deepcopy((packet, raw, checks))
    result = strict.validate_report(
        raw, packet, checks, completed_at=NOW, research_id=RUN
    )
    assert result["valueDirections"][0]["plans"]
    assert result["valueDirections"][0]["industryCycle"]["searchExecutionVerified"] is True
    assert (packet, raw, checks) == before


def test_acceptance_cannot_report_complete_for_another_runs_execution_receipt():
    packet, raw, checks = verified_case()
    result = acceptance.validate_report_outcome(
        raw, packet, checks, completed_at=NOW, research_id=OTHER_RUN
    )
    assert result.decision != "COMPLETE" and result.formal_payload is None
    assert result.accepted["valueDirections"] == []
    assert any(
        issue.rule_id == "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED"
        for issue in result.issues
    )


def test_acceptance_passes_same_identity_to_strict_and_every_component_probe(monkeypatch):
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
    assert strict.validate_report(
        raw, packet, checks, completed_at=NOW, research_id=RUN
    ) == legacy


def test_v1_acceptance_keeps_legacy_injected_validator_signature():
    from tests.unit.test_industrial_metals_research_regression import case

    packet, raw, checks = case()

    def legacy_validator(raw, packet, checks, *, completed_at):
        return strict.validate_report(raw, packet, checks, completed_at=completed_at)

    result = acceptance.validate_report_outcome(
        raw, packet, checks, research_id=RUN, completed_at=NOW,
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
async def test_real_offline_acceptance_uses_requested_run_not_receipt_identity(research_id):
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
    assert (result["decision"] == "COMPLETE") is (research_id == RUN)
    if research_id != RUN:
        assert result["accepted"]["valueDirections"] == []
