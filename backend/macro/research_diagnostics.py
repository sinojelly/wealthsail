"""Closed, content-free metadata shared by validation, storage and audit."""

import re

FAILURE_STAGES = frozenset(
    {"MODEL_EXECUTION", "SOURCE_VERIFICATION", "REPORT_VALIDATION", "PERSISTING"}
)
REASON_CODES = frozenset(
    {
        "DIRECTION_VALIDATION_FAILED",
        "FUND_PLAN_INVALID",
        "SCHEDULE_DATE_INVALID",
        "SCHEMA_VALIDATION_FAILED",
        "SOURCE_DATE_INVALID",
        "SOURCE_REFERENCE_INVALID",
        "TIMEOUT",
        "NETWORK_UNAVAILABLE",
        "PAYLOAD_VALIDATION_FAILED",
        "UNEXPECTED_ERROR",
        "CANCELLED",
        "PIPELINE_RUNTIME_EXPIRED",
    }
)
RULE_IDS = frozenset(
    {
        "INDUSTRY_REVIEW_DUPLICATE",
        "INDUSTRY_REVIEW_COVERAGE_MISSING",
        "INDUSTRY_RISK_CHECK_DUPLICATE",
        "INDUSTRY_RISK_CHECK_MISSING",
        "INDUSTRY_RISK_SOURCE_MISSING",
        "INDUSTRY_CYCLE_SOURCE_INVALID",
        "DIRECTION_INDUSTRY_REVIEW_UNKNOWN",
        "DIRECTION_DECLARED_INDUSTRY_UNKNOWN",
        "DIRECTION_PLAN_SECTOR_UNREVIEWED",
        "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED",
        "INDUSTRY_REVIEW_BUDGET_EXCEEDED",
        "RESEARCH_INPUT_BUDGET_EXCEEDED",
        "DIRECTION_MACRO_REFERENCE_UNKNOWN",
        "DIRECTION_CALIBRATION_RULE_UNKNOWN",
        "DIRECTION_EVIDENCE_INSUFFICIENT",
    }
)


def is_safe_item_index(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9999


def safe_error_class(value):
    if value is None:
        return None
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value)
        else "ResearchError"
    )


def _known(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def sanitize_missing_industry_codes(value):
    """Keep only bounded server taxonomy codes, never a model's unknown labels."""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return None
    from backend.macro.industry_contract import STANDARD_INDUSTRY_CODES

    codes = sorted(
        {item for item in value if isinstance(item, str) and item in STANDARD_INDUSTRY_CODES}
    )[:10]
    return codes or None


def sanitize_failure_diagnostics(
    *,
    stage=None,
    reason_code=None,
    rule_id=None,
    item_index=None,
    missing_industry_codes=None,
) -> dict:
    """Drop arbitrary text rather than letting optional diagnostics fail a run."""
    result = {
        "failure_stage": _known(stage, FAILURE_STAGES),
        "failure_reason_code": _known(reason_code, REASON_CODES),
        "failure_rule_id": _known(rule_id, RULE_IDS),
        "failure_item_index": item_index if is_safe_item_index(item_index) else None,
    }
    codes = sanitize_missing_industry_codes(missing_industry_codes)
    if codes:
        result["failure_industry_codes"] = codes
    return result
