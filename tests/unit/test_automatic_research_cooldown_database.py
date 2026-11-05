"""Owned PostgreSQL dispatch cooling; no models, providers or production access."""

from datetime import datetime, timedelta
import os
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.macro import daily_repository as repository
from backend.macro.model_dispatch_repository import (
    create_model_guard,
    lock_model_dispatch,
    release_model_guard,
)
from tests.unit.test_manual_research_database import fixture, make_run


NOW = datetime(5000, 8, 5, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
FIELDS = {"active", "hours", "lastAiDispatchAt", "automaticEligibleAt"}


def owned_database():
    dsn = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("owned dynamic102 database required")
    target = urlsplit(dsn)
    assert target.hostname in {"localhost", "127.0.0.1", "::1"}
    assert target.port == 29034
    assert target.path == "/wealthsail_dynamic_research102_v1"
    return dsn


def test_pure_cooldown_preserves_exact_boundary_and_safe_dispatch_metadata():
    read = repository.automatic_research_cooldown
    active = read(NOW, NOW + timedelta(hours=23) - timedelta(microseconds=1))
    assert active == {
        "active": True,
        "hours": 23,
        "lastAiDispatchAt": NOW,
        "automaticEligibleAt": NOW + timedelta(hours=23),
    }
    assert read(NOW, NOW + timedelta(hours=23))["active"] is False
    assert read(None, NOW) == {
        "active": False,
        "hours": 23,
        "lastAiDispatchAt": None,
        "automaticEligibleAt": None,
    }


@pytest.mark.parametrize(
    "last,current", [(NOW.replace(tzinfo=None), NOW), (NOW, NOW.replace(tzinfo=None))]
)
def test_pure_cooldown_rejects_clocks_without_timezone(last, current):
    with pytest.raises(ValueError):
        repository.automatic_research_cooldown(last, current)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["MANUAL", "DEBUG", "SCHEDULED"])
@pytest.mark.parametrize("terminal", ["READY", "FAILED", "UNCERTAIN", None])
async def test_every_durable_dispatch_cools_automatic_entry_regardless_of_business_result(
    mode, terminal
):
    owned_database()
    async with fixture() as (conn, tenant, actor, _):
        if mode == "SCHEDULED":
            run = await repository.claim_daily_run(
                conn,
                NOW,
                {"asOf": NOW.isoformat()},
                input_hash="cooldown-owned",
                prompt_version="cooldown-owned",
            )
            run_id = run["run_id"]
        else:
            _, _, run_id = await make_run(conn, tenant, actor, mode=mode, now=NOW)
        if terminal is not None:
            await repository.finish_daily_run(
                conn,
                run_id,
                NOW + timedelta(minutes=1),
                status=terminal,
                payload={"summary": "owned fixture"} if terminal == "READY" else None,
                error_class=None if terminal == "READY" else "FixtureFailure",
            )
        count_before = await conn.fetchval(
            "SELECT count(*) FROM research.daily_research_runs"
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        active = await repository.read_automatic_research_cooldown(
            conn, NOW + timedelta(hours=1)
        )
        assert set(active) == FIELDS
        assert active["active"] and active["hours"] == 23
        assert active["lastAiDispatchAt"] == NOW
        assert active["automaticEligibleAt"] == NOW + timedelta(hours=23)
        assert (
            await repository.read_automatic_research_cooldown(
                conn, NOW + timedelta(hours=23)
            )
        )["active"] is False
        assert (
            await conn.fetchval("SELECT count(*) FROM research.daily_research_runs")
            == count_before
        )
        assert (
            not {"owner_token", "ownerToken", "tenantId", "requestId", "payload"}
            & active.keys()
        )


@pytest.mark.asyncio
async def test_no_dispatch_does_not_cool_or_consume_scheduled_slot_when_telemetry_write_fails(
    monkeypatch,
):
    owned_database()
    from backend.ai_usage import repository as telemetry

    async def unavailable(*_args, **_kwargs):
        raise OSError("PRIVATE telemetry unavailable")

    monkeypatch.setattr(telemetry, "persist", unavailable)
    async with fixture() as (conn, *_):
        before = await conn.fetchval(
            "SELECT count(*) FROM research.daily_research_runs"
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        result = await repository.read_automatic_research_cooldown(conn, NOW)
        assert result["active"] is False
        assert set(result) == FIELDS
        assert (
            await conn.fetchval("SELECT count(*) FROM research.daily_research_runs")
            == before
        )
        assert not await repository.consumed(conn, NOW)


@pytest.mark.asyncio
async def test_manual_commit_between_early_read_and_locked_recheck_blocks_automatic_without_inserting_slot():
    dsn = owned_database()
    async with fixture(committed=True) as (automatic, tenant, actor, _):
        early = await repository.read_automatic_research_cooldown(automatic, NOW)
        assert early["active"] is False
        manual = await asyncpg.connect(dsn)
        try:
            async with manual.transaction():
                await manual.execute("SET LOCAL ROLE wealthsail_worker")
                await lock_model_dispatch(manual)
                _, _, run_id = await make_run(manual, tenant, actor, now=NOW)
                token = await create_model_guard(manual, run_id, NOW)
                assert await release_model_guard(
                    manual, run_id, token, NOW + timedelta(minutes=1)
                )
            async with automatic.transaction():
                await automatic.execute("SET LOCAL ROLE wealthsail_worker")
                await lock_model_dispatch(automatic)
                late = await repository.read_automatic_research_cooldown(
                    automatic, NOW - timedelta(seconds=1)
                )
                assert late["active"] is True
                assert late["lastAiDispatchAt"] == NOW
                assert set(late) == FIELDS
                assert not await repository.consumed(automatic, NOW)
                assert (
                    await automatic.fetchval(
                        "SELECT count(*) FROM research.daily_research_runs WHERE slot_date=$1 AND run_mode='SCHEDULED'",
                        NOW.date(),
                    )
                    == 0
                )
        finally:
            await manual.close()
