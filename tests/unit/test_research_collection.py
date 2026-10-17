"""Collection diagnostics retain last known source outcomes and real due times."""

import json
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.common.database import create_pool
from backend.macro.market_repository import read_collection, series_context

NOW = datetime(2026, 10, 15, 12, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
PUBLIC = {"UST_NOMINAL", "UST_REAL", "PBOC_M1", "PBOC_TSF", "PBOC_LOANS"}


def connection(*, result=None, phase="CANCELLED", source_rows=()):
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "result": result or {"status": "UNAVAILABLE", "sourceResults": {}},
        "recorded_at": NOW - timedelta(minutes=31),
        "phase": phase,
    }
    conn.fetch.return_value = list(source_rows)
    return conn


@pytest.mark.asyncio
async def test_empty_cancelled_attempt_retains_last_failure_and_does_not_revive_current_risk():
    conn = connection(
        source_rows=[{"source_key": "UST_REAL", "result": {"status": "FAILED"}}]
    )
    collection = await read_collection(conn, NOW)
    assert collection["sourceResults"]["UST_REAL"]["status"] == "FAILED"
    points = [
        {
            "code": "US_T10_REAL",
            "reference_date": (NOW - timedelta(days=6 - i)).date(),
            "frequency": "DAILY",
            "value": 1 + i / 10,
            "unit": "%",
            "retrieved_at": NOW - timedelta(hours=1),
            "available_at": NOW - timedelta(hours=1),
            "metadata": {"definitionKey": "UST_REAL", "definitionVersion": "v1"},
        }
        for i in range(6)
    ]
    series = series_context(points, NOW, source_results=collection["sourceResults"])
    assert next(i for i in series if i["code"] == "US_T10_REAL")["state"] == "STALE"


@pytest.mark.asyncio
async def test_later_individual_success_replaces_prior_source_failure():
    conn = connection(
        source_rows=[
            {"source_key": "UST_REAL", "result": json.dumps({"status": "SUCCESS"})}
        ]
    )
    collection = await read_collection(conn, NOW)
    assert collection["sourceResults"] == {"UST_REAL": {"status": "SUCCESS"}}


@pytest.mark.asyncio
async def test_old_failure_keeps_its_own_attempt_time_after_a_new_empty_cancel():
    failed_at = NOW - timedelta(days=2)
    conn = connection(
        source_rows=[
            {
                "source_key": "UST_REAL",
                "result": {"status": "FAILED"},
                "recorded_at": failed_at,
            }
        ]
    )
    collection = await read_collection(conn, NOW)
    assert collection["collectedAt"] == NOW - timedelta(minutes=31)
    assert collection["sourceAttemptAt"] == {"UST_REAL": failed_at}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deadline,now,expected",
    [
        (
            datetime(2026, 10, 15, 12, tzinfo=NOW.tzinfo),
            NOW,
            datetime(2026, 10, 15, 12, tzinfo=NOW.tzinfo),
        ),
        (
            datetime(2026, 10, 15, 12, tzinfo=NOW.tzinfo),
            NOW.replace(hour=11, minute=40),
            datetime(2026, 10, 15, 12, tzinfo=NOW.tzinfo),
        ),
        (
            datetime(2026, 10, 15, 15, tzinfo=NOW.tzinfo),
            NOW.replace(hour=11, minute=40),
            datetime(2026, 10, 15, 12, 30, tzinfo=NOW.tzinfo),
        ),
        (
            datetime(2026, 10, 15, 15, tzinfo=NOW.tzinfo),
            NOW.replace(hour=13),
            datetime(2026, 10, 15, 12, 30, tzinfo=NOW.tzinfo),
        ),
    ],
)
async def test_retry_due_is_not_postponed_past_deadline_or_refreshed_to_request_time(
    deadline, now, expected
):
    conn = connection(
        result={"status": "PARTIAL", "retryUntil": deadline.isoformat()},
        phase="FINISHED",
    )
    result = await read_collection(conn, now)
    assert result["nextCheckAt"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["COMPLETED", "PARTIAL"])
async def test_regular_or_exhausted_retry_keeps_original_twenty_four_hour_due(status):
    conn = connection(
        result={"status": status, "retryExhausted": status == "PARTIAL"},
        phase="FINISHED",
    )
    result = await read_collection(conn, NOW + timedelta(days=2))
    assert result["nextCheckAt"] == NOW - timedelta(minutes=31) + timedelta(days=1)


@pytest.mark.asyncio
async def test_active_lease_remains_the_next_check_even_if_old_retry_window_expired():
    conn = connection(
        source_rows=[{"source_key": "UST_REAL", "result": {"status": "FAILED"}}]
    )
    conn.fetchrow.side_effect = [
        {
            "phase": "CLAIMED",
            "recorded_at": NOW - timedelta(minutes=1),
            "lease_until": NOW + timedelta(minutes=9),
            "result": {},
        },
        {
            "phase": "CANCELLED",
            "recorded_at": NOW - timedelta(hours=1),
            "result": {
                "status": "UNAVAILABLE",
                "retryUntil": (NOW - timedelta(minutes=1)).isoformat(),
            },
        },
    ]
    result = await read_collection(conn, NOW)
    assert result["nextCheckAt"] == NOW + timedelta(minutes=9)
    assert result["sourceResults"]["UST_REAL"]["status"] == "FAILED"


async def insert_attempt(
    conn,
    at,
    outcomes,
    *,
    phase="FINISHED",
    job="OPPORTUNITY_MARKET_EVIDENCE",
    lease=None,
):
    await conn.execute(
        """INSERT INTO market.macro_evidence_attempts(run_id,job_key,phase,started_at,recorded_at,lease_until,trigger,result)
        VALUES($1,$2,$3,$4,$4,$5,'ISOLATED_TEST',$6::jsonb)""",
        uuid4(),
        job,
        phase,
        at,
        lease,
        json.dumps({"status": "UNAVAILABLE", "sourceResults": outcomes}),
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
    reason="isolated validation database required",
)
async def test_real_sql_retains_unbounded_source_history_with_global_failure_recovery_and_asof_fences():
    pool = await create_pool(
        os.environ["INVESTMENT_TEST_DATABASE_URL"], min_size=1, max_size=1
    )
    at = datetime(2040, 1, 15, 12, tzinfo=NOW.tzinfo)
    try:
        async with pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            try:
                await conn.execute("SET LOCAL ROLE wealthsail_worker")
                await insert_attempt(
                    conn,
                    at - timedelta(hours=100),
                    {
                        "UST_REAL": {"status": "FAILED"},
                        "UST_NOMINAL": {"status": "SUCCESS"},
                        "CHINABOND": {"status": "PERMISSION_REQUIRED"},
                        "CFETS": {"status": "PERMISSION_REQUIRED"},
                        "PRE_MARKET": {"status": "SUCCESS"},
                    },
                )
                for i in range(40):
                    await insert_attempt(
                        conn, at - timedelta(hours=90 - i), {}, phase="CANCELLED"
                    )
                for role in ("wealthsail_api", "wealthsail_worker"):
                    await conn.execute("SET LOCAL ROLE " + role)
                    result = await read_collection(conn, at - timedelta(hours=1))
                    assert result["sourceResults"]["UST_REAL"]["status"] == "FAILED"
                    assert result["sourceAttemptAt"]["UST_REAL"] == at - timedelta(
                        hours=100
                    )
                    assert (
                        result["collectedAt"] != result["sourceAttemptAt"]["UST_REAL"]
                    )
                await insert_attempt(
                    conn,
                    at,
                    {
                        "PUBLIC_SOURCES": {
                            "status": "FAILED",
                            "errorClass": "TimeoutError",
                        }
                    },
                )
                await insert_attempt(
                    conn,
                    at + timedelta(minutes=1),
                    {"UST_NOMINAL": {"status": "SUCCESS"}},
                )
                await insert_attempt(
                    conn, at + timedelta(minutes=3), {"PBOC_M1": {"status": "SUCCESS"}}
                )
                await insert_attempt(
                    conn,
                    at + timedelta(minutes=1, seconds=30),
                    {"PBOC_M1": {"status": "SUCCESS"}},
                    phase="CLAIMED",
                    lease=at + timedelta(minutes=10),
                )
                await insert_attempt(
                    conn,
                    at + timedelta(minutes=1),
                    {"PBOC_TSF": {"status": "SUCCESS"}},
                    job="ANOTHER_JOB",
                )
                await conn.execute("SET LOCAL ROLE wealthsail_api")
                result = await read_collection(conn, at + timedelta(minutes=2))
                outcomes = result["sourceResults"]
                assert outcomes["UST_NOMINAL"]["status"] == "SUCCESS"
                assert all(
                    outcomes[key]["status"] == "FAILED"
                    for key in PUBLIC - {"UST_NOMINAL"}
                )
                assert outcomes["PRE_MARKET"]["status"] == "SUCCESS"
                assert (
                    outcomes["CHINABOND"]["status"]
                    == outcomes["CFETS"]["status"]
                    == "PERMISSION_REQUIRED"
                )
                assert "PUBLIC_SOURCES" not in outcomes
                assert result["sourceAttemptAt"]["UST_NOMINAL"] == at + timedelta(
                    minutes=1
                )
                assert result["sourceAttemptAt"]["PBOC_M1"] == at
                assert result["sourceAttemptAt"]["PRE_MARKET"] == at - timedelta(
                    hours=100
                )
                assert result["nextCheckAt"] == at + timedelta(minutes=10)
                later = await read_collection(conn, at + timedelta(minutes=4))
                assert later["sourceResults"]["PBOC_M1"]["status"] == "SUCCESS"
                assert later["sourceAttemptAt"]["PBOC_M1"] == at + timedelta(minutes=3)
            finally:
                await tx.rollback()
    finally:
        await pool.close()
