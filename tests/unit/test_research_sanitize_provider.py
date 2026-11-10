"""A confirmed successful model's parseable captured body remains publishable."""

import pytest

from backend.integrations.codex_research import CodexOutputError, decode_report
from backend.macro.daily_research import validate_report
from tests.unit.test_daily_research_job import isolate, settings, NOW


@pytest.mark.parametrize(
    "body",
    [
        '[{"title":"AI硬件","thesis":"新机会完整保留"}]',
        '"一段完整投研观点"',
        '{"summary":"旧观点","summary":"新观点"}',
    ],
)
def test_provider_decoder_accepts_parseable_json_shapes_and_duplicate_fields(body):
    parsed = decode_report(body)
    assert isinstance(parsed, dict)
    assert (
        validate_report(parsed, {"asOf": NOW.isoformat()}, {}, completed_at=NOW)[
            "executionStatus"
        ]
        == "COMPLETED"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        '[{"title":"AI硬件","thesis":"新机会完整保留"}]',
        '{"summary":"旧观点","summary":"新观点"}',
    ],
)
async def test_confirmed_successful_model_captured_json_is_published_without_another_call(
    monkeypatch, body
):
    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(mod, "validate_report", validate_report)
    calls = []

    async def captured(*args, lifecycle, **kwargs):
        calls.append(1)
        lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
        error = CodexOutputError("legacy decoder rejected the body shape")
        error.raw_output = body
        error.inference_succeeded = True
        error.usage = {"prompt_tokens": 521579, "completion_tokens": 17850}
        raise error

    monkeypatch.setattr(mod, "run_codex_research", captured)
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "READY"
    assert calls == [1]
    assert state["terminal"][0]["payload"]["executionStatus"] == "COMPLETED"
    assert state["terminal"][0]["payload"]["validationWarnings"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "succeeded,confirmed,body",
    [(False, True, "{}"), (True, False, "{}"), (True, True, "not-json")],
)
async def test_unfinished_or_unparseable_provider_failure_is_not_published(
    monkeypatch, succeeded, confirmed, body
):
    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(mod, "validate_report", validate_report)

    async def captured(*args, lifecycle, **kwargs):
        if confirmed:
            lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
        error = CodexOutputError("provider did not produce a reliable result")
        error.raw_output = body
        error.inference_succeeded = succeeded
        raise error

    monkeypatch.setattr(mod, "run_codex_research", captured)
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] != "READY"
    assert state["terminal"][0]["payload"] is None


@pytest.mark.asyncio
async def test_actual_job_publishes_repaired_unicode_without_source_verification_failure(
    monkeypatch,
):
    from tests.unit.test_daily_research_job import ConfirmedResearchMock

    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(mod, "validate_report", validate_report)
    raw = {"summary": "完整观点" + chr(0) + chr(0xD800), "sources": []}
    monkeypatch.setattr(
        mod, "run_codex_research", ConfirmedResearchMock(return_value=raw)
    )
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "READY"
    payload = state["terminal"][0]["payload"]
    assert payload["summary"] == "完整观点" + chr(0xFFFD)
    assert any(
        row["code"] == "TEXT_ENCODING_REPAIRED" for row in payload["validationWarnings"]
    )
