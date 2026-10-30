"""Real PostgreSQL lease fencing and worker/API permission boundaries."""

import asyncio
from datetime import datetime, timedelta
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.common.database import create_pool, system_connection
from backend.macro.daily_pipeline_repository import (
    claim_pipeline,
    advance_pipeline,
    read_pipeline,
    renew_pipeline_lease,
    release_pipeline_before_model,
    finish_source_wait_window,
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


@pytest.mark.parametrize("stage", ["COLLECTING", "VALIDATING_INPUT"])
async def test_expired_pre_model_owner_can_recover_without_changing_schedule_or_source(
    stage,
):
    from backend.macro.alpha_repository import claim_alpha_batch

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        batch = await claim_alpha_batch(conn, now)
        old = await claim_pipeline(conn, now, schedule_time="07:00")
        assert await advance_pipeline(
            conn, old, now, stage=stage, source_batch_id=batch["id"]
        )
        resumed = await claim_pipeline(
            conn, now + timedelta(minutes=31), schedule_time="08:00"
        )
        assert resumed["id"] == old["id"]
        assert resumed["started_at"] == old["started_at"]
        assert resumed["scheduled_for"] == old["scheduled_for"]
        assert resumed["source_batch_id"] == batch["id"]
        assert resumed["lease_token"] != old["lease_token"]
        assert not await advance_pipeline(
            conn, old, now + timedelta(minutes=31), stage="MODEL_RUNNING"
        )
        assert not await renew_pipeline_lease(conn, old, now + timedelta(minutes=31))
        assert not await release_pipeline_before_model(
            conn, old, now + timedelta(minutes=31)
        )
        assert await advance_pipeline(
            conn, resumed, now + timedelta(minutes=31), stage="VALIDATING_INPUT"
        )
    finally:
        await transaction.rollback()
        await conn.close()


async def test_heartbeat_keeps_a_slow_stage_owned_and_cannot_revive_an_expired_lease():
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        claim = await claim_pipeline(conn, now)
        assert await renew_pipeline_lease(conn, claim, now + timedelta(minutes=25))
        assert await claim_pipeline(conn, now + timedelta(minutes=31)) is None
        assert await advance_pipeline(
            conn, claim, now + timedelta(minutes=40), stage="VALIDATING_INPUT"
        )
        assert not await renew_pipeline_lease(conn, claim, now + timedelta(minutes=71))
        resumed = await claim_pipeline(conn, now + timedelta(minutes=71))
        assert resumed["lease_token"] != claim["lease_token"]
        assert not await renew_pipeline_lease(conn, claim, now + timedelta(minutes=71))
    finally:
        await transaction.rollback()
        await conn.close()


async def test_shutdown_releases_pre_model_owner_but_never_a_consumed_model_slot():
    from backend.macro.daily_repository import claim_daily_run

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        old = await claim_pipeline(conn, now)
        assert await release_pipeline_before_model(
            conn, old, now + timedelta(minutes=5)
        )
        resumed = await claim_pipeline(conn, now + timedelta(minutes=10))
        assert resumed["lease_token"] != old["lease_token"]
        # Deliberately leave research_run_id NULL to exercise the independent
        # immutable model-slot guard rather than relying only on the cached FK.
        await claim_daily_run(
            conn,
            now + timedelta(minutes=11),
            {},
            input_hash="a" * 64,
            prompt_version="fixture",
        )
        assert not await release_pipeline_before_model(
            conn, resumed, now + timedelta(minutes=12)
        )
        assert await claim_pipeline(conn, now + timedelta(minutes=41)) is None
        assert (await read_pipeline(conn, now + timedelta(minutes=41)))[
            "state"
        ] == "UNCERTAIN"
    finally:
        await transaction.rollback()
        await conn.close()


async def test_closing_start_window_does_not_interrupt_a_newly_reclaimed_live_owner():
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        old = await claim_pipeline(conn, now)
        assert await advance_pipeline(conn, old, now, stage="WAITING_SOURCES")
        resumed = await claim_pipeline(conn, now + timedelta(minutes=119))
        await finish_source_wait_window(conn, now + timedelta(minutes=120))
        assert await advance_pipeline(
            conn, resumed, now + timedelta(minutes=122), stage="VALIDATING_INPUT"
        )
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.mark.parametrize("fail_persistence", [False, True])
async def test_real_model_slot_and_pipeline_result_preserve_schedule_and_commit_atomically(
    monkeypatch, fail_persistence
):
    from backend.jobs import daily_research as job
    from backend.macro import daily_pipeline_repository as pipeline

    now = clock()
    pool = await create_pool(
        DSN, min_size=1, max_size=2, runtime_role="wealthsail_worker"
    )
    owned = None
    try:
        async with system_connection(pool) as conn:
            owned = dict(await claim_pipeline(conn, now))
        monkeypatch.setattr(job, "now_shanghai", lambda: now)
        monkeypatch.setattr(
            job, "preflight_codex", AsyncMock(return_value={"executable": "fixture"})
        )
        monkeypatch.setattr(
            job,
            "run_codex_research",
            AsyncMock(return_value={"summary": "public", "sources": []}),
        )
        monkeypatch.setattr(job, "verify_research_sources", AsyncMock(return_value={}))
        monkeypatch.setattr(job, "build_prompt", lambda *_: "fixture public prompt")
        monkeypatch.setattr(job, "validate_report", lambda raw, *_, **__: raw)
        monkeypatch.setattr(job, "write_research_audit", lambda *_, **__: None)
        monkeypatch.setattr(pipeline, "source_quality", lambda *_: {"allowed": True})

        async def trace(settings, *, operation, **kwargs):
            return await operation()

        monkeypatch.setattr(job, "trace_inference", trace)
        if fail_persistence:
            monkeypatch.setattr(
                job, "finish_daily_run", AsyncMock(side_effect=RuntimeError("fixture"))
            )
        settings = SimpleNamespace(
            daily_research_enabled=True,
            daily_research_codex_path="fixture",
            daily_research_model=None,
            daily_research_timeout_seconds=900,
            daily_research_schedule_time="06:00",
        )
        operation = job._execute_daily_research(
            pool, settings, now, packet={"asOf": now.isoformat()}, pipeline=owned
        )
        if fail_persistence:
            with pytest.raises(RuntimeError):
                await operation
        else:
            assert (await operation)["status"] == "READY"
        async with system_connection(pool) as conn:
            row = await conn.fetchrow(
                "SELECT p.state,p.research_run_id,r.scheduled_for,t.status FROM research.daily_research_pipelines p JOIN research.daily_research_runs r ON r.run_id=p.research_run_id LEFT JOIN research.daily_research_results t ON t.run_id=r.run_id WHERE p.id=$1",
                owned["id"],
            )
        assert row["scheduled_for"] == now
        assert row["state"] == ("RUNNING" if fail_persistence else "READY")
        assert row["status"] == (None if fail_persistence else "READY")
        job.run_codex_research.assert_awaited_once()
    finally:
        await pool.close()
        admin = await asyncpg.connect(DSN)
        try:
            if owned:
                await admin.execute(
                    "DELETE FROM research.daily_research_pipelines WHERE id=$1",
                    owned["id"],
                )
                await admin.execute(
                    "DELETE FROM research.daily_research_results WHERE run_id IN (SELECT run_id FROM research.daily_research_runs WHERE slot_date=$1 AND run_mode='SCHEDULED')",
                    now.date(),
                )
                await admin.execute(
                    "DELETE FROM research.daily_research_runs WHERE slot_date=$1 AND run_mode='SCHEDULED'",
                    now.date(),
                )
        finally:
            await admin.close()
