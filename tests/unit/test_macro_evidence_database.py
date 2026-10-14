"""Real SQL contract in an explicitly selected isolated validation database."""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest

from backend.common.database import create_pool
from backend.macro import evidence_repository as repo
from test_macro_evidence import NOW, payload, release

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated validation database required"),
]


async def test_real_sql_semantic_revisions_concurrent_dedup_and_read_barriers():
    pool = await create_pool(
        DSN, min_size=1, max_size=2, runtime_role="wealthsail_worker"
    )
    uri = f"https://www.stats.gov.cn/test/{uuid4()}"
    data = payload(releases=[release(sourceUri=uri)])
    try:

        async def persist():
            async with pool.acquire() as conn:
                return await repo.append_evidence(conn, data, NOW)

        counts = await asyncio.gather(persist(), persist())
        assert sum(row["releases"] for row in counts) == 1
        async with pool.acquire() as conn:
            assert (
                await repo.append_evidence(
                    conn, payload(releases=[release(sourceUri=uri, value="1.0")]), NOW
                )
            )["releases"] == 1
            rows = await conn.fetch(
                "SELECT * FROM market.macro_release_evidence WHERE source_uri=$1 ORDER BY revision",
                uri,
            )
            assert [row["revision"] for row in rows] == [1, 2]
            assert all(row["published_at"] is None for row in rows)
            read = await repo.read_evidence(conn, NOW)
            assert (
                len([row for row in read["releases"] if row["source_uri"] == uri]) == 1
            )
            read = await repo.read_evidence(conn, NOW - timedelta(seconds=1))
            assert not any(row["source_uri"] == uri for row in read["releases"])
            for table in (
                "macro_release_evidence",
                "macro_release_schedules",
                "macro_events",
                "macro_event_direction_links",
                "macro_evidence_attempts",
            ):
                privileges = await conn.fetchrow(
                    "SELECT has_table_privilege('wealthsail_api',$1,'SELECT') AS api_read,has_table_privilege('wealthsail_api',$1,'INSERT') AS api_write,has_table_privilege('wealthsail_worker',$1,'INSERT') AS worker_insert,has_table_privilege('wealthsail_worker',$1,'UPDATE') AS worker_update,has_table_privilege('wealthsail_worker',$1,'DELETE') AS worker_delete,has_table_privilege('authenticated',$1,'SELECT') AS public_read",
                    "market." + table,
                )
                assert dict(privileges) == {
                    "api_read": True,
                    "api_write": False,
                    "worker_insert": True,
                    "worker_update": False,
                    "worker_delete": False,
                    "public_read": False,
                }
    finally:
        await pool.close()


async def test_real_sql_claim_blocks_other_instances_then_deduplicates_restart():
    pool = await create_pool(
        DSN, min_size=1, max_size=2, runtime_role="wealthsail_worker"
    )
    try:

        async def claim():
            async with pool.acquire() as conn:
                return await repo.claim_refresh(conn, NOW, force=True)

        claims = await asyncio.gather(claim(), claim())
        won = [row for row in claims if row is not None]
        assert len(won) == 1
        async with pool.acquire() as conn:
            await repo.finish_refresh(conn, won[0], NOW, {"status": "COMPLETED"})
            assert await repo.claim_refresh(conn, NOW + timedelta(minutes=15)) is None
    finally:
        await pool.close()


@pytest.mark.parametrize("kind", ["releases", "schedules", "events"])
async def test_reverting_to_an_earlier_fact_still_appends_a_real_revision(kind):
    pool = await create_pool(
        DSN, min_size=1, max_size=1, runtime_role="wealthsail_worker"
    )
    uri = f"https://www.stats.gov.cn/test/{uuid4()}"
    a = release(sourceUri=uri)
    b = {**a, "value": "1.0"}
    if kind == "schedules":
        a = {**a, "scheduledAt": NOW + timedelta(days=5)}
        b = {**a, "scheduledAt": NOW + timedelta(days=6)}
    if kind == "events":
        a = {
            **a,
            "eventKey": uri,
            "title": "official title A",
            "category": "DATA_RELEASE",
            "sourceType": "OFFICIAL_FACT",
            "summary": "A",
            "directionIds": ["a-share:broad"],
            "evidence": {},
        }
        b = {**a, "title": "official title B", "summary": "B"}
    try:
        async with pool.acquire() as conn:
            for row in [a, b, a]:
                count = await repo.append_evidence(conn, {kind: [row]}, NOW)
                assert count[kind] == 1
            count = await repo.append_evidence(conn, {kind: [a]}, NOW)
            assert count[kind] == 0
            table = {
                "releases": "macro_release_evidence",
                "schedules": "macro_release_schedules",
                "events": "macro_events",
            }[kind]
            rows = await conn.fetch(
                f"SELECT revision FROM market.{table} WHERE source_uri=$1 ORDER BY revision",
                uri,
            )
            assert [row["revision"] for row in rows] == [1, 2, 3]
    finally:
        await pool.close()
