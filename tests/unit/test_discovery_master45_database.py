"""Durable Worker master preparation under the exact rollback-only review DB."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.investment import discovery_master as service
from test_discovery_master45 import entries
from test_theme_graph import NOW

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated review45 DB required"),
]


async def connection():
    parsed = urlsplit(DSN)
    assert parsed.hostname in {"127.0.0.1", "localhost"}
    assert parsed.port == 55434 and parsed.path == "/wealthsail_review45_validation_v1"
    return await asyncpg.connect(DSN)


async def full_fixture_source(conn):
    # Other validation fixtures may already have bootstrapped the real public
    # catalog. A complete test response includes it, rather than pretending a
    # disjoint 10k response is complete and weakening the shrink protection.
    existing = await conn.fetch(
        "SELECT fund_code,fund_name,fund_type,pinyin_abbr,pinyin_full FROM reference.fund_master"
    )
    combined = {
        row["fund_code"]: service.FundMasterEntry(
            row["fund_code"],
            row["fund_name"],
            row["fund_type"],
            row["pinyin_abbr"],
            row["pinyin_full"],
        )
        for row in existing
    }
    combined.update({row.fund_code: row for row in entries()})
    return list(combined.values())


async def test_concurrent_preparation_claims_once_and_upserts_through_worker_function(
    monkeypatch,
):
    conn, other = await connection(), await connection()
    tx = conn.transaction()
    await tx.start()
    try:
        job = "DISCOVERY_FUND_MASTER:review45:" + str(uuid4())
        monkeypatch.setattr(service, "_JOB", job)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        complete = await full_fixture_source(conn)

        @asynccontextmanager
        async def acquire(pool):
            yield conn

        monkeypatch.setattr(service, "system_connection", acquire)
        started, release = asyncio.Event(), asyncio.Event()
        fetches = []

        async def fetcher():
            fetches.append(True)
            started.set()
            await release.wait()
            return complete

        first = asyncio.create_task(
            service.ensure_discovery_fund_master(
                object(), NOW, fetcher=fetcher, now_provider=lambda: NOW
            )
        )
        await asyncio.wait_for(started.wait(), 2)
        assert not await other.fetchval(
            "SELECT pg_try_advisory_xact_lock(hashtext($1))", job
        )
        second = await service.ensure_discovery_fund_master(
            object(), NOW, fetcher=fetcher, now_provider=lambda: NOW
        )
        assert second["status"] == "SKIPPED" and second["reason"] == "ACTIVE_CLAIM"
        release.set()
        result = await first
        assert result["status"] == "SUCCESS" and result["totalEntries"] == len(complete)
        assert len(fetches) == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM reference.fund_master WHERE fund_code BETWEEN '900000' AND '909999'"
            )
            == 10000
        )
        attempts = await conn.fetch(
            "SELECT phase,result FROM market.macro_evidence_attempts WHERE job_key=$1 ORDER BY recorded_at,phase",
            job,
        )
        assert {row["phase"] for row in attempts} == {"CLAIMED", "FINISHED"}
        saved = json.loads(
            next(row["result"] for row in attempts if row["phase"] == "FINISHED")
        )
        assert saved["totalEntries"] == len(complete) and saved["status"] == "SUCCESS"
        assert not await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','reference.fund_master','INSERT')"
        )
        assert await conn.fetchval(
            "SELECT has_function_privilege('wealthsail_worker','reference.upsert_fund_master(text,text,text,text,text)','EXECUTE')"
        )
    finally:
        await tx.rollback()
        await conn.close()
        await other.close()


async def test_success_watermark_failure_cooldown_and_api_cannot_start_public_fetch(
    monkeypatch,
):
    conn = await connection()
    tx = conn.transaction()
    await tx.start()
    try:
        job = "DISCOVERY_FUND_MASTER:review45:" + str(uuid4())
        monkeypatch.setattr(service, "_JOB", job)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        complete = await full_fixture_source(conn)

        @asynccontextmanager
        async def acquire(pool):
            yield conn

        monkeypatch.setattr(service, "system_connection", acquire)
        successful_calls = []

        async def fetcher():
            successful_calls.append(True)
            return complete

        first = await service.ensure_discovery_fund_master(
            object(), NOW, fetcher=fetcher, now_provider=lambda: NOW
        )
        assert first["status"] == "SUCCESS"
        fresh = await service.ensure_discovery_fund_master(
            object(),
            NOW + timedelta(days=6),
            fetcher=fetcher,
            now_provider=lambda: NOW + timedelta(days=6),
        )
        assert fresh["reason"] == "FRESH" and len(successful_calls) == 1
        refreshed = await service.ensure_discovery_fund_master(
            object(),
            NOW + timedelta(days=7),
            fetcher=fetcher,
            now_provider=lambda: NOW + timedelta(days=7),
        )
        assert refreshed["status"] == "SUCCESS" and len(successful_calls) == 2

        monkeypatch.setattr(service, "_JOB", job + ":failure")
        failed_calls = []

        async def unavailable():
            failed_calls.append(True)
            raise RuntimeError("public source unavailable")

        before = await conn.fetchval("SELECT count(*) FROM reference.fund_master")
        assert (
            await service.ensure_discovery_fund_master(
                object(), NOW, fetcher=unavailable, now_provider=lambda: NOW
            )
        )["status"] == "FAILED"
        cooled = await service.ensure_discovery_fund_master(
            object(),
            NOW + timedelta(minutes=59),
            fetcher=unavailable,
            now_provider=lambda: NOW + timedelta(minutes=59),
        )
        assert cooled["reason"] == "COOLDOWN" and len(failed_calls) == 1
        await service.ensure_discovery_fund_master(
            object(),
            NOW + timedelta(hours=1),
            fetcher=unavailable,
            now_provider=lambda: NOW + timedelta(hours=1),
        )
        assert len(failed_calls) == 2
        assert (
            await conn.fetchval("SELECT count(*) FROM reference.fund_master") == before
        )

        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE wealthsail_api")
            monkeypatch.setattr(service, "_JOB", job + ":api")
            result = await service.ensure_discovery_fund_master(
                object(), NOW, fetcher=unavailable, now_provider=lambda: NOW
            )
            assert (
                result["status"] == "FAILED"
                and result["errorClass"] == "InsufficientPrivilegeError"
            )
            assert len(failed_calls) == 2
    finally:
        await tx.rollback()
        await conn.close()


async def test_future_completion_does_not_unlock_an_active_master_claim(monkeypatch):
    conn = await connection()
    tx = conn.transaction()
    await tx.start()
    try:
        monkeypatch.setattr(
            service, "_JOB", "DISCOVERY_FUND_MASTER:review45:" + str(uuid4())
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")

        @asynccontextmanager
        async def acquire(pool):
            yield conn

        monkeypatch.setattr(service, "system_connection", acquire)
        claim = await service._claim_master(object(), NOW)
        await service._finish_master(
            object(),
            claim,
            NOW + timedelta(minutes=1),
            {"status": "SUCCESS", "totalEntries": 10000},
        )
        result = await service._claim_master(object(), NOW)
        assert result["status"] == "SKIPPED" and result["reason"] == "ACTIVE_CLAIM"
    finally:
        await tx.rollback()
        await conn.close()


async def test_clock_regression_cannot_hide_a_future_claim_and_start_another_fetch(
    monkeypatch,
):
    conn = await connection()
    tx = conn.transaction()
    await tx.start()
    try:
        monkeypatch.setattr(
            service, "_JOB", "DISCOVERY_FUND_MASTER:review45:" + str(uuid4())
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")

        @asynccontextmanager
        async def acquire(pool):
            yield conn

        monkeypatch.setattr(service, "system_connection", acquire)
        await service._claim_master(object(), NOW)
        result = await service._claim_master(object(), NOW - timedelta(seconds=1))
        assert result["status"] == "SKIPPED" and result["reason"] == "ACTIVE_CLAIM"
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.parametrize("stage", ["FETCH", "AFTER_UPSERT"])
async def test_regressed_clock_keeps_only_real_claim_and_never_writes_success(
    monkeypatch, stage
):
    conn = await connection()
    tx = conn.transaction()
    await tx.start()
    try:
        job = "DISCOVERY_FUND_MASTER:review45:" + str(uuid4())
        monkeypatch.setattr(service, "_JOB", job)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        complete = await full_fixture_source(conn)
        before = await conn.fetchval("SELECT count(*) FROM reference.fund_master")

        @asynccontextmanager
        async def acquire(pool):
            yield conn

        monkeypatch.setattr(service, "system_connection", acquire)

        async def fetcher():
            return complete

        samples = iter(
            [
                NOW - timedelta(seconds=1)
                if stage == "FETCH"
                else NOW + timedelta(seconds=1),
                NOW - timedelta(seconds=1),
            ]
        )
        result = await service.ensure_discovery_fund_master(
            object(), NOW, fetcher=fetcher, now_provider=lambda: next(samples)
        )
        assert (
            result["status"] == "FAILED" and result["errorClass"] == "ClockRegression"
        )
        assert "completedAt" not in result
        attempts = await conn.fetch(
            "SELECT phase FROM market.macro_evidence_attempts WHERE job_key=$1", job
        )
        assert [row["phase"] for row in attempts] == ["CLAIMED"]
        after = await conn.fetchval("SELECT count(*) FROM reference.fund_master")
        assert after == (before if stage == "FETCH" else len(complete))
    finally:
        await tx.rollback()
        await conn.close()
