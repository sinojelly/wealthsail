"""Migration 098 keeps industry metadata nullable, safe and append-only."""

import asyncpg
import pytest
import pytest_asyncio
from urllib.parse import urlsplit

from backend.macro.daily_repository import finish_daily_run, read_daily_research
from tests.unit.test_daily_failure_diagnostics_database import (
    DSN,
    claim,
    stamp,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated DB required"),
]


@pytest_asyncio.fixture
async def conn():
    target = urlsplit(DSN)
    assert target.hostname in {"localhost", "127.0.0.1", "::1"}
    assert target.path.startswith("/wealthsail_manual_research")
    connection = await asyncpg.connect(DSN)
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


async def test_098_adds_only_nullable_industry_metadata_and_preserves_runtime_grants(conn):
    column = await conn.fetchrow(
        "SELECT is_nullable,data_type,udt_name FROM information_schema.columns "
        "WHERE table_schema='research' AND table_name='daily_research_results' "
        "AND column_name='failure_industry_codes'"
    )
    assert column is not None, "098 nullable industry metadata is absent"
    assert dict(column) == {
        "is_nullable": "YES",
        "data_type": "ARRAY",
        "udt_name": "_text",
    }
    for role in ("wealthsail_api", "wealthsail_worker", "authenticated"):
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            assert await conn.fetchval(
                "SELECT has_column_privilege($1,'research.daily_research_results','failure_industry_codes',$2)",
                role,
                privilege,
            ) == (
                privilege == "SELECT" and role != "authenticated"
                or privilege == "INSERT" and role == "wealthsail_worker"
            )
        assert not await conn.fetchval(
            "SELECT has_table_privilege($1,'research.daily_research_results','DELETE')",
            role,
        )


async def test_098_old_insert_defaults_new_metadata_to_null(conn):
    now = stamp()
    run = await claim(conn, now)
    await conn.execute(
        "INSERT INTO research.daily_research_results(run_id,status,recorded_at,error_class) "
        "VALUES($1,'FAILED',$2,'ValueError')",
        run["run_id"],
        now,
    )
    assert await conn.fetchval(
        "SELECT failure_industry_codes FROM research.daily_research_results WHERE run_id=$1",
        run["run_id"],
    ) is None


async def test_industry_codes_round_trip_only_for_admin_and_cannot_retry_failed_slot(conn):
    now = stamp()
    run = await claim(conn, now)
    await finish_daily_run(
        conn,
        run["run_id"],
        now,
        status="FAILED",
        error_class="ResearchValidationError",
        failure_stage="REPORT_VALIDATION",
        failure_reason_code="DIRECTION_VALIDATION_FAILED",
        failure_rule_id="DIRECTION_PLAN_SECTOR_UNREVIEWED",
        failure_item_index=0,
        failure_industry_codes=["RARE_EARTH", "NONFERROUS", "PRIVATE_MODEL"],
    )
    expected = ["NONFERROUS", "RARE_EARTH"]
    assert await conn.fetchval(
        "SELECT failure_industry_codes FROM research.daily_research_results WHERE run_id=$1",
        run["run_id"],
    ) == expected
    assert (await read_daily_research(conn, now))["failureIndustryCodes"] is None
    result = await read_daily_research(conn, now, include_failure_industry_codes=True)
    assert result["failureIndustryCodes"] == expected
    assert result["failureRuleId"] == "DIRECTION_PLAN_SECTOR_UNREVIEWED"
    assert await claim(conn, now) is None
    await finish_daily_run(conn, run["run_id"], now, status="FAILED")
    assert await conn.fetchval(
        "SELECT failure_industry_codes FROM research.daily_research_results WHERE run_id=$1",
        run["run_id"],
    ) == expected


@pytest.mark.parametrize(
    "codes",
    [[], [None], ["PRIVATE_MODEL_SECTOR"], ["NONFERROUS"] * 11],
)
async def test_direct_sql_rejects_empty_null_unknown_or_oversized_code_arrays(conn, codes):
    now = stamp()
    run = await claim(conn, now)
    with pytest.raises(asyncpg.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO research.daily_research_results(run_id,status,recorded_at,failure_industry_codes) "
                "VALUES($1,'FAILED',$2,$3::text[])",
                run["run_id"],
                now,
                codes,
            )


async def test_ready_cannot_contain_industry_failure_metadata_even_via_sql(conn):
    now = stamp()
    run = await claim(conn, now)
    with pytest.raises(asyncpg.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO research.daily_research_results(run_id,status,recorded_at,payload,failure_industry_codes) "
                "VALUES($1,'READY',$2,'{}',ARRAY['NONFERROUS'])",
                run["run_id"],
                now,
            )
