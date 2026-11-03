"""Owned committed rows prove archive faults cannot roll back strict publication."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import asyncpg
import pytest

from backend.jobs import research_archive as archiver
from backend.macro import daily_repository
from backend.macro import research_acceptance_repository as acceptance
from backend.macro.research_acceptance import ValidationOutcome
from tests.unit.test_daily_research_job import isolate, settings
from tests.unit.test_manual_research_database import NOW, fixture, make_run
from tests.unit.test_research_acceptance_database import DSN

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned acceptance DB required"),
]


def boundary(monkeypatch, run_id, batch_id):
    actual_enqueue = acceptance.enqueue_research_archive
    mod, _state = isolate(monkeypatch)

    @asynccontextmanager
    async def connection(_pool):
        conn = await asyncpg.connect(DSN)
        try:
            yield conn
        finally:
            await conn.close()

    outcome = ValidationOutcome(
        str(run_id),
        NOW,
        "COMPLETE",
        (),
        {},
        {},
        formal_payload={"summary": "formal", "sources": []},
    )
    from backend.macro import alpha_repository

    monkeypatch.setattr(mod, "system_connection", connection)
    monkeypatch.setattr(archiver, "system_connection", connection)
    monkeypatch.setattr(mod, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(
        mod, "claim_daily_run", AsyncMock(return_value={"run_id": run_id})
    )
    monkeypatch.setattr(mod, "finish_daily_run", daily_repository.finish_daily_run)
    monkeypatch.setattr(acceptance, "enqueue_research_archive", actual_enqueue)
    monkeypatch.setattr(mod, "validate_report_outcome", lambda *_, **__: outcome)
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value={"batchId": str(batch_id)}),
    )
    return mod, actual_enqueue


def owned_database():
    target = urlsplit(DSN)
    assert target.hostname in {"localhost", "127.0.0.1", "::1"}
    assert target.path.startswith("/wealthsail_manual_research_acceptance") or (
        target.port == 29034 and target.path == "/wealthsail_acceptance_review100_v1"
    )


async def execute(mod, request_id, batch_id):
    return await mod._execute_daily_research(
        object(),
        settings(daily_research_manual_enabled=True),
        NOW,
        manual_request_id=request_id,
        source_batch_id=batch_id,
        packet={"asOf": NOW.isoformat()},
    )


@pytest.mark.parametrize("fail_stage", [None, "artifact", "outcome"])
async def test_strict_ready_survives_archive_fault_and_recovers_without_inference(
    monkeypatch, fail_stage
):
    owned_database()
    async with fixture(committed=True) as (conn, tenant, actor, _other):
        request_id, batch_id, run_id = await make_run(conn, tenant, actor)
        mod, _enqueue = boundary(monkeypatch, run_id, batch_id)
        target = (
            "_save_frozen_artifacts"
            if fail_stage == "artifact"
            else "save_validation_outcome"
        )
        original = getattr(acceptance, target)
        if fail_stage:
            monkeypatch.setattr(
                acceptance,
                target,
                AsyncMock(side_effect=ConnectionError("PRIVATE archive URI")),
            )
        result = await execute(mod, request_id, batch_id)
        assert result["status"] == "READY"
        assert result["auditStatus"] == "AUDIT_PENDING"
        mod.archive_research_run.assert_not_awaited()
        # The guard is released before a recovery service ever claims the job.
        mod.release_model_guard.assert_awaited_once()
        row = await conn.fetchrow(
            "SELECT status,payload FROM research.daily_research_results WHERE run_id=$1",
            run_id,
        )
        assert row["status"] == "READY" and row["payload"] is not None
        assert (
            await conn.fetchval(
                "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                run_id,
            )
            == "AUDIT_PENDING"
        )
        for table in ("research_validation_artifacts", "research_validation_records"):
            assert (
                await conn.fetchval(
                    f"SELECT count(*) FROM research.{table} WHERE run_id=$1", run_id
                )
                == 0
            )
        recovered = await archiver.archive_research_run(object(), run_id)
        if fail_stage:
            assert recovered["auditStatus"] == "AUDIT_PENDING"
            monkeypatch.setattr(acceptance, target, original)
            # Advance only this owned test job's retry eligibility, never its
            # lease clock or the frozen evidence/validation timestamps.
            await conn.execute(
                "UPDATE research.research_validation_archive_queue SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE run_id=$1 AND status='AUDIT_PENDING'",
                run_id,
            )
            recovered = await archiver.archive_research_run(object(), run_id)
        assert recovered["auditStatus"] == "ARCHIVED"
        # Repeating recovery is inert and cannot duplicate an INITIAL receipt.
        await archiver.archive_research_run(object(), run_id)
        for table in ("research_validation_artifacts", "research_validation_records"):
            assert (
                await conn.fetchval(
                    f"SELECT count(*) FROM research.{table} WHERE run_id=$1", run_id
                )
                == 1
            )
        assert (
            await conn.fetchval(
                "SELECT validated_at FROM research.research_validation_artifacts WHERE run_id=$1",
                run_id,
            )
            == NOW
        )
        assert (
            await conn.fetchval(
                "SELECT recorded_at FROM research.research_validation_records WHERE run_id=$1",
                run_id,
            )
            == NOW
        )
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_results WHERE run_id=$1",
                run_id,
            )
            == "READY"
        )
        mod.run_codex_research.assert_awaited_once()
        mod.verify_research_sources.assert_awaited_once()
        mod.release_model_guard.assert_awaited_once()


@pytest.mark.parametrize("failure", ["result", "outbox"])
async def test_core_publication_and_outbox_failure_roll_back_together(
    monkeypatch, failure
):
    owned_database()
    async with fixture(committed=True) as (conn, tenant, actor, _other):
        request_id, batch_id, run_id = await make_run(conn, tenant, actor)
        mod, original_enqueue = boundary(monkeypatch, run_id, batch_id)
        original = (
            daily_repository.finish_daily_run
            if failure == "result"
            else original_enqueue
        )

        async def fail_after_write(*args, **kwargs):
            await original(*args, **kwargs)
            raise ConnectionError("PRIVATE core transaction failure")

        if failure == "result":
            monkeypatch.setattr(mod, "finish_daily_run", fail_after_write)
        else:
            monkeypatch.setattr(
                acceptance, "enqueue_research_archive", fail_after_write
            )
        archive = AsyncMock()
        monkeypatch.setattr(mod, "archive_research_run", archive)
        with pytest.raises(ConnectionError):
            await execute(mod, request_id, batch_id)
        for table in (
            "daily_research_results",
            "research_validation_archive_queue",
            "research_validation_artifacts",
            "research_validation_records",
        ):
            assert (
                await conn.fetchval(
                    f"SELECT count(*) FROM research.{table} WHERE run_id=$1", run_id
                )
                == 0
            )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.daily_research_runs WHERE run_id=$1",
                run_id,
            )
            == 1
        )
        archive.assert_not_awaited()
        mod.run_codex_research.assert_awaited_once()
        mod.release_model_guard.assert_awaited_once()
