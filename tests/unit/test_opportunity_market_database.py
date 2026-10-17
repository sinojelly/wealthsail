"""Real append/read/role contracts in the explicitly selected disposable DB."""

import os
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
import requests

from backend.common.database import create_pool, tenant_transaction
from backend.investment import decision_repository
from backend.investment.opportunity_service import get_context
from backend.macro import market_repository as repo
from test_opportunity_decision_database import seed
from test_opportunity_market import NOW, provider
from test_opportunity_monitors import result

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated validation database required"),
]


@asynccontextmanager
async def rollback_worker():
    pool = await create_pool(DSN, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            try:
                await conn.execute("SET LOCAL ROLE wealthsail_worker")
                yield conn
            finally:
                await tx.rollback()
    finally:
        await pool.close()


def isolated_provider(value="1.50", **changes):
    return provider(
        value,
        referenceDate="2024-02-13",
        sourceUri=f"https://home.treasury.gov/resource-center-data-chart-center/interest-rates/pages/xml?year=2024&fixture={uuid4()}",
        **changes,
    )


async def test_real_retrieval_only_dedup_retains_original_availability_and_numeric_hash():
    fact = isolated_provider()
    async with rollback_worker() as conn:
        assert await repo.append_series(conn, [fact], NOW) == {
            "observations": 1,
            "rejected": 0,
        }
        refreshed = {
            **fact,
            "value": Decimal("1.5"),
            "retrievedAt": NOW + timedelta(hours=1),
            "availableAt": NOW + timedelta(hours=1),
        }
        assert await repo.append_series(
            conn, [refreshed], NOW + timedelta(hours=1)
        ) == {"observations": 0, "rejected": 0}
        rows = await conn.fetch(
            "SELECT * FROM market.opportunity_series_observations WHERE source_uri=$1 ORDER BY revision",
            fact["sourceUri"],
        )
        assert len(rows) == 1 and rows[0]["revision"] == 1
        assert rows[0]["retrieved_at"] == rows[0]["available_at"] == NOW
        assert (
            rows[0]["published_at"] is None
            and rows[0]["time_basis"] == "RETRIEVAL_ONLY"
        )
        assert (
            rows[0]["canonical_hash"]
            == repo.normalize_series(refreshed, NOW + timedelta(hours=1))[
                "canonical_hash"
            ]
        )


async def test_real_reversion_appends_then_bad_unit_tombstone_never_restores_old_good_value():
    fact = isolated_provider()
    async with rollback_worker() as conn:
        for value in ("1.5", "2.0", "1.5"):
            assert (await repo.append_series(conn, [{**fact, "value": value}], NOW))[
                "observations"
            ] == 1
        assert (await repo.append_series(conn, [{**fact, "value": "1.5"}], NOW))[
            "observations"
        ] == 0
        assert (await repo.append_series(conn, [{**fact, "unit": "bp"}], NOW))[
            "observations"
        ] == 1
        rows = await conn.fetch(
            "SELECT * FROM market.opportunity_series_observations WHERE source_uri=$1 ORDER BY revision",
            fact["sourceUri"],
        )
        assert [row["revision"] for row in rows] == [1, 2, 3, 4]
        assert rows[0]["canonical_hash"] == rows[2]["canonical_hash"]
        assert rows[3]["value"] is None
        visible = [
            row
            for row in await repo.read_series(conn, NOW)
            if row["source_uri"] == fact["sourceUri"]
        ]
        assert len(visible) == 1 and visible[0]["revision"] == 4
        item = {row["code"]: row for row in repo.series_context(visible, NOW)}[
            fact["code"]
        ]
        assert item["state"] == "CONFLICT" and item["value"] is None
        assert (
            item["changes"]["previousValue"] is None
            and item["history"][-1]["state"] == "CONFLICT"
        )


async def test_actual_api_and_worker_roles_cannot_write_permission_or_rewrite_facts():
    async with rollback_worker() as conn:
        denied = await conn.fetchrow("""SELECT has_table_privilege('wealthsail_api','market.opportunity_series_observations','INSERT') AS api_insert,
          has_table_privilege('wealthsail_api','market.opportunity_series_observations','UPDATE') AS api_update,
          has_table_privilege('wealthsail_worker','market.opportunity_series_observations','UPDATE') AS worker_update,
          has_table_privilege('wealthsail_worker','market.opportunity_series_observations','DELETE') AS worker_delete,
          has_table_privilege('wealthsail_api','reference.opportunity_source_permissions','INSERT') AS api_approve,
          has_table_privilege('wealthsail_worker','reference.opportunity_source_permissions','INSERT') AS worker_approve""")
        assert not any(denied.values())
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO reference.opportunity_source_permissions(source_key,status,purpose) VALUES($1,'APPROVED','test')",
                    uuid4().hex,
                )
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE wealthsail_api")
            assert {
                row["source_key"]
                for row in await conn.fetch(
                    "SELECT source_key FROM reference.opportunity_source_permissions"
                )
            } >= {"CHINABOND", "CFETS"}
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE reference.opportunity_source_permissions SET status='APPROVED' WHERE source_key='CFETS'"
                    )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE market.opportunity_series_observations SET value=999 WHERE code='US_T10_REAL'"
                    )


async def test_actual_actor_context_keeps_two_markets_and_private_notices_without_http(
    monkeypatch,
):
    pool = await create_pool(DSN, min_size=1, max_size=2)
    worker = await create_pool(
        DSN, min_size=1, max_size=2, runtime_role="wealthsail_worker"
    )
    no_http = AsyncMock(side_effect=AssertionError("read context must never call HTTP"))
    no_sync_http = Mock(side_effect=AssertionError("read context must never call HTTP"))
    monkeypatch.setattr(httpx.AsyncClient, "request", no_http)
    monkeypatch.setattr(httpx.Client, "request", no_sync_http)
    monkeypatch.setattr(requests.Session, "request", no_sync_http)
    try:
        tenant, actor, other = await seed(pool)
        scope = {"direction_id": "gold:physical", "index_id": "au9999"}
        async with tenant_transaction(pool, actor, tenant) as conn:
            monitor = await decision_repository.set_monitor(
                conn, tenant, actor, scope, True, NOW - timedelta(days=1)
            )
        async with worker.acquire() as conn:
            monitor = dict(
                await conn.fetchrow(
                    "SELECT * FROM risk.opportunity_condition_monitors WHERE id=$1",
                    UUID(monitor["id"]),
                )
            )
            async with conn.transaction():
                await decision_repository.record_monitor_result(
                    conn, monitor, result(), NOW - timedelta(minutes=30)
                )
            async with conn.transaction():
                await decision_repository.record_monitor_result(
                    conn, monitor, result("HOLD_REVIEW"), NOW - timedelta(minutes=15)
                )
        async with tenant_transaction(pool, actor, tenant) as conn:
            assert await conn.fetchval("SELECT current_user::text") == "wealthsail_api"
            gold = await get_context(
                conn,
                tenant,
                actor,
                direction_id="gold:physical",
                index_id="au9999",
                now=NOW,
            )
            ashare = await get_context(
                conn,
                tenant,
                actor,
                direction_id="a-share:broad",
                index_id="hs300",
                now=NOW,
            )
            assert (
                gold["direction"]["marketId"] == "gold"
                and ashare["direction"]["marketId"] == "a-share"
            )
            assert [row["indexId"] for row in gold["marketEvidence"]] == ["au9999"]
            assert [row["indexId"] for row in ashare["marketEvidence"]] == ["hs300"]
            assert len(gold["notices"]) == 1 and ashare["notices"] == []
            assert (
                gold["portfolio"]["heldSelected"] is None
                and gold["decision"]["state"] == "INSUFFICIENT"
            )
            assert (
                gold["directionModel"]["requiredEvidence"]
                != ashare["directionModel"]["requiredEvidence"]
            )
        async with tenant_transaction(pool, other, tenant) as conn:
            assert (
                await get_context(
                    conn,
                    tenant,
                    other,
                    direction_id="gold:physical",
                    index_id="au9999",
                    now=NOW,
                )
            )["notices"] == []
        no_http.assert_not_awaited()
        no_sync_http.assert_not_called()
    finally:
        await worker.close()
        await pool.close()
