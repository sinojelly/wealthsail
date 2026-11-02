"""Safe diagnostics must preserve the once-per-day dispatch boundary."""

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from backend.macro.daily_research import ResearchValidationError
from tests.unit.test_daily_research_job import NOW, isolate, settings


@pytest.mark.asyncio
async def test_business_failure_logs_and_inserts_same_safe_metadata_without_retry(
    monkeypatch, caplog
):
    mod, state = isolate(monkeypatch)

    def invalid(*args, **kwargs):
        raise ResearchValidationError(
            "DIRECTION_VALIDATION_FAILED",
            "PRIVATE-MODEL-TEXT",
            rule_id="INDUSTRY_REVIEW_COVERAGE_MISSING",
            item_index=3,
        )

    monkeypatch.setattr(mod, "validate_report", invalid)
    with caplog.at_level("WARNING"):
        result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "FAILED"
    terminal = state["terminal"][0]
    assert {
        key: terminal.get(key)
        for key in (
            "failure_stage",
            "failure_reason_code",
            "failure_rule_id",
            "failure_item_index",
        )
    } == {
        "failure_stage": "REPORT_VALIDATION",
        "failure_reason_code": "DIRECTION_VALIDATION_FAILED",
        "failure_rule_id": "INDUSTRY_REVIEW_COVERAGE_MISSING",
        "failure_item_index": 3,
    }
    assert state["terminal_in_transactions"] == [True]
    lines = [
        record.message
        for record in caplog.records
        if record.message.startswith("daily_research_failed ")
    ]
    assert len(lines) == 1
    assert (
        "rule_id=INDUSTRY_REVIEW_COVERAGE_MISSING item_index=3 run_id=one" in lines[0]
    )
    assert "PRIVATE-MODEL-TEXT" not in caplog.text
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "SKIPPED"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_tampered_exception_metadata_cannot_enter_logs_or_storage(
    monkeypatch, caplog
):
    mod, state = isolate(monkeypatch)
    error = ValueError("PRIVATE-TEXT")
    error.reason_code = "PRIVATE-REASON\nINJECTED"
    error.rule_id = "PRIVATE-RULE"
    error.item_index = True
    monkeypatch.setattr(
        mod, "validate_report", lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )
    with caplog.at_level("WARNING"):
        await mod.sync_daily_research(object(), settings(), NOW)
    assert "PRIVATE" not in caplog.text
    assert (
        state["terminal"][0].get("failure_reason_code") == "PAYLOAD_VALIDATION_FAILED"
    )
    assert state["terminal"][0].get("failure_rule_id") is None
    assert state["terminal"][0].get("failure_item_index") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,reason",
    [(TimeoutError(), "TIMEOUT"), (asyncio.CancelledError(), "CANCELLED")],
)
async def test_timeout_and_cancel_persist_uncertain_diagnostics(
    monkeypatch, error, reason
):
    mod, state = isolate(monkeypatch)
    mod.run_codex_research.side_effect = error
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await mod.sync_daily_research(object(), settings(), NOW)
    else:
        assert (await mod.sync_daily_research(object(), settings(), NOW))[
            "status"
        ] == "UNCERTAIN"
    assert state["terminal"][0].get("failure_stage") == "MODEL_EXECUTION"
    assert state["terminal"][0].get("failure_reason_code") == reason


@pytest.mark.asyncio
async def test_persistence_timeout_is_logged_uncertain_and_never_reinvokes_model(
    monkeypatch, caplog
):
    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(
        mod, "finish_daily_run", AsyncMock(side_effect=TimeoutError("PRIVATE-DB-DSN"))
    )
    with caplog.at_level("WARNING"):
        result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result == {"status": "UNCERTAIN", "errorClass": "PipelineRuntimeExpired"}
    assert (
        "daily_research_persist_failed stage=PERSISTING error_class=TimeoutError run_id=one"
        in caplog.text
    )
    assert "PRIVATE-DB-DSN" not in caplog.text
    assert state["terminal"] == []
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "SKIPPED"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_persist_cancellation_summary_distinguishes_runtime_expiry(
    monkeypatch, expired
):
    mod, state = isolate(monkeypatch)
    snapshot = Mock()
    monkeypatch.setattr(mod, "write_research_diagnostic", snapshot)
    monkeypatch.setattr(
        mod, "finish_daily_run", AsyncMock(side_effect=asyncio.CancelledError())
    )
    pipeline = {
        "id": "pipeline",
        "stage": "MODEL_RUNNING",
        "runtime_deadline_at": NOW + timedelta(hours=1),
        "_runtime_deadline": 0 if expired else float("inf"),
    }
    with pytest.raises(asyncio.CancelledError):
        await mod._execute_daily_research(
            object(),
            settings(),
            NOW,
            packet={"asOf": NOW.isoformat()},
            pipeline=pipeline,
        )
    fields = snapshot.call_args.kwargs
    assert fields["stage"] == "PERSISTING"
    assert fields["report_status"] == "UNCERTAIN"
    assert fields["reason_code"] == (
        "PIPELINE_RUNTIME_EXPIRED" if expired else "CANCELLED"
    )
    assert not pipeline.get("_terminal_written")
    assert state["consumed"] is True
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_oversized_model_output_still_leaves_small_failure_summary(
    monkeypatch, tmp_path
):
    mod, state = isolate(monkeypatch)
    identity = uuid4()

    async def claim(*args, **kwargs):
        state["consumed"] = True
        return {"run_id": identity}

    monkeypatch.setattr(mod, "claim_daily_run", claim)
    mod.run_codex_research.side_effect = None
    mod.run_codex_research.return_value = {"summary": "字" * 1_000_000}

    def invalid(*args, **kwargs):
        raise ResearchValidationError("SCHEMA_VALIDATION_FAILED", "PRIVATE-RAW")

    monkeypatch.setattr(mod, "validate_report", invalid)
    await mod.sync_daily_research(
        object(), settings(daily_research_audit_dir=str(tmp_path)), NOW
    )
    summary = json.loads((tmp_path / f"{identity}.diagnostic.json").read_text("utf-8"))
    assert summary["reasonCode"] == "SCHEMA_VALIDATION_FAILED"
    assert (
        summary["stage"] == "REPORT_VALIDATION" and summary["reportStatus"] == "FAILED"
    )
    assert not (tmp_path / f"{identity}.json").exists()
    assert "PRIVATE" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_service_warns_about_disabled_audit_once_per_start(monkeypatch, caplog):
    mod, _ = isolate(monkeypatch)
    stopped = asyncio.Event()
    stopped.set()
    with caplog.at_level("WARNING"):
        await mod.run_daily_research_service(
            object(), settings(), stop_event=stopped, now_provider=lambda: NOW
        )
    assert caplog.text.count("daily_research_audit_disabled") == 1
