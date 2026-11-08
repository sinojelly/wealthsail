"""Final insertion statements bind explicit work to a live claimed request."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.macro.alpha_repository import claim_alpha_batch
from backend.macro.daily_repository import claim_daily_run
from tests.unit.test_research_debug_repository import NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["DEBUG", "MANUAL"])
@pytest.mark.parametrize("kind", ["alpha", "daily"])
async def test_explicit_cost_barrier_locks_request_and_rechecks_after_lock(mode, kind):
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    args = {"debug_request_id" if mode == "DEBUG" else "manual_request_id": uuid4()}
    if kind == "alpha":
        assert await claim_alpha_batch(conn, NOW, **args) is None
    else:
        assert (
            await claim_daily_run(
                conn,
                NOW,
                {},
                input_hash="fixture",
                prompt_version="fixture",
                source_batch_id=uuid4(),
                **args,
            )
            is None
        )
    sql = conn.fetchrow.await_args.args[0]
    assert "AS MATERIALIZED" in sql and "FOR UPDATE OF q" in sql
    final = sql.split("INSERT INTO", 1)[1]
    assert "q.status='RUNNING'" in final and "q.completed_at IS NULL" in final
    assert "q.started_at<=" in final and "interval '35 minutes'" in final
    assert "clock_timestamp()" in final
    if kind == "daily":
        assert "b.debug_request_id=q.id" in final
        assert "b.status IN ('READY','PARTIAL')" in final
        assert "b.completed_at<=" in final


@pytest.mark.asyncio
async def test_debug_also_requires_its_exact_collected_batch_before_any_sql():
    conn = AsyncMock()
    with pytest.raises(ValueError):
        await claim_daily_run(
            conn,
            NOW,
            {},
            input_hash="fixture",
            prompt_version="fixture",
            debug_request_id=uuid4(),
        )
    conn.fetchrow.assert_not_awaited()
