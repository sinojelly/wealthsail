"""Real isolated PostgreSQL uniqueness, time barriers and runtime role grants."""

import asyncio
from datetime import datetime, timedelta
import os
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.common.database import create_pool, system_connection
from backend.macro.daily_repository import (
    claim_daily_run,
    finish_daily_run,
    read_daily_research,
    read_recent_public_research,
)

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated DB required"),
]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def stamp():
    return datetime(2090, 1, 1, 9, tzinfo=SHANGHAI) + timedelta(
        days=uuid4().int % 200000
    )


async def claim(conn, now):
    return await claim_daily_run(
        conn,
        now,
        {"asOf": now.isoformat()},
        input_hash="fixture",
        prompt_version="fixture",
        cli_version="fixture",
    )


async def test_concurrent_dispatch_unique_and_failed_slot_cannot_be_reclaimed():
    pool = await create_pool(
        DSN, min_size=1, max_size=2, runtime_role="wealthsail_worker"
    )
    now, owned = stamp(), []

    async def dispatch(stamp):
        async with system_connection(pool) as conn:
            return await claim(conn, stamp)

    try:
        claims = await asyncio.gather(dispatch(now), dispatch(now))
        assert sum(value is not None for value in claims) == 1
        winner = next(row for row in claims if row)
        owned.append(winner["run_id"])
        async with system_connection(pool) as conn:
            await finish_daily_run(
                conn,
                winner["run_id"],
                now,
                status="UNCERTAIN",
                error_class="TimeoutError",
            )
            await finish_daily_run(
                conn,
                winner["run_id"],
                now + timedelta(minutes=1),
                status="READY",
                payload={"summary": "late"},
            )
        assert await dispatch(now + timedelta(hours=10)) is None
        next_day = await dispatch(now + timedelta(days=1))
        assert next_day is not None
        owned.append(next_day["run_id"])
        async with system_connection(pool) as conn:
            assert (
                await conn.fetchval(
                    "SELECT status FROM research.daily_research_results WHERE run_id=$1",
                    winner["run_id"],
                )
                == "UNCERTAIN"
            )
    finally:
        await pool.close()
        admin = await asyncpg.connect(DSN)
        try:
            await admin.execute(
                "DELETE FROM research.daily_research_results WHERE run_id=ANY($1::uuid[])",
                owned,
            )
            await admin.execute(
                "DELETE FROM research.daily_research_runs WHERE run_id=ANY($1::uuid[])",
                owned,
            )
        finally:
            await admin.close()


async def test_api_reads_visible_run_even_when_its_terminal_is_still_future_and_cannot_write():
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        now = stamp()
        row = await claim(conn, now)
        await finish_daily_run(
            conn,
            row["run_id"],
            now + timedelta(minutes=1),
            status="READY",
            payload={"summary": "future"},
        )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        result = await read_daily_research(conn, now)
        assert result["state"] == "RUNNING"
        assert result["report"] is None or result["report"].get("summary") != "future"
        for table in (
            "research.daily_research_runs",
            "research.daily_research_results",
        ):
            assert await conn.fetchval(
                "SELECT has_table_privilege(current_user,$1,'SELECT')", table
            )
            for role in ("wealthsail_api", "wealthsail_worker", "authenticated"):
                for privilege in ("INSERT", "UPDATE", "DELETE"):
                    expected = role == "wealthsail_worker" and privilege == "INSERT"
                    assert (
                        await conn.fetchval(
                            "SELECT has_table_privilege($1,$2,$3)",
                            role,
                            table,
                            privilege,
                        )
                        == expected
                    )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO research.daily_research_results(run_id,status,recorded_at) VALUES($1,'FAILED',$2)",
                    row["run_id"],
                    now,
                )
        await conn.execute("SET LOCAL ROLE authenticated")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetch("SELECT * FROM research.daily_research_runs")
    finally:
        await transaction.rollback()
        await conn.close()


async def test_real_public_packet_uses_api_read_only_connection_without_actor_or_http(
    monkeypatch,
):
    from backend.dependencies import now_shanghai
    from backend.jobs.daily_research import load_public_packet
    import httpx

    def no_http(*args, **kwargs):
        raise AssertionError("packet SQL must not perform HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "request", no_http)
    conn = await asyncpg.connect(DSN)
    try:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL ROLE wealthsail_api")
            packet = await load_public_packet(
                conn,
                now_shanghai(),
                {"documents": [], "energy": [], "sourceResults": {}},
            )
        assert set(packet) == {
            "methodVersion",
            "asOf",
            "macros",
            "series",
            "indices",
            "actionableZones",
            "directions",
            "schedules",
            "documents",
            "energy",
            "sourceResults",
            "previousQuestions",
            "previousResearch",
            "previousUpcoming",
            "candidateIndex",
            "alphaPublic",
            "themes",
            "themeActionableZones",
            "strategyMemoryRules",
            "previousActionablePlans",
            "discoveryUniverse",
            "discoveryActionableZones",
            "previousValueDirections",
            "discoveryCoverage",
            "quantitativeScan",
            "institutionalSignals",
            "dynamicNavSourceResults",
            "fundSubscriptionStatus",
        }
        assert {row["indexId"] for row in packet["indices"]} == {
            "hs300",
            "a500",
            "zz500",
        }
        assert not {"portfolio", "holdings", "tenantId", "userId"}.intersection(packet)
    finally:
        await conn.close()


async def test_ready_report_identity_and_recent_history_follow_actual_selected_success():
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        now = stamp()
        old = await claim(conn, now - timedelta(days=2))
        await finish_daily_run(
            conn,
            old["run_id"],
            now - timedelta(days=2),
            status="READY",
            payload={"summary": "old public report", "events": []},
        )
        failed = await claim(conn, now - timedelta(days=1))
        await finish_daily_run(
            conn,
            failed["run_id"],
            now - timedelta(days=1),
            status="FAILED",
            error_class="TimeoutError",
        )
        current = await claim(conn, now)
        await finish_daily_run(
            conn,
            current["run_id"],
            now + timedelta(minutes=1),
            status="READY",
            payload={"summary": "future public report"},
        )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        result = await read_daily_research(conn, now)
        assert result["state"] == "RUNNING"
        assert result["runId"] == str(current["run_id"])
        assert result["reportRunId"] == str(old["run_id"])
        assert result["reportCompletedAt"] == now - timedelta(days=2)
        assert result["reportSlotDate"] == old["slot_date"]
        rows = await read_recent_public_research(conn, now)
        assert [row["runId"] for row in rows] == [str(old["run_id"])]
    finally:
        await transaction.rollback()
        await conn.close()
