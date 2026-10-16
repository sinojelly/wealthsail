"""Study checks read facts under each opted-in actor; no model or delivery."""

from contextlib import asynccontextmanager
from datetime import datetime
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest


@pytest.mark.asyncio
async def test_actor_failures_are_isolated_and_generation_is_explicit(monkeypatch):
    assert util.find_spec("backend.jobs.study_tracking"), "study worker missing"
    job = import_module("backend.jobs.study_tracking")
    rows = [
        {"id": uuid4(), "tenant_id": uuid4(), "user_id": uuid4(), "generation": 4}
        for _ in range(2)
    ]
    conn = AsyncMock()
    conn.fetch.return_value = rows
    scopes = []

    @asynccontextmanager
    async def system(pool):
        yield conn

    @asynccontextmanager
    async def actor(pool, user, tenant):
        scopes.append((tenant, user))
        yield conn

    check = AsyncMock(
        side_effect=[
            RuntimeError("private details"),
            {"stored": True, "result": {"baseline": False}},
        ]
    )
    monkeypatch.setattr(job, "system_connection", system)
    monkeypatch.setattr(job, "tenant_transaction", actor)
    monkeypatch.setattr(job, "check_study", check)
    stats = await job.sync_study_tracking(
        object(), datetime(2026, 10, 17, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert stats == {"checked": 1, "stored": 1, "failed": 1}
    assert scopes == [(row["tenant_id"], row["user_id"]) for row in rows]
    assert [call.args[4] for call in check.await_args_list] == [4, 4]
