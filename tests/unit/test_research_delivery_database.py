"""Owned loopback PostgreSQL: current economic delivery remains tenant-scoped."""

from datetime import timedelta
import os
from unittest.mock import AsyncMock

import pytest

from backend.common.database import apply_tenant_context
from backend.macro import research_acceptance_repository as acceptance
from backend.macro.daily_repository import (
    claim_daily_run,
    finish_daily_run,
    local,
    read_daily_research,
)
from backend.macro.research_delivery import read_economic_research
from tests.unit.test_manual_research_database import make_run
from tests.unit.test_research_acceptance_database import outcome
from tests.unit.test_research_delivery import summary
from tests.unit.test_research_history_repair_database import fixture

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
        reason="owned loopback 102/103 database required",
    ),
]


async def test_current_manual_archive_reads_safe_news_without_borrowing_previous_ready_or_other_tenant(
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=None),
    )
    async with fixture() as (conn, _, _, tenants, actors, _):
        dispatched = await conn.fetchval("SELECT clock_timestamp()")
        old_stamp = dispatched - timedelta(days=1)
        old = await claim_daily_run(
            conn,
            old_stamp,
            {},
            input_hash="economic-delivery-owned",
            prompt_version="fixture",
            schedule_time="00:00",
        )
        assert old is not None
        await finish_daily_run(
            conn,
            old["run_id"],
            old_stamp,
            status="READY",
            payload={"summary": "旧投资报告，不作为本轮新闻"},
        )
        _, _, run_id = await make_run(conn, tenants[0], actors[1], now=dispatched)
        validated = await conn.fetchval("SELECT clock_timestamp()")
        await finish_daily_run(
            conn,
            run_id,
            validated,
            status="FAILED",
            error_class="ResearchValidationError",
        )
        receipt = outcome(run_id, validated)
        receipt["accepted"] = summary()["accepted"]
        receipt["accepted"]["sources"][0].update(
            publishedDate=local(validated).date().isoformat(),
            retrievedAt=validated.isoformat(),
            verification="UNVERIFIED",
        )
        receipt["accepted"]["events"][0].update(
            raw="PRIVATE", plans=[{"price": "PRIVATE"}]
        )
        await acceptance.enqueue_research_archive(
            conn, run_id, {"asOf": dispatched.isoformat()}, {}, {}, validated, receipt,
            recorded_at=validated,
        )
        claim = await acceptance.claim_research_archive(conn, run_id=run_id)
        await acceptance.persist_research_archive(conn, claim)
        observed = await conn.fetchval("SELECT clock_timestamp()")
        await apply_tenant_context(conn, str(actors[1]), str(tenants[0]))

        class ReadOnlyConnection:
            async def fetchrow(self, sql, *args):
                assert sql.lstrip().startswith("SELECT") and "frozen_bundle" not in sql
                return await conn.fetchrow(sql, *args)

            async def fetchval(self, sql, *args):
                assert sql.lstrip().startswith("SELECT")
                return await conn.fetchval(sql, *args)

            async def execute(self, *args):
                raise AssertionError("economic read cannot mutate state")

        result = await read_daily_research(ReadOnlyConnection(), observed)
        economic = result["economicResearch"]
        assert result["state"] == "FAILED" and result["reportRunId"] == str(
            old["run_id"]
        )
        assert result["report"]["summary"] == "旧投资报告，不作为本轮新闻"
        safe_receipt = await acceptance.read_validation_summary(conn, run_id, observed)
        assert economic["researchId"] == str(run_id) and economic["status"] == "PARTIAL", {
            "economicStatus": economic["status"],
            "receipt": {key: (safe_receipt or {}).get(key) for key in (
                "researchId", "kind", "archiveState", "validatedAt", "recordedAt",
            )},
            "dispatched": dispatched.isoformat(), "observed": observed.isoformat(),
        }
        assert (
            economic["archiveState"] == "ARCHIVED"
            and economic["archivePending"] is False
        )
        assert economic["observations"]["events"][0]["title"] == "本轮经济观察"
        assert economic["observations"]["sources"][0]["verification"] == "UNVERIFIED"
        assert "PRIVATE" not in str(economic) and "旧投资报告" not in str(economic)
        await apply_tenant_context(conn, str(actors[2]), str(tenants[1]))
        other = await read_economic_research(ReadOnlyConnection(), run_id, observed)
        assert other["status"] == "UNKNOWN" and other["observations"]["events"] == []
