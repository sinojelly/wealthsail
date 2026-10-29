"""Test structured diagnostic logging for daily research failures."""

import pytest

from tests.unit.test_daily_research_job import isolate, settings, NOW


@pytest.mark.asyncio
async def test_validation_failure_logs_stage_and_specific_reason(monkeypatch, caplog):
    """Validation failure logs error_class, stage=VALIDATION_FAILED, and rule reason."""
    mod, state = isolate(monkeypatch)

    def failing_validate(*args, **kwargs):
        raise ValueError("direction note needs related current sources")

    monkeypatch.setattr(mod, "validate_report", failing_validate)

    with caplog.at_level("WARNING"):
        outcome = await mod.sync_daily_research(object(), settings(), NOW)

    assert outcome["status"] == "FAILED"
    assert (
        "daily_research_failed error_class=ValueError stage=VALIDATION_FAILED reason=direction note needs related current sources"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_source_check_failure_logs_source_check_failed_stage(monkeypatch, caplog):
    """Source check failure logs stage=SOURCE_CHECK_FAILED."""
    mod, state = isolate(monkeypatch)

    async def failing_source_check(*args, **kwargs):
        raise RuntimeError("source check network failed")

    monkeypatch.setattr(mod, "verify_research_sources", failing_source_check)

    with caplog.at_level("WARNING"):
        outcome = await mod.sync_daily_research(object(), settings(), NOW)

    assert outcome["status"] == "FAILED"
    assert (
        "daily_research_failed error_class=RuntimeError stage=SOURCE_CHECK_FAILED reason=source check network failed"
        in caplog.text
    )
