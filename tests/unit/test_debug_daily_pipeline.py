"""The explicit path must not reuse the scheduled day's old public packet."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_daily_research_job import isolate, settings, NOW


@pytest.mark.asyncio
async def test_debug_before_0830_ignores_scheduled_consumption_and_binds_batch(
    monkeypatch,
):
    mod, _ = isolate(monkeypatch)
    request, batch = uuid4(), uuid4()
    early = NOW.replace(hour=7)
    monkeypatch.setattr(mod, "now_shanghai", lambda: early)
    result = await mod.sync_daily_research(
        object(),
        settings(app_env="development", daily_research_debug_enabled=True),
        early,
        debug_request_id=request,
        source_batch_id=batch,
    )
    assert result["status"] == "READY"
    mod.consumed.assert_not_awaited()
    assert mod.load_public_packet.await_args.kwargs == {"debug_request_id": request}
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_debug_disabled_environment_cannot_bypass_by_calling_pipeline_directly(
    monkeypatch,
):
    mod, _ = isolate(monkeypatch)
    result = await mod.sync_daily_research(
        object(),
        settings(app_env="production", daily_research_debug_enabled=True),
        NOW,
        debug_request_id=uuid4(),
        source_batch_id=uuid4(),
    )
    assert result["status"] == "DISABLED"
    mod.collect_daily_sources.assert_not_awaited()


@pytest.mark.asyncio
async def test_debug_collection_claim_is_bound_to_same_request(monkeypatch):
    from test_alpha_public_job import Pool, bundle, empty_nav
    from backend.jobs import alpha_public

    identity = uuid4()
    claim = AsyncMock(return_value={"id": uuid4()})
    monkeypatch.setattr(alpha_public, "claim_alpha_batch", claim)
    monkeypatch.setattr(alpha_public, "finish_alpha_batch", AsyncMock())
    result = await alpha_public.sync_alpha_public(
        Pool(),
        NOW,
        debug_request_id=identity,
        collector=AsyncMock(return_value=bundle()),
        nav_collector=empty_nav,
        now_provider=lambda: NOW,
    )
    assert result["status"] == "READY"
    assert claim.await_args.kwargs == {"debug_request_id": identity}
