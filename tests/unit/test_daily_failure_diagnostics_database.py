"""Real PostgreSQL diagnostics retain append-only grants and dispatch barriers."""

from datetime import datetime, timedelta
import ipaddress
import os
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest
import pytest_asyncio

from backend.macro.daily_repository import (
    claim_daily_run,
    finish_daily_run,
    read_daily_research,
)


DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated DB required"),
]
COLUMNS = (
    "failure_stage",
    "failure_reason_code",
    "failure_rule_id",
    "failure_item_index",
)


@pytest_asyncio.fixture
async def conn():
    host = urlsplit(DSN).hostname
    assert host == "localhost" or ipaddress.ip_address(host).is_loopback
    connection = await asyncpg.connect(DSN)
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


def stamp():
    return datetime(2300, 1, 1, 9, tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(
        days=uuid4().int % 100000
    )


async def claim(conn, now):
    return await claim_daily_run(
        conn, now, {}, input_hash="fixture", prompt_version="failure-diag-fixture"
    )


async def test_migration_adds_nullable_columns_without_changing_runtime_grants(conn):
    columns = await conn.fetch(
        "SELECT column_name,is_nullable FROM information_schema.columns "
        "WHERE table_schema='research' AND table_name='daily_research_results' AND column_name=ANY($1::text[])",
        list(COLUMNS),
    )
    assert {row["column_name"] for row in columns} == set(COLUMNS)
    assert all(row["is_nullable"] == "YES" for row in columns)
    for role in ("wealthsail_api", "wealthsail_worker", "authenticated"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert await conn.fetchval(
                "SELECT has_table_privilege($1,'research.daily_research_results',$2)",
                role,
                privilege,
            ) == (
                privilege == "SELECT"
                and role != "authenticated"
                or privilege == "INSERT"
                and role == "wealthsail_worker"
            )
    security = await conn.fetchrow(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='research.daily_research_results'::regclass"
    )
    assert dict(security) == {"relrowsecurity": False, "relforcerowsecurity": False}


async def test_old_insert_defaults_diagnostics_to_null(conn):
    now = stamp()
    run = await claim(conn, now)
    await conn.execute(
        "INSERT INTO research.daily_research_results(run_id,status,recorded_at,error_class) VALUES($1,'FAILED',$2,'ValueError')",
        run["run_id"],
        now,
    )
    row = await conn.fetchrow(
        "SELECT failure_stage,failure_reason_code,failure_rule_id,failure_item_index FROM research.daily_research_results WHERE run_id=$1",
        run["run_id"],
    )
    assert dict(row) == dict.fromkeys(COLUMNS)


@pytest.mark.parametrize(
    "column,value",
    [
        ("failure_stage", "private text"),
        ("failure_reason_code", "raw message"),
        ("failure_rule_id", "raw detail"),
        ("failure_item_index", -1),
        ("failure_item_index", 10000),
    ],
)
async def test_database_rejects_unbounded_or_invalid_diagnostic_values(
    conn, column, value
):
    now = stamp()
    run = await claim(conn, now)
    with pytest.raises(asyncpg.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                f"INSERT INTO research.daily_research_results(run_id,status,recorded_at,{column}) VALUES($1,'FAILED',$2,$3)",
                run["run_id"],
                now,
                value,
            )


async def test_ready_cannot_contain_failure_diagnostics_even_via_direct_sql(conn):
    now = stamp()
    run = await claim(conn, now)
    with pytest.raises(asyncpg.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO research.daily_research_results(run_id,status,recorded_at,payload,failure_stage) VALUES($1,'READY',$2,'{}','REPORT_VALIDATION')",
                run["run_id"],
                now,
            )


@pytest.mark.parametrize("index", [0, 9999])
async def test_database_accepts_both_inclusive_item_index_boundaries(conn, index):
    now = stamp()
    run = await claim(conn, now)
    await finish_daily_run(
        conn, run["run_id"], now, status="FAILED", failure_item_index=index
    )
    assert (
        await conn.fetchval(
            "SELECT failure_item_index FROM research.daily_research_results WHERE run_id=$1",
            run["run_id"],
        )
        == index
    )


async def test_worker_single_terminal_write_and_api_projection_keep_failed_slot_consumed(
    conn,
):
    now = stamp()
    await conn.execute("SET LOCAL ROLE wealthsail_worker")
    run = await claim(conn, now)
    await finish_daily_run(
        conn,
        run["run_id"],
        now,
        status="FAILED",
        error_class="ValueError",
        failure_stage="REPORT_VALIDATION",
        failure_reason_code="PAYLOAD_VALIDATION_FAILED",
        failure_rule_id="INDUSTRY_RISK_CHECK_DUPLICATE",
        failure_item_index=4,
    )
    await finish_daily_run(
        conn, run["run_id"], now, status="READY", payload={"summary": "late"}
    )
    assert await claim(conn, now + timedelta(hours=1)) is None
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM research.daily_research_results WHERE run_id=$1",
            run["run_id"],
        )
        == 1
    )
    await conn.execute("SET LOCAL ROLE wealthsail_api")
    actual = await read_daily_research(conn, now)
    assert actual["runId"] == str(run["run_id"])
    assert actual["state"] == "FAILED"
    assert actual["failureStage"] == "REPORT_VALIDATION"
    assert actual["failureReasonCode"] == "PAYLOAD_VALIDATION_FAILED"
    assert actual["failureItemIndex"] == 4
    assert actual["failureRuleId"] == "INDUSTRY_RISK_CHECK_DUPLICATE"
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await conn.execute(
                "UPDATE research.daily_research_results SET failure_item_index=5 WHERE run_id=$1",
                run["run_id"],
            )
