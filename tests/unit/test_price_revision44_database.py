"""Prior-day comparison SQL and role permissions in the owned review44 DB."""

from datetime import datetime, timedelta
import os
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.macro.daily_repository import read_previous_daily_research

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned review44 DB required"),
]


async def test_previous_ready_sql_excludes_today_debug_future_and_failed_with_role_readonly():
    parts = urlsplit(DSN)
    assert (parts.hostname, parts.port, parts.path) == (
        "127.0.0.1",
        55434,
        "/wealthsail_review44_validation_v1",
    )
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        now = datetime(2800, 1, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(
            days=uuid4().int % 30000
        )
        tenant, actor = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'revision44 fixture')",
            tenant,
        )
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'revision44 fixture')",
            actor,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
            tenant,
            actor,
        )

        async def report(
            day, minute, *, mode="SCHEDULED", status="READY", completed=None
        ):
            dispatched = day.replace(hour=8, minute=minute)
            request, batch = None, None
            if mode == "DEBUG":
                request, batch = uuid4(), uuid4()
                await conn.execute(
                    """INSERT INTO research.daily_research_refresh_requests(id,tenant_id,user_id,command_key,requested_at)
                    VALUES($1,$2,$3,$1,$4)""",
                    request,
                    tenant,
                    actor,
                    dispatched,
                )
                await conn.execute(
                    """INSERT INTO market.alpha_public_batches(id,run_date,claimed_at,status,run_mode,debug_request_id)
                    VALUES($1,$2,$3,'CLAIMED','DEBUG',$4)""",
                    batch,
                    dispatched.date(),
                    dispatched,
                    request,
                )
            run_id = uuid4()
            await conn.execute(
                """INSERT INTO research.daily_research_runs(run_id,slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet,run_mode,debug_request_id,source_batch_id)
                VALUES($1,$2,$3,$3,'fixture','revision44','{}',$4,$5,$6)""",
                run_id,
                dispatched.date(),
                dispatched,
                mode,
                request,
                batch,
            )
            if status == "READY":
                await conn.execute(
                    """INSERT INTO research.daily_research_results(run_id,status,recorded_at,payload)
                    VALUES($1,'READY',$2,jsonb_build_object('summary',$3::text))""",
                    run_id,
                    completed or dispatched,
                    str(run_id),
                )
            else:
                await conn.execute(
                    """INSERT INTO research.daily_research_results(run_id,status,recorded_at,error_class)
                    VALUES($1,'FAILED',$2,'FixtureFailure')""",
                    run_id,
                    dispatched,
                )
            return run_id

        await report(now - timedelta(days=3), 30)
        expected = await report(now - timedelta(days=2), 40, mode="DEBUG")
        await report(now - timedelta(days=1), 30, status="FAILED")
        await report(
            now - timedelta(days=1),
            45,
            mode="DEBUG",
            completed=now + timedelta(minutes=1),
        )
        await report(now, 30)
        await report(now, 50, mode="DEBUG")
        for role in ("wealthsail_api", "wealthsail_worker"):
            await conn.execute("SET LOCAL ROLE " + role)
            result = await read_previous_daily_research(conn, now)
            assert result["reportRunId"] == str(expected)
            assert result["reportSlotDate"] == (now - timedelta(days=2)).date()
            assert result["report"]["summary"] == str(expected)
            assert not await conn.fetchval(
                "SELECT has_table_privilege($1,'research.daily_research_results','UPDATE')",
                role,
            )
            await conn.execute("RESET ROLE")
    finally:
        await transaction.rollback()
        await conn.close()
