"""Automatic research NAV preparation persists actual, reusable histories."""

from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from backend.investment import discovery, theme_graph
from test_theme_graph import NOW, nav_history


@pytest.mark.asyncio
async def test_automatic_discovery_collects_long_missing_history_and_appends_shared_cache(
    monkeypatch,
):
    conn = AsyncMock()
    conn.fetch.return_value = []

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(discovery, "system_connection", acquire)
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    assets = [{"fundCode": "008281", "overseasCalendarRequired": False}]
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=assets)
    )
    history = nav_history()
    collected = AsyncMock(
        return_value={
            "fundHistories": [history],
            "themeNavSourceResults": {
                "008281": {"status": "SUCCESS", "records": len(history["points"])}
            },
        }
    )
    monkeypatch.setattr(theme_graph, "collect_theme_nav_histories", collected)
    result = await discovery.collect_discovery_nav_histories(object(), NOW)
    assert result["fundHistories"][0] == history
    assert any(
        "INSERT INTO market.fund_research_nav_histories" in call.args[0]
        for call in conn.execute.await_args_list
    )
    assert collected.await_args.kwargs["minimum_points"] == 550


@pytest.mark.asyncio
async def test_read_only_packet_uses_newer_shared_unit_nav_history_without_network(
    monkeypatch,
):
    conn = AsyncMock()
    history = nav_history()
    conn.fetch.return_value = [{"payload": history}]
    asset = {
        "subjectKind": "nav",
        "subjectId": "008281",
        "fundCode": "008281",
        "market": "FUND_CN",
        "label": "真实芯片",
        "overseasCalendarRequired": False,
    }
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=[asset])
    )
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=history["calendarProof"])
    )
    old = deepcopy(history)
    old["points"][-1]["close"] = "9.9999"
    old["retrievedAt"] = old["availableAt"] = "2026-10-17T08:30:00+08:00"
    result = await discovery.build_discovery_packet(
        conn, {}, {"fundHistories": [old]}, [], {}, NOW
    )
    assert result["discoveryActionableZones"][0]["currentPrice"] != "9.9999"
    assert result["quantitativeScan"]["items"][0]["historySampleCount"] == len(
        history["points"]
    )


@pytest.mark.asyncio
async def test_daily_model_prepares_core_nav_even_without_macro_collection_service(
    monkeypatch,
):
    from test_daily_research_job import isolate, settings, NOW as JOB_NOW

    job, state = isolate(monkeypatch)
    prepared = AsyncMock(side_effect=lambda pool, now, external, **kwargs: external)
    monkeypatch.setattr(job, "prepare_research_inputs", prepared)
    result = await job.sync_daily_research(
        object(), settings(alpha_public_enabled=False), JOB_NOW
    )
    assert result["status"] == "READY"
    prepared.assert_awaited_once()
    assert state["terminal"][0]["status"] == "READY"


@pytest.mark.asyncio
async def test_daily_freezes_completed_partial_batch_at_actual_clock_without_recollecting_nav(
    monkeypatch,
):
    from datetime import timedelta
    from backend.jobs import alpha_public
    from backend.macro import daily_pipeline_repository, alpha_repository
    from test_daily_pipeline import valid_partial_bundle
    from test_daily_research_job import isolate, settings, NOW as JOB_NOW

    quality_gate = daily_pipeline_repository.source_quality
    job, state = isolate(monkeypatch)
    after_collection = JOB_NOW + timedelta(minutes=3)
    monkeypatch.setattr(job, "now_shanghai", lambda: after_collection)
    monkeypatch.setattr(daily_pipeline_repository, "source_quality", quality_gate)
    monkeypatch.setattr(
        daily_pipeline_repository,
        "scheduled_source_batch",
        AsyncMock(
            return_value={
                "id": "source-batch",
                "run_date": JOB_NOW.date(),
                "status": "PARTIAL",
                "claimed_at": JOB_NOW,
                "completed_at": after_collection,
            }
        ),
    )
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value=valid_partial_bundle(after_collection)),
    )
    monkeypatch.setattr(
        alpha_public, "sync_alpha_public", AsyncMock(return_value={"status": "SKIPPED"})
    )
    prepared = AsyncMock(side_effect=lambda pool, now, external, **kwargs: external)
    monkeypatch.setattr(job, "prepare_research_inputs", prepared)
    result = await job.sync_daily_research(
        object(), settings(alpha_public_enabled=True), JOB_NOW
    )
    assert result["status"] == "READY"
    assert prepared.await_args.args[1] == after_collection
    assert prepared.await_args.kwargs["source_batch_id"] == "source-batch"
    assert job.load_public_packet.await_args.kwargs["source_batch_id"] == "source-batch"
    assert state["terminal"][0]["payload"]["inputQuality"]["allowed"] is True
    assert state["terminal"][0]["payload"]["inputQuality"]["status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_complete_short_history_is_reused_without_repeated_impossible_backfill(
    monkeypatch,
):
    from test_disclosed_nav_opportunities import disclosed_history
    from datetime import timedelta

    history = disclosed_history()
    # A complete short result can be reused only after searching the full
    # deep-sync interval, rather than proving just its old one-month query.
    history["providerHistoryProof"]["queryStart"] = (
        NOW.date() - timedelta(days=900)
    ).isoformat()
    conn = AsyncMock()
    conn.fetch.return_value = [{"fund_code": "162411", "payload": history}]

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(discovery, "system_connection", acquire)
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=history["calendarProof"])
    )
    monkeypatch.setattr(
        discovery,
        "read_discovery_assets",
        AsyncMock(
            return_value=[{"fundCode": "162411", "overseasCalendarRequired": True}]
        ),
    )
    collector = AsyncMock(
        return_value={"fundHistories": [], "themeNavSourceResults": {}}
    )
    monkeypatch.setattr(theme_graph, "collect_theme_nav_histories", collector)
    result = await discovery.collect_discovery_nav_histories(object(), NOW)
    assert result["fundHistories"][0]["fundCode"] == "162411"
    assert collector.await_args.kwargs["asset_rows"] == []
    assert result["themeNavSourceResults"]["162411"]["records"] < 220


@pytest.mark.asyncio
async def test_complete_but_shallow_query_is_not_a_deep_backfill_proof(monkeypatch):
    from test_disclosed_nav_opportunities import disclosed_history

    history = disclosed_history()  # Complete only within a one-month interval.
    conn = AsyncMock()
    conn.fetch.return_value = [{"fund_code": "162411", "payload": history}]

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(discovery, "system_connection", acquire)
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        discovery,
        "read_discovery_assets",
        AsyncMock(
            return_value=[{"fundCode": "162411", "overseasCalendarRequired": True}]
        ),
    )
    collector = AsyncMock(
        return_value={"fundHistories": [], "themeNavSourceResults": {}}
    )
    monkeypatch.setattr(theme_graph, "collect_theme_nav_histories", collector)
    await discovery.collect_discovery_nav_histories(object(), NOW)
    assert collector.await_args.kwargs["minimum_points"] == 550
    assert collector.await_args.kwargs["asset_rows"] == [
        {"asset_code": "162411", "overseas_calendar_required": True}
    ]
