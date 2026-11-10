"""Real PostgreSQL atomic recovery, exact material binding and idempotence."""

from datetime import datetime, timedelta, timezone
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

DSN = os.environ.get("RESEARCH_KEEP_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not DSN, reason="isolated recovery DB required")]


async def connect():
    target = urlsplit(DSN)
    assert target.hostname in {"127.0.0.1", "localhost", "::1"} and target.port is not None
    assert target.username == "migration_test_admin" and target.path.startswith("/research_keep_")
    return await asyncpg.connect(DSN)


async def seed(conn):
    run = uuid4()
    start = datetime(2026, 11, 9, 23, 1, 8, tzinfo=timezone.utc)
    end = start + timedelta(minutes=6, seconds=38)
    await conn.execute(
        """INSERT INTO research.daily_research_runs
        (run_id,slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet)
        VALUES($1,'2026-11-10',$2,$2,'unchanged-cost-barrier','fixture',$3::jsonb)""",
        run, start, json.dumps({"asOf": start.isoformat()}),
    )
    await conn.execute(
        """INSERT INTO research.daily_research_results
        (run_id,status,recorded_at,error_class,failure_stage,failure_reason_code)
        VALUES($1,'FAILED',$2,'ResearchValidationError','REPORT_VALIDATION','DIRECTION_VALIDATION_FAILED')""",
        run, end,
    )
    await conn.execute(
        """INSERT INTO research.daily_research_pipelines
        (slot_date,scheduled_for,start_window_ends_at,runtime_deadline_at,started_at,updated_at,finished_at,state,stage,lease_token,lease_until,research_run_id,error_class)
        VALUES('2026-11-10',$2::timestamptz,$2::timestamptz+interval '2 hours',$2::timestamptz+interval '2 hours',$2,$3,$3,'FAILED','VERIFYING_OUTPUT',$4,$3,$1,'ResearchValidationError')""",
        run, start, end, uuid4(),
    )
    digest = await conn.fetchval("SELECT md5(public_packet::text) FROM research.daily_research_runs WHERE run_id=$1", run)
    payload = {"executionStatus": "COMPLETED", "asOf": end.isoformat(),
               "valueDirections": [{"title": "AI硬件", "reviewIndustryIds": ["SEMICONDUCTOR_AI", "MEMORY"], "plans": [{"fundCode": "012345"}]}],
               "validationWarnings": [], "researchRecovery": {"sourceRunId": str(run), "rawSha256": "a" * 64, "modelCalls": 0}}
    return run, end, digest, json.dumps(payload)


async def test_publication_is_atomic_idempotent_and_preserves_original_failure_and_cost_slot():
    conn = await connect()
    tx = conn.transaction()
    await tx.start()
    try:
        run, original_at, digest, payload = await seed(conn)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = datetime.now(timezone.utc)
        first = await conn.fetchrow("SELECT * FROM research.publish_sanitized_daily_report($1,$2::jsonb,$3,$4,$5)", run, payload, digest, "a" * 64, now)
        second = await conn.fetchrow("SELECT * FROM research.publish_sanitized_daily_report($1,$2::jsonb,$3,$4,$5)", run, payload, digest, "a" * 64, now)
        assert first["published"] and not first["already_published"]
        assert second["published"] and second["already_published"]
        row = await conn.fetchrow("SELECT status,payload FROM research.daily_research_results WHERE run_id=$1", run)
        assert row["status"] == "READY"
        published = json.loads(row["payload"])
        assert published["asOf"] == original_at.isoformat()
        assert published["valueDirections"][0]["plans"][0]["fundCode"] == "012345"
        assert await conn.fetchval("SELECT state FROM research.daily_research_pipelines WHERE research_run_id=$1", run) == "READY"
        audits = await conn.fetch("SELECT previous_result,previous_pipeline FROM research.daily_research_report_recoveries WHERE run_id=$1", run)
        assert len(audits) == 1
        assert json.loads(audits[0]["previous_result"])["status"] == "FAILED"
        assert json.loads(audits[0]["previous_pipeline"])["error_class"] == "ResearchValidationError"
        assert await conn.fetchval("SELECT input_hash FROM research.daily_research_runs WHERE run_id=$1", run) == "unchanged-cost-barrier"
        assert await conn.fetchval("SELECT count(*) FROM research.daily_research_runs WHERE slot_date='2026-11-10'") == 1
    finally:
        await tx.rollback()
        await conn.close()


async def test_wrong_frozen_packet_and_browser_role_cannot_publish():
    conn = await connect()
    tx = conn.transaction()
    await tx.start()
    try:
        run, _, digest, payload = await seed(conn)
        now = datetime.now(timezone.utc)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        with pytest.raises(asyncpg.InvalidParameterValueError, match="RECOVERY_PACKET_MISMATCH"):
            async with conn.transaction():
                await conn.fetch("SELECT * FROM research.publish_sanitized_daily_report($1,$2::jsonb,$3,$4,$5)", run, payload, "0" * 32, "a" * 64, now)
        assert await conn.fetchval("SELECT status FROM research.daily_research_results WHERE run_id=$1", run) == "FAILED"
        assert await conn.fetchval("SELECT count(*) FROM research.daily_research_report_recoveries WHERE run_id=$1", run) == 0
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetch("SELECT * FROM research.publish_sanitized_daily_report($1,$2::jsonb,$3,$4,$5)", run, payload, digest, "a" * 64, now)
    finally:
        await tx.rollback()
        await conn.close()
