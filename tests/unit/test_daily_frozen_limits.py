"""Existing pipelines obey their original absolute cutoffs after config changes."""

import asyncio
from datetime import timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from test_daily_research_job import NOW, isolate, settings


def owned_pipeline(*, runtime_minutes=120):
    started = NOW - timedelta(hours=1)
    return {
        "id": "pipeline",
        "lease_token": "owner",
        "stage": "COLLECTING",
        "scheduled_for": started,
        "started_at": started,
        "start_window_ends_at": started + timedelta(hours=2),
        "runtime_deadline_at": started + timedelta(minutes=runtime_minutes),
    }


def public_pipeline(row):
    return {
        "id": row["id"],
        "state": "RUNNING",
        "stage": "WAITING_SOURCES",
        "scheduledFor": row["scheduled_for"],
        "startedAt": row["started_at"],
        "startWindowEndsAt": row["start_window_ends_at"],
        "runtimeDeadlineAt": row["runtime_deadline_at"],
        "finishedAt": None,
        "researchRunId": None,
        "errorClass": None,
    }


@pytest.mark.asyncio
async def test_shortened_window_cannot_end_an_existing_pipelines_recovery(monkeypatch):
    from backend.macro import daily_pipeline_repository as repo

    mod, state = isolate(monkeypatch)
    now = NOW + timedelta(minutes=35)
    owned = owned_pipeline()
    monkeypatch.setattr(mod, "now_shanghai", lambda: now)
    repo.read_pipeline.return_value = public_pipeline(owned)
    repo.claim_pipeline.side_effect = None
    repo.claim_pipeline.return_value = owned
    result = await mod.sync_daily_research(
        object(), settings(daily_research_start_window_minutes=60), now
    )
    assert result["status"] == "READY"
    assert state["consumed"] is True


@pytest.mark.asyncio
async def test_shortened_runtime_cannot_cut_off_existing_preparation(monkeypatch):
    from backend.macro import daily_pipeline_repository as repo

    mod, state = isolate(monkeypatch)
    owned = owned_pipeline()
    repo.claim_pipeline.side_effect = None
    repo.claim_pipeline.return_value = owned
    result = await mod.sync_daily_research(
        object(), settings(daily_research_pipeline_timeout_seconds=600), NOW
    )
    assert result["status"] == "READY"
    assert state["consumed"] is True


@pytest.mark.asyncio
async def test_extended_runtime_cannot_revive_an_expired_pipeline(monkeypatch):
    from backend.jobs import alpha_public
    from backend.macro import daily_pipeline_repository as repo

    mod, state = isolate(monkeypatch)
    owned = owned_pipeline(runtime_minutes=60)
    repo.claim_pipeline.side_effect = None
    repo.claim_pipeline.return_value = owned
    result = await mod.sync_daily_research(
        object(), settings(daily_research_pipeline_timeout_seconds=14400), NOW
    )
    assert result["status"] == "FAILED"
    assert result["errorClass"] == "PipelineRuntimeExpired"
    assert state["consumed"] is False
    alpha_public.sync_alpha_public.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


def test_remaining_runtime_uses_the_frozen_utc_deadline():
    from backend.jobs.daily_research import _remaining_runtime

    owned = owned_pipeline()
    owned["runtime_deadline_at"] = owned["runtime_deadline_at"].astimezone(timezone.utc)
    assert (
        _remaining_runtime(
            owned, settings(daily_research_pipeline_timeout_seconds=600), NOW
        )
        == 3600
    )


@pytest.mark.asyncio
async def test_first_claim_receives_configured_intervals_for_persistence(monkeypatch):
    from backend.macro import daily_pipeline_repository as repo

    mod, _ = isolate(monkeypatch)
    await mod.sync_daily_research(
        object(),
        settings(
            daily_research_start_window_minutes=90,
            daily_research_pipeline_timeout_seconds=9000,
        ),
        NOW,
    )
    args = repo.claim_pipeline.await_args.kwargs
    assert args["start_window_minutes"] == 90
    assert args["pipeline_timeout_seconds"] == 9000


@pytest.mark.asyncio
async def test_api_uses_frozen_window_when_current_configuration_is_shortened(
    monkeypatch,
):
    from backend.macro import daily_repository as api, daily_pipeline_repository

    now = NOW + timedelta(minutes=35)
    owned = owned_pipeline()
    monkeypatch.setattr(
        daily_pipeline_repository,
        "read_pipeline",
        AsyncMock(return_value=public_pipeline(owned)),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    result = await api.read_daily_research(conn, now, start_window_minutes=60)
    assert result["state"] == "RUNNING"
    assert result["scheduleState"] == "DISPATCHED"
    assert result["pipeline"]["startWindowEndsAt"] == NOW + timedelta(hours=1)
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_waits_for_live_pre_model_lease_after_window_then_archives(
    monkeypatch,
):
    from backend.jobs import alpha_public
    from backend.macro import daily_pipeline_repository as repo

    mod, _ = isolate(monkeypatch)
    clock = [NOW.replace(hour=9, minute=1)]
    owned = owned_pipeline(runtime_minutes=180)
    stored = public_pipeline(owned)
    stored["stage"] = "COLLECTING"
    lease_end = clock[0] + timedelta(minutes=1)
    monkeypatch.setattr(mod, "now_shanghai", lambda: clock[0])
    repo.read_pipeline.side_effect = lambda *_: dict(stored)

    async def archive(conn, now):
        if now >= lease_end:
            stored["state"] = "MISSED"

    monkeypatch.setattr(
        repo, "finish_source_wait_window", AsyncMock(side_effect=archive)
    )
    stop = asyncio.Event()
    delays = []

    async def wait(event, seconds):
        delays.append(seconds)
        if seconds == 60:
            clock[0] += timedelta(seconds=seconds)
        else:
            event.set()

    await mod.run_daily_research_service(
        object(),
        settings(),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert delays[0] == 60
    assert stored["state"] == "MISSED"
    repo.claim_pipeline.assert_not_awaited()
    alpha_public.sync_alpha_public.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()
