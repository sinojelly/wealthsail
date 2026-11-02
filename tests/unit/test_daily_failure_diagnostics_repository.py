"""A failed run's bounded diagnostics belong to its own displayed identity."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from backend.macro.daily_repository import finish_daily_run, read_daily_research
from test_daily_repository import NOW, run


DIAGNOSTICS = {
    "failure_stage": "REPORT_VALIDATION",
    "failure_reason_code": "PAYLOAD_VALIDATION_FAILED",
    "failure_rule_id": "INDUSTRY_RISK_CHECK_DUPLICATE",
    "failure_item_index": 3,
}
PUBLIC = {
    "failureStage": "REPORT_VALIDATION",
    "failureReasonCode": "PAYLOAD_VALIDATION_FAILED",
    "failureRuleId": "INDUSTRY_RISK_CHECK_DUPLICATE",
    "failureItemIndex": 3,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "UNCERTAIN"])
async def test_terminal_diagnostics_are_inserted_once_with_the_terminal_result(status):
    conn = AsyncMock()
    await finish_daily_run(
        conn, "run-1", NOW, status=status, error_class="ValueError", **DIAGNOSTICS
    )
    assert conn.execute.await_count == 1
    sql, *args = conn.execute.await_args.args
    assert "failure_stage" in sql and "failure_reason_code" in sql
    assert "failure_rule_id" in sql and "failure_item_index" in sql
    assert "ON CONFLICT(run_id) DO NOTHING" in sql
    assert args[-4:] == list(DIAGNOSTICS.values())


@pytest.mark.asyncio
async def test_ready_result_cannot_persist_failure_diagnostics():
    conn = AsyncMock()
    await finish_daily_run(
        conn, "run-1", NOW, status="READY", payload={"summary": "ready"}, **DIAGNOSTICS
    )
    assert conn.execute.await_args.args[-4:] == (None, None, None, None)


@pytest.mark.asyncio
async def test_repository_sanitizes_untrusted_diagnostics_before_persistence():
    conn = AsyncMock()
    await finish_daily_run(
        conn,
        "run-1",
        NOW,
        status="FAILED",
        failure_stage="private raw exception",
        failure_reason_code="password=secret",
        failure_rule_id="unbounded-untrusted-message",
        failure_item_index=True,
    )
    assert conn.execute.await_args.args[-4:] == (None, None, None, None)


@pytest.mark.asyncio
async def test_current_failure_uses_its_diagnostics_while_showing_an_older_report(
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=None),
    )
    old = run(
        run_id="old-ready",
        slot_date=(NOW - timedelta(days=1)).date(),
        status="READY",
        recorded_at=NOW - timedelta(days=1),
        payload={"summary": "previous success"},
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        run(status="FAILED", recorded_at=NOW, **DIAGNOSTICS),
        old,
    ]
    actual = await read_daily_research(conn, NOW)
    assert actual["runId"] == "run-1" and actual["reportRunId"] == "old-ready"
    assert {key: actual[key] for key in PUBLIC} == PUBLIC


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline_run_id", ["run-1", "another-run", None])
async def test_pipeline_only_exposes_matching_latest_terminal_diagnostics(
    monkeypatch, pipeline_run_id
):
    pipeline = {
        "state": "FAILED",
        "researchRunId": pipeline_run_id,
        "startedAt": NOW - timedelta(minutes=5),
        "finishedAt": NOW,
        "errorClass": "ValueError",
    }
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=pipeline),
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        run(status="FAILED", recorded_at=NOW, **DIAGNOSTICS),
        None,
    ]
    actual = await read_daily_research(conn, NOW)
    expected = PUBLIC if pipeline_run_id == "run-1" else dict.fromkeys(PUBLIC)
    assert {key: actual[key] for key in PUBLIC} == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "READY"])
async def test_non_failed_latest_result_does_not_expose_diagnostics(
    monkeypatch, status
):
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=None),
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        run(status=status, recorded_at=NOW if status else None, **DIAGNOSTICS),
        None,
    ]
    actual = await read_daily_research(conn, NOW)
    assert {key: actual[key] for key in PUBLIC} == dict.fromkeys(PUBLIC)


@pytest.mark.asyncio
async def test_read_projection_drops_unknown_codes_written_elsewhere(monkeypatch):
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=None),
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        run(
            status="FAILED",
            recorded_at=NOW,
            failure_stage="RAW_EXCEPTION",
            failure_reason_code="PRIVATE_CODE",
            failure_rule_id="RAW_MESSAGE",
            failure_item_index=-1,
        ),
        None,
    ]
    actual = await read_daily_research(conn, NOW)
    assert {key: actual[key] for key in PUBLIC} == dict.fromkeys(PUBLIC)
