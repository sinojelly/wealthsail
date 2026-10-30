"""Real PostgreSQL lease fencing and worker/API permission boundaries."""

import asyncio
from datetime import datetime, timedelta
import os
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.common.database import create_pool, system_connection
from backend.macro.daily_pipeline_repository import (
    claim_pipeline,
    advance_pipeline,
    read_pipeline,
)

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated DB required"),
]


def clock():
    return datetime(2090, 1, 1, 7, tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(
        days=uuid4().int % 100000
    )


async def test_concurrent_owners_cannot_collect_or_dispatch_the_same_pipeline():
    pool = await create_pool(
        DSN, min_size=1, max_size=2, runtime_role="wealthsail_worker"
    )
    now = clock()

    async def claim():
        async with system_connection(pool) as conn:
            return await claim_pipeline(conn, now)

    try:
        rows = await asyncio.gather(claim(), claim())
        assert sum(row is not None for row in rows) == 1
    finally:
        await pool.close()
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(
                "DELETE FROM research.daily_research_pipelines WHERE slot_date=$1",
                now.date(),
            )
        finally:
            await conn.close()


async def test_waiting_can_resume_but_prior_token_and_expired_model_owner_cannot_write():
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        old = await claim_pipeline(conn, now)
        assert await advance_pipeline(conn, old, now, stage="WAITING_SOURCES")
        resumed = await claim_pipeline(conn, now + timedelta(minutes=1))
        assert resumed is not None and resumed["lease_token"] != old["lease_token"]
        assert (
            await advance_pipeline(
                conn, old, now + timedelta(minutes=1), stage="MODEL_RUNNING"
            )
            is False
        )
        assert await advance_pipeline(
            conn, resumed, now + timedelta(minutes=1), stage="MODEL_RUNNING"
        )
        assert await claim_pipeline(conn, now + timedelta(minutes=2)) is None
        assert await claim_pipeline(conn, now + timedelta(minutes=32)) is None
        result = await read_pipeline(conn, now + timedelta(minutes=32))
        assert result["state"] == "UNCERTAIN"
        assert result["errorClass"] == "PipelineLeaseExpired"
        assert (
            await advance_pipeline(
                conn,
                resumed,
                now + timedelta(minutes=32),
                stage="PERSISTING",
                state="READY",
            )
            is False
        )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        assert (await read_pipeline(conn, now + timedelta(minutes=32)))[
            "state"
        ] == "UNCERTAIN"
        assert (
            await conn.fetchval(
                "SELECT has_table_privilege(current_user,'research.daily_research_pipelines','UPDATE')"
            )
            is False
        )
        assert (
            await conn.fetchval(
                "SELECT has_table_privilege(current_user,'research.daily_research_pipelines','INSERT')"
            )
            is False
        )
    finally:
        await transaction.rollback()
        await conn.close()
