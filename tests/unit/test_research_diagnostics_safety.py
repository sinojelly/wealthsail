"""Failure metadata must never carry unreviewed model text."""

import pytest

from backend.macro.research_diagnostics import sanitize_failure_diagnostics


def test_known_failure_metadata_is_preserved():
    assert sanitize_failure_diagnostics(
        stage="REPORT_VALIDATION",
        reason_code="DIRECTION_VALIDATION_FAILED",
        rule_id="INDUSTRY_REVIEW_COVERAGE_MISSING",
        item_index=3,
    ) == {
        "failure_stage": "REPORT_VALIDATION",
        "failure_reason_code": "DIRECTION_VALIDATION_FAILED",
        "failure_rule_id": "INDUSTRY_REVIEW_COVERAGE_MISSING",
        "failure_item_index": 3,
    }


@pytest.mark.parametrize("unsafe", ["MODEL_SECRET", "x\nsecret", [], {}, 99, True])
def test_unknown_or_non_string_codes_are_dropped(unsafe):
    result = sanitize_failure_diagnostics(
        stage=unsafe, reason_code=unsafe, rule_id=unsafe
    )
    assert all(value is None for value in result.values())


@pytest.mark.parametrize("index", [True, False, -1, 10000, 1.0, "3", {}, []])
def test_item_index_accepts_only_bounded_non_boolean_integers(index):
    assert sanitize_failure_diagnostics(item_index=index)["failure_item_index"] is None


@pytest.mark.parametrize("index", [0, 9999])
def test_item_index_boundary_is_preserved(index):
    assert sanitize_failure_diagnostics(item_index=index)["failure_item_index"] == index
