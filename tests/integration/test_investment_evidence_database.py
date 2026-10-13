"""Real database validation for governed investment sources and evidence."""
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.test_investment_database import database_url


@pytest.mark.asyncio
async def test_worker_can_register_fund_source_without_changing_identity():
    from backend.common.database import create_pool
    from scripts.register_investment_reference import ReferenceImport, register

    conn = await asyncpg.connect(database_url())
    key = uuid4()
    try:
        await conn.execute("""INSERT INTO reference.instruments
          (id,asset_type,primary_market,canonical_name,metadata)
          VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','source-contract',
            '{"unrelated":7,"research":{"keep":true}}')""", key)
        pool = await create_pool(database_url(), min_size=1, max_size=1,
                                 runtime_role="wealthsail_worker")
        try:
            await register(pool, ReferenceImport.model_validate({"funds": [{
                "instrumentId": str(key), "sourceUri": "https://example.org/fund/nav",
                "publishHour": 23, "returnSeriesVerified": True,
            }]}))
        finally:
            await pool.close()
        import json
        metadata = json.loads(await conn.fetchval(
            "SELECT metadata FROM reference.instruments WHERE id=$1", key))
        assert metadata["unrelated"] == 7
        assert metadata["research"]["keep"] is True
        assert metadata["research"]["returnSeriesVerified"] is True
        assert not await conn.fetchval("""SELECT has_column_privilege(
          'wealthsail_api','reference.instruments','metadata','UPDATE')""")
        assert not await conn.fetchval("""SELECT has_column_privilege(
          'wealthsail_worker','reference.instruments','canonical_name','UPDATE')""")
    finally:
        await conn.execute("DELETE FROM reference.instruments WHERE id=$1", key)
        await conn.close()
