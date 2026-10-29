"""Test structured diagnostic logging for daily research failures."""

import pytest

from tests.unit.test_daily_research_job import isolate, settings, NOW


@pytest.mark.asyncio
async def test_validation_failure_logs_stage_and_canonical_reason_code(monkeypatch, caplog):
    """Validation failure logs error_class, stage=REPORT_VALIDATION, and reason_code=PAYLOAD_VALIDATION_FAILED."""
    mod, state = isolate(monkeypatch)

    def failing_validate(*args, **kwargs):
        raise ValueError("direction note needs related current sources")

    monkeypatch.setattr(mod, "validate_report", failing_validate)

    with caplog.at_level("WARNING"):
        outcome = await mod.sync_daily_research(object(), settings(), NOW)

    assert outcome["status"] == "FAILED"
    assert (
        "daily_research_failed error_class=ValueError stage=REPORT_VALIDATION reason_code=PAYLOAD_VALIDATION_FAILED"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_source_check_failure_logs_source_verification_stage(monkeypatch, caplog):
    """Source check failure logs stage=SOURCE_VERIFICATION."""
    mod, state = isolate(monkeypatch)

    async def failing_source_check(*args, **kwargs):
        raise RuntimeError("source check network failed")

    monkeypatch.setattr(mod, "verify_research_sources", failing_source_check)

    with caplog.at_level("WARNING"):
        outcome = await mod.sync_daily_research(object(), settings(), NOW)

    assert outcome["status"] == "FAILED"
    assert (
        "daily_research_failed error_class=RuntimeError stage=SOURCE_VERIFICATION reason_code=RuntimeError"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_model_execution_failure_logs_model_execution_stage(monkeypatch, caplog):
    """Model execution failure logs stage=MODEL_EXECUTION and canonical reason_code."""
    mod, state = isolate(monkeypatch)

    def failing_run(*args, **kwargs):
        raise TimeoutError("CLI execution timed out")

    monkeypatch.setattr(mod, "run_codex_research", failing_run)

    with caplog.at_level("WARNING"):
        outcome = await mod.sync_daily_research(object(), settings(), NOW)

    assert outcome["status"] == "UNCERTAIN"
    assert (
        "daily_research_failed error_class=TimeoutError stage=MODEL_EXECUTION reason_code=TIMEOUT"
        in caplog.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason_code",
    [
        "SOURCE_REFERENCE_INVALID",
        "SOURCE_DATE_INVALID",
        "DIRECTION_VALIDATION_FAILED",
        "FUND_PLAN_INVALID",
        "SCHEMA_VALIDATION_FAILED",
        "SCHEDULE_DATE_INVALID",
    ],
)
async def test_fine_grained_validation_failure_logs_specific_reason_code(monkeypatch, caplog, reason_code):
    """Specific ResearchValidationError logs error_class=ResearchValidationError and exact reason_code."""
    from backend.macro.daily_research import ResearchValidationError

    mod, state = isolate(monkeypatch)

    def failing_validate(*args, **kwargs):
        raise ResearchValidationError(reason_code, f"Detail for {reason_code}")

    monkeypatch.setattr(mod, "validate_report", failing_validate)

    with caplog.at_level("WARNING"):
        outcome = await mod.sync_daily_research(object(), settings(), NOW)

    assert outcome["status"] == "FAILED"
    expected = (
        f"daily_research_failed error_class=ResearchValidationError stage=REPORT_VALIDATION reason_code={reason_code}"
    )
    assert expected in caplog.text


def test_canonical_reason_code_mapping():
    """Unit test _canonical_reason_code direct mappings."""
    import json
    from backend.jobs.daily_research import _canonical_reason_code
    from backend.macro.daily_research import ResearchValidationError

    # Custom reason_code attribute takes precedence
    err = ResearchValidationError("SOURCE_REFERENCE_INVALID", "broken ref")
    assert _canonical_reason_code(err) == "SOURCE_REFERENCE_INVALID"

    # Timeouts
    assert _canonical_reason_code(TimeoutError("slow")) == "TIMEOUT"

    # Network
    assert _canonical_reason_code(ConnectionResetError("reset")) == "NETWORK_UNAVAILABLE"

    # JSON / Schema
    try:
        json.loads("invalid json")
    except Exception as exc:
        assert _canonical_reason_code(exc) == "SCHEMA_VALIDATION_FAILED"

    # Generic ValueError fallback
    assert _canonical_reason_code(ValueError("unknown issue")) == "PAYLOAD_VALIDATION_FAILED"


