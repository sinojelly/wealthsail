"""Cold-start macro persistence/coverage executes real SQL under Worker authority."""

from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock

import asyncpg
import pytest

from backend.dependencies import now_shanghai
from tests.integration.test_investment_database import database_url


@pytest.mark.asyncio
async def test_macro_bootstrap_backfills_and_then_skips_with_real_worker_permissions(
    monkeypatch,
):
    from backend.jobs import (
        investment_macro as job,
        investment_macro_refresh as refresh,
    )

    conn = await asyncpg.connect(database_url())
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(
            "DELETE FROM market.market_observations WHERE subject_type='MACRO_SERIES' AND field_code=ANY($1::text[])",
            list(refresh._SOURCES),
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")

        @asynccontextmanager
        async def connection(_pool):
            yield conn

        monkeypatch.setattr(job, "system_connection", connection)
        monkeypatch.setattr(refresh, "system_connection", connection)
        now = now_shanghai()
        first_month = (now.year - 5) * 12 + now.month - 1
        points = [
            {
                "fieldCode": code,
                "period": date((first_month + n) // 12, (first_month + n) % 12 + 1, 1),
                "value": 50 if code == "PMI" else 1,
                "sourceUri": source,
            }
            for code, source in refresh._SOURCES.items()
            for n in range(60)
        ]
        fetch = AsyncMock(return_value=points)
        monkeypatch.setattr(job, "fetch_macro_history", fetch)
        result = await refresh.ensure_macro_history(object(), now)
        assert result["status"] == "COMPLETED"
        assert result["observations"] == 240
        assert all(
            item["complete"] and item["fresh"] and item["refreshed"]
            for item in result["coverage"].values()
        )
        assert await conn.fetchval("SELECT current_user::text") == "wealthsail_worker"
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM market.source_snapshots WHERE retrieved_at=$1 AND retrieval_key LIKE 'research-macro:%' AND published_at IS NOT NULL)",
            now,
        )
        skipped = await refresh.ensure_macro_history(object(), now)
        assert skipped["status"] == "SKIPPED"
        fetch.assert_awaited_once()
    finally:
        await tx.rollback()
        await conn.close()
