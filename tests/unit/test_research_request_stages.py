"""Phase telemetry must stay separate from scheduled work and research validity."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.jobs import daily_research as job
from backend.macro import research_debug_repository as repo


@pytest.mark.asyncio
async def test_scheduled_research_phase_does_not_open_an_actor_queue_connection(
    monkeypatch,
):
    connection = AsyncMock(
        side_effect=AssertionError("scheduled stage must not access requests")
    )
    monkeypatch.setattr(job, "system_connection", connection)
    await job._explicit_request_stage(object(), None, "VERIFYING_OUTPUT")
    connection.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_request_phase_updates_its_own_request_and_telemetry_error_does_not_cancel_report(
    monkeypatch,
):
    pool, conn, identity = object(), object(), uuid4()

    @asynccontextmanager
    async def connection(supplied):
        assert supplied is pool
        yield conn

    monkeypatch.setattr(job, "system_connection", connection)
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(repo, "set_refresh_stage", update)
    await job._explicit_request_stage(pool, identity, "REPORT_VALIDATION")
    assert update.call_args.args[0] is conn
    assert update.call_args.args[1] == identity
    assert update.call_args.args[3] == "REPORT_VALIDATION"
    update.side_effect = RuntimeError("telemetry unavailable")
    await job._explicit_request_stage(pool, identity, "PERSISTING")
