"""Real PostgreSQL lease fencing and worker/API permission boundaries."""

import asyncio
from datetime import datetime, timedelta
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
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


async def test_first_claim_freezes_cutoffs_and_recovery_ignores_new_configuration():
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        due = clock()
        started = due + timedelta(minutes=15)
        original = await claim_pipeline(
            conn,
            started,
            schedule_time="07:00",
            start_window_minutes=120,
            pipeline_timeout_seconds=7200,
        )
        assert original["start_window_ends_at"] == due + timedelta(hours=2)
        assert original["runtime_deadline_at"] == started + timedelta(hours=2)
        recovered = await claim_pipeline(
            conn,
            due + timedelta(minutes=95),
            schedule_time="08:00",
            start_window_minutes=30,
            pipeline_timeout_seconds=60,
        )
        assert recovered is not None
        for field in (
            "scheduled_for",
            "started_at",
            "start_window_ends_at",
            "runtime_deadline_at",
        ):
            assert recovered[field] == original[field]
        assert recovered["lease_token"] != original["lease_token"]
    finally:
        await transaction.rollback()
        await conn.close()


async def test_enlarging_configuration_cannot_extend_an_existing_start_window():
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        await claim_pipeline(
            conn,
            now,
            start_window_minutes=30,
            pipeline_timeout_seconds=7200,
        )
        assert (
            await claim_pipeline(
                conn,
                now + timedelta(minutes=31),
                start_window_minutes=360,
                pipeline_timeout_seconds=14400,
            )
            is None
        )
    finally:
        await transaction.rollback()
        await conn.close()


async def test_runtime_expiry_can_be_owned_for_terminal_archival_without_extending_it():
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        original = await claim_pipeline(conn, now, pipeline_timeout_seconds=1800)
        recovered = await claim_pipeline(
            conn,
            now + timedelta(minutes=31),
            pipeline_timeout_seconds=14400,
        )
        assert recovered is not None
        assert recovered["runtime_deadline_at"] == original["runtime_deadline_at"]
        assert recovered["runtime_deadline_at"] < now + timedelta(minutes=31)
        assert recovered["lease_token"] != original["lease_token"]
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.mark.parametrize("minutes", [-1, 120])
async def test_new_pipeline_is_not_created_outside_its_start_window(minutes):
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        assert await claim_pipeline(conn, now + timedelta(minutes=minutes)) is None
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.mark.parametrize("stage", ["COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"])
async def test_frozen_window_archives_each_expired_pre_model_stage_and_api_projects_it(
    stage,
):
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        owned = await claim_pipeline(conn, now)
        assert await advance_pipeline(conn, owned, now, stage=stage)
        # Window closure is persisted, not inferred from a new worker config.
        await finish_source_wait_window(conn, now + timedelta(minutes=119))
        assert (
            await conn.fetchval(
                "SELECT state FROM research.daily_research_pipelines WHERE id=$1",
                owned["id"],
            )
            == "RUNNING"
        )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        projected = await read_pipeline(conn, now + timedelta(minutes=120))
        assert projected["state"] == "MISSED"
        assert projected["startWindowEndsAt"] == now + timedelta(minutes=120)
        assert projected["runtimeDeadlineAt"] == now + timedelta(seconds=7200)
        assert (
            await conn.fetchval(
                "SELECT state FROM research.daily_research_pipelines WHERE id=$1",
                owned["id"],
            )
            == "RUNNING"
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await finish_source_wait_window(conn, now + timedelta(minutes=120))
        stored = await conn.fetchrow(
            "SELECT state,error_class FROM research.daily_research_pipelines WHERE id=$1",
            owned["id"],
        )
        assert stored["state"] == "MISSED"
        assert stored["error_class"] == "SourceWaitWindowExpired"
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.mark.parametrize("stage", ["COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"])
@pytest.mark.parametrize("guard", ["live_lease", "model_slot"])
async def test_window_closure_never_archives_a_live_owner_or_consumed_model(
    stage, guard
):
    from backend.macro.daily_repository import claim_daily_run

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = clock()
        owned = await claim_pipeline(conn, now)
        if guard == "live_lease":
            assert await advance_pipeline(conn, owned, now, stage="WAITING_SOURCES")
            owned = await claim_pipeline(conn, now + timedelta(minutes=119))
            assert await advance_pipeline(
                conn, owned, now + timedelta(minutes=119), stage=stage
            )
            if stage == "WAITING_SOURCES":
                # A source wait deliberately releases its lease immediately;
                # emulate a still-live owner waiting inside that stage.
                await conn.execute(
                    "UPDATE research.daily_research_pipelines SET lease_until=$2 WHERE id=$1",
                    owned["id"],
                    now + timedelta(minutes=121),
                )
        else:
            assert await advance_pipeline(conn, owned, now, stage=stage)
            await claim_daily_run(
                conn,
                now + timedelta(minutes=5),
                {},
                input_hash="a" * 64,
                prompt_version="fixture",
            )
            # No cached FK: the independent scheduled model slot is decisive.
            assert owned["research_run_id"] is None
        await finish_source_wait_window(conn, now + timedelta(minutes=120))
        assert (
            await conn.fetchval(
                "SELECT state FROM research.daily_research_pipelines WHERE id=$1",
                owned["id"],
            )
            == "RUNNING"
        )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        status = await read_pipeline(conn, now + timedelta(minutes=120))
        assert status["state"] == ("RUNNING" if guard == "live_lease" else "UNCERTAIN")
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
                run_id = await admin.fetchval(
                    "SELECT research_run_id FROM research.daily_research_pipelines WHERE id=$1",
                    owned["id"],
                )
                await admin.execute(
                    "DELETE FROM research.daily_research_pipelines WHERE id=$1",
                    owned["id"],
                )
                if run_id is not None:
                    target = urlsplit(DSN)
                    if target.path.startswith(
                        "/wealthsail_manual_research_acceptance"
                    ) or (target.port == 29034 and target.path in {"/wealthsail_acceptance_review100_v1", "/wealthsail_acceptance_repair101_v1", "/wealthsail_acceptance_repair101_v2"}):
                        assert target.hostname in {"localhost", "127.0.0.1", "::1"}
                        for table in (
                            "research_validation_replay_attempts",
                            "research_validation_archive_queue",
                            "research_validation_records",
                            "research_validation_artifacts",
                        ):
                            if await admin.fetchval("SELECT to_regclass($1) IS NOT NULL", f"research.{table}"):
                                await admin.execute(
                                    f"DELETE FROM research.{table} WHERE run_id=$1",
                                    run_id,
                                )
                    for table in (
                        "daily_research_model_guards",
                        "daily_research_results",
                        "daily_research_runs",
                    ):
                        await admin.execute(
                            f"DELETE FROM research.{table} WHERE run_id=$1",
                            run_id,
                        )
        finally:
            await admin.close()


@pytest.mark.parametrize("stage", ["COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"])
@pytest.mark.parametrize("guard", [None, "live_lease", "model_slot", "model_fk"])
async def test_previous_day_archival_only_closes_expired_pre_model_work(stage, guard):
    from backend.macro.alpha_repository import claim_alpha_batch
    from backend.macro.daily_pipeline_repository import archive_prior_pipelines
    from backend.macro.daily_repository import claim_daily_run

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        started = clock().replace(hour=23, minute=59)
        now = started + timedelta(minutes=31)
        batch = await claim_alpha_batch(conn, started)
        owned = await claim_pipeline(conn, started, schedule_time="23:59")
        assert await advance_pipeline(
            conn, owned, started, stage=stage, source_batch_id=batch["id"]
        )
        run = None
        if guard == "live_lease":
            await conn.execute(
                "UPDATE research.daily_research_pipelines SET lease_until=$2 WHERE id=$1",
                owned["id"],
                now + timedelta(minutes=1),
            )
        elif guard in {"model_slot", "model_fk"}:
            # An independent same-day slot protects even a NULL cached FK.
            # A FK alone protects as well; its row is deliberately on another
            # date so the two cost-barrier predicates are tested independently.
            run = await claim_daily_run(
                conn,
                started - timedelta(days=1) if guard == "model_fk" else started,
                {},
                input_hash="a" * 64,
                prompt_version="fixture",
            )
            if guard == "model_fk":
                await conn.execute(
                    "UPDATE research.daily_research_pipelines SET research_run_id=$2 WHERE id=$1",
                    owned["id"],
                    run["run_id"],
                )
        before = await conn.fetchrow(
            "SELECT * FROM research.daily_research_pipelines WHERE id=$1", owned["id"]
        )
        assert await archive_prior_pipelines(conn, now) is (guard == "live_lease")
        stored = await conn.fetchrow(
            "SELECT * FROM research.daily_research_pipelines WHERE id=$1", owned["id"]
        )
        expected = (
            "RUNNING" if guard == "live_lease" else "UNCERTAIN" if guard else "MISSED"
        )
        assert stored["state"] == expected
        assert stored["error_class"] == (
            before["error_class"]
            if guard == "live_lease"
            else "PipelineLeaseExpired"
            if guard
            else "PipelineDayExpired"
        )
        for field in (
            "lease_token",
            "lease_until",
            "source_batch_id",
            "research_run_id",
            "scheduled_for",
            "started_at",
            "start_window_ends_at",
            "runtime_deadline_at",
        ):
            assert stored[field] == before[field]
        assert dict(
            await conn.fetchrow(
                "SELECT * FROM market.alpha_public_batches WHERE id=$1", batch["id"]
            )
        ) == dict(batch)
        if run:
            assert dict(
                await conn.fetchrow(
                    "SELECT * FROM research.daily_research_runs WHERE run_id=$1",
                    run["run_id"],
                )
            ) == dict(run)
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM research.daily_research_results WHERE run_id=$1",
                    run["run_id"],
                )
                == 0
            )
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.mark.parametrize("stage", ["MODEL_RUNNING", "VERIFYING_OUTPUT", "PERSISTING"])
async def test_previous_day_post_model_stage_is_never_archived_as_missed(stage):
    from backend.macro.daily_pipeline_repository import archive_prior_pipelines

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        started = clock().replace(hour=23, minute=59)
        owned = await claim_pipeline(conn, started, schedule_time="23:59")
        assert await advance_pipeline(conn, owned, started, stage=stage)
        await archive_prior_pipelines(conn, started + timedelta(minutes=30))
        stored = await conn.fetchrow(
            "SELECT state,error_class FROM research.daily_research_pipelines WHERE id=$1",
            owned["id"],
        )
        assert dict(stored) == {
            "state": "UNCERTAIN",
            "error_class": "PipelineLeaseExpired",
        }
    finally:
        await transaction.rollback()
        await conn.close()


async def test_api_finds_previous_day_owner_and_uses_its_own_model_barrier():
    from backend.macro.daily_repository import claim_daily_run

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        started = clock().replace(hour=23, minute=59)
        owned = await claim_pipeline(conn, started, schedule_time="23:59")
        assert await advance_pipeline(conn, owned, started, stage="WAITING_SOURCES")
        await claim_daily_run(
            conn, started, {}, input_hash="a" * 64, prompt_version="fixture"
        )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        result = await read_pipeline(conn, started + timedelta(minutes=11))
        assert result is not None
        assert result["id"] == str(owned["id"])
        assert result["state"] == "UNCERTAIN"
        assert result["errorClass"] == "PipelineLeaseExpired"
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.mark.parametrize("today", ["pipeline", "model_slot"])
async def test_previous_day_pipeline_does_not_hide_todays_pipeline_or_consumed_slot(
    today,
):
    from backend.macro.daily_repository import claim_daily_run

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        started = clock().replace(hour=23, minute=59)
        await claim_pipeline(conn, started, schedule_time="23:59")
        now = started + timedelta(minutes=11)
        if today == "pipeline":
            current = await claim_pipeline(conn, now, schedule_time="00:00")
        else:
            await claim_daily_run(
                conn,
                now,
                {},
                input_hash="a" * 64,
                prompt_version="fixture",
                schedule_time="00:00",
            )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        result = await read_pipeline(conn, now)
        if today == "pipeline":
            assert result["id"] == str(current["id"])
        else:
            assert result is None
    finally:
        await transaction.rollback()
        await conn.close()


async def test_previous_day_archival_leaves_current_day_and_terminal_history_unchanged():
    from backend.macro.daily_pipeline_repository import archive_prior_pipelines

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        started = clock().replace(hour=23, minute=59)
        old = await claim_pipeline(conn, started, schedule_time="23:59")
        assert await advance_pipeline(
            conn, old, started, stage="COLLECTING", state="FAILED"
        )
        now = started + timedelta(minutes=31)
        current = await claim_pipeline(conn, now, schedule_time="00:00")
        # An expired current-day lease belongs to current-day recovery logic.
        await conn.execute(
            "UPDATE research.daily_research_pipelines SET lease_until=$2 WHERE id=$1",
            current["id"],
            now,
        )
        before = await conn.fetch(
            "SELECT * FROM research.daily_research_pipelines WHERE id=ANY($1::uuid[]) ORDER BY id",
            [old["id"], current["id"]],
        )
        assert await archive_prior_pipelines(conn, now) is False
        after = await conn.fetch(
            "SELECT * FROM research.daily_research_pipelines WHERE id=ANY($1::uuid[]) ORDER BY id",
            [old["id"], current["id"]],
        )
        assert after == before
    finally:
        await transaction.rollback()
        await conn.close()
