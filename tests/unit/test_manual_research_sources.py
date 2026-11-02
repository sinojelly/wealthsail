"""Manual work uses a separate paid attempt and an exact fresh public batch."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.macro.alpha_repository import claim_alpha_batch, read_alpha_public
from backend.macro.daily_repository import claim_daily_run, read_daily_research
from tests.unit.test_daily_repository import NOW, run


@pytest.mark.asyncio
async def test_manual_source_claim_has_its_own_request_identity():
    request = uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": uuid4(), "run_mode": "MANUAL"}
    result = await claim_alpha_batch(conn, NOW, manual_request_id=request)
    assert result["run_mode"] == "MANUAL"
    sql, *args = conn.fetchrow.await_args.args
    assert "'MANUAL'" in sql and request in args


@pytest.mark.asyncio
async def test_missing_exact_manual_source_cannot_use_old_scheduled_cache():
    request = uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    result = await read_alpha_public(conn, NOW, manual_request_id=request)
    assert result["collectionState"] == "NOT_COLLECTED"
    assert result["catalysts"] == result["fundHistories"] == []
    for call in conn.fetchrow.await_args_list:
        assert "run_mode='MANUAL'" in call.args[0]
        assert request in call.args


@pytest.mark.asyncio
async def test_manual_run_does_not_use_scheduled_day_or_debug_mode():
    conn = AsyncMock()
    request, batch, identity = uuid4(), uuid4(), uuid4()
    conn.fetchrow.return_value = {"run_id": identity, "run_mode": "MANUAL"}
    result = await claim_daily_run(
        conn,
        NOW.replace(hour=5),
        {},
        input_hash="fixture",
        prompt_version="fixture",
        manual_request_id=request,
        source_batch_id=batch,
    )
    assert result["run_id"] == identity
    sql, *args = conn.fetchrow.await_args.args
    assert "'MANUAL'" in sql and request in args and batch in args
    assert NOW.replace(hour=5) in args


@pytest.mark.asyncio
async def test_two_request_modes_cannot_share_one_source_selection():
    with pytest.raises(ValueError):
        await read_alpha_public(
            AsyncMock(), NOW, debug_request_id=uuid4(), manual_request_id=uuid4()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["READY", "FAILED"])
async def test_new_manual_execution_supersedes_old_automatic_failure(
    monkeypatch, status
):
    from datetime import timedelta

    old = NOW - timedelta(minutes=20)
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(
            return_value={
                "state": "FAILED",
                "stage": "VERIFYING_OUTPUT",
                "startedAt": old,
                "finishedAt": old,
                "researchRunId": "automatic",
                "errorClass": "ResearchValidationError",
            }
        ),
    )
    latest = run(
        run_id="manual",
        run_mode="MANUAL",
        status=status,
        recorded_at=NOW,
        payload={"summary": "new report"} if status == "READY" else None,
        failure_stage="REPORT_VALIDATION",
        failure_reason_code="DIRECTION_VALIDATION_FAILED",
    )
    prior = run(
        run_id="automatic-ready",
        status="READY",
        run_mode="SCHEDULED",
        recorded_at=old,
        payload={"summary": "old report"},
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [latest] if status == "READY" else [latest, prior]
    result = await read_daily_research(conn, NOW)
    assert result["pipeline"] is None
    assert result["state"] == status and result["runId"] == "manual"
    assert result["runMode"] == "MANUAL"
    assert result["reportRunMode"] == ("MANUAL" if status == "READY" else "SCHEDULED")
    assert result["report"]["summary"] == (
        "new report" if status == "READY" else "old report"
    )
    if status == "FAILED":
        assert result["failureStage"] == "REPORT_VALIDATION"


@pytest.mark.asyncio
async def test_disabling_automatic_schedule_does_not_hide_current_manual_success(
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=None),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = run(
        run_mode="MANUAL",
        status="READY",
        recorded_at=NOW,
        payload={"summary": "manual"},
    )
    result = await read_daily_research(conn, NOW, enabled=False)
    assert result["scheduleState"] == "DISABLED"
    assert result["state"] == "READY"
    assert result["reportRunMode"] == "MANUAL"
