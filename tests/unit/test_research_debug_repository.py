"""Debug research requests have their own identities and bounded visibility."""

from datetime import datetime, timedelta
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 18, 7, tzinfo=ZoneInfo("Asia/Shanghai"))


def engine():
    assert util.find_spec("backend.macro.research_debug_repository"), (
        "durable debug requests missing"
    )
    return import_module("backend.macro.research_debug_repository")


def row(**changes):
    value = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        command_key=uuid4(),
        requested_at=NOW,
        started_at=NOW,
        completed_at=None,
        status="RUNNING",
        batch_id=None,
        report_run_id=None,
        error_class=None,
    )
    value.update(changes)
    return value


def test_running_request_times_out_without_reusing_or_mutating_its_identity():
    original = row()
    output = engine().request_dto(original, NOW + timedelta(minutes=36))
    assert output["id"] == str(original["id"])
    assert output["status"] == "UNCERTAIN"
    assert output["errorClass"] == "ExecutionWindowExpired"
    assert original["status"] == "RUNNING"


def test_future_terminal_fields_are_not_revealed_before_they_exist():
    original = row(
        status="READY",
        completed_at=NOW + timedelta(minutes=1),
        report_run_id=uuid4(),
        batch_id=uuid4(),
    )
    output = engine().request_dto(original, NOW)
    assert output["status"] == "RUNNING"
    assert output["completedAt"] is output["reportRunId"] is output["batchId"] is None


@pytest.mark.asyncio
async def test_actor_request_lookup_hides_missing_or_other_actors():
    from backend.errors import NotFoundError

    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with pytest.raises(NotFoundError):
        await engine().read_refresh(conn, uuid4(), uuid4(), uuid4(), NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["RUNNING", "QUEUED", "unknown"])
async def test_request_finish_rejects_nonterminal_status(status):
    with pytest.raises(ValueError):
        await engine().finish_refresh(AsyncMock(), uuid4(), NOW, status=status)


@pytest.mark.asyncio
async def test_ready_request_cannot_claim_success_without_a_report():
    with pytest.raises(ValueError):
        await engine().finish_refresh(AsyncMock(), uuid4(), NOW, status="READY")


@pytest.mark.asyncio
async def test_debug_daily_claim_is_allowed_before_0830_without_backdating():
    from backend.macro.daily_repository import claim_daily_run

    conn = AsyncMock()
    conn.fetchrow.return_value = {"run_id": uuid4()}
    request, batch = uuid4(), uuid4()
    output = await claim_daily_run(
        conn,
        NOW,
        {},
        input_hash="a",
        prompt_version="b",
        debug_request_id=request,
        source_batch_id=batch,
    )
    assert output is not None
    arguments = conn.fetchrow.call_args.args
    assert NOW in arguments and request in arguments and batch in arguments
    assert not any(
        isinstance(value, datetime) and value.hour == 8 for value in arguments
    )


@pytest.mark.asyncio
async def test_debug_alpha_missing_exact_batch_cannot_fall_back_to_old_daily_cache():
    from backend.macro.alpha_repository import read_alpha_public

    conn = AsyncMock()
    conn.fetchrow.return_value = None
    output = await read_alpha_public(conn, NOW, debug_request_id=uuid4())
    assert output["catalysts"] == []
    assert output["collectionState"] == "NOT_COLLECTED"
