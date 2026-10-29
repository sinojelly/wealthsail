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

