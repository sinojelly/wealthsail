"""Restart observation never grants permission to replay or reuse yesterday's input."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from test_daily_frozen_limits import owned_pipeline, public_pipeline
from test_daily_research_job import NOW, isolate, settings


def isolate_edges(monkeypatch):
    from backend.macro import daily_pipeline_repository as repo

    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(
        repo, "archive_prior_pipelines", AsyncMock(return_value=0), raising=False
    )
    return mod, state, repo


def stored_pipeline(*, started=None, stage="COLLECTING", window_minutes=120):
    started = started or NOW.replace(hour=7)
    row = owned_pipeline()
    row.update(
        scheduled_for=started,
        started_at=started,
        start_window_ends_at=started + timedelta(minutes=window_minutes),
        runtime_deadline_at=started + timedelta(hours=4),
    )
    public = public_pipeline(row)
    public["stage"] = stage
    return row, public


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_owner", [False, True])
async def test_transient_archive_failure_preserves_the_due_plan_or_owner_observation(
    monkeypatch, existing_owner
):
    mod, state, repo = isolate_edges(monkeypatch)
    clock = [
        NOW.replace(hour=9, minute=1)
        if existing_owner
        else NOW.replace(hour=6, minute=59)
    ]
    _, stored = stored_pipeline()
    if existing_owner:
        repo.read_pipeline.side_effect = lambda *_: dict(stored)
    monkeypatch.setattr(mod, "now_shanghai", lambda: clock[0])
    attempts = []

    async def archive(conn, stamp):
        attempts.append(stamp)
        if len(attempts) == 2:
            # Startup has succeeded; a transient failure in loop maintenance
            # must neither terminate the service nor advance its pending plan.
            assert not state["consumed"]
            repo.claim_pipeline.assert_not_awaited()
            mod.run_codex_research.assert_not_awaited()
            raise ConnectionError("temporary archive database outage")
        return False

    repo.archive_prior_pipelines.side_effect = archive

    async def close_window(conn, stamp):
        stored["state"] = "MISSED"

    monkeypatch.setattr(
        repo, "finish_source_wait_window", AsyncMock(side_effect=close_window)
    )
    repo.scheduled_source_batch.side_effect = None
    repo.scheduled_source_batch.return_value = {
        "id": "source-batch",
        "run_date": clock[0].date(),
        "status": "READY",
        "completed_at": clock[0],
    }
    mod.load_public_packet.side_effect = lambda *_, **__: {"asOf": clock[0].isoformat()}
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        if len(delays) == 1:
            assert seconds == 60
            assert not state["consumed"]
            clock[0] += timedelta(seconds=seconds)
        else:
            event.set()

    await mod.run_daily_research_service(
        object(),
        settings(daily_research_catch_up=not existing_owner),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert delays[0] == 60
    assert len(attempts) >= 3
    if existing_owner:
        assert stored["state"] == "MISSED"
        assert not state["consumed"]
        repo.claim_pipeline.assert_not_awaited()
        mod.run_codex_research.assert_not_awaited()
    else:
        assert repo.claim_pipeline.await_args.args[1] == NOW.replace(hour=7)
        assert state["terminal"][-1]["status"] == "READY"
        mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_catch_up_disabled_still_observes_live_owner_after_window_until_archived(
    monkeypatch,
):
    mod, state, repo = isolate_edges(monkeypatch)
    clock = [NOW.replace(hour=9, minute=1)]
    _, stored = stored_pipeline()
    lease_end = clock[0] + timedelta(minutes=1)
    repo.read_pipeline.side_effect = lambda *_: dict(stored)
    monkeypatch.setattr(mod, "now_shanghai", lambda: clock[0])

    async def close_window(conn, now):
        if now >= lease_end:
            stored["state"] = "MISSED"

    monkeypatch.setattr(
        repo, "finish_source_wait_window", AsyncMock(side_effect=close_window)
    )
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        if seconds == 60:
            clock[0] += timedelta(seconds=60)
        else:
            event.set()

    await mod.run_daily_research_service(
        object(),
        settings(daily_research_catch_up=False),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert delays[0] == 60
    assert stored["state"] == "MISSED"
    assert not state["consumed"]
    repo.claim_pipeline.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_catch_up_disabled_without_existing_task_waits_for_next_plan(monkeypatch):
    mod, state, repo = isolate_edges(monkeypatch)
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        event.set()

    await mod.run_daily_research_service(
        object(),
        settings(daily_research_catch_up=False),
        stop_event=stop,
        now_provider=lambda: NOW,
        wait=wait,
    )
    assert delays == [23 * 3600]
    assert not state["consumed"]
    repo.claim_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_observation_only_does_not_reclaim_expired_preparation_inside_window(
    monkeypatch,
):
    from backend.jobs import alpha_public

    mod, state, repo = isolate_edges(monkeypatch)
    _, stored = stored_pipeline()
    stored.update(state="UNCERTAIN", errorClass="PipelineLeaseExpired")
    repo.read_pipeline.return_value = stored
    await mod.sync_daily_research(
        object(), settings(daily_research_catch_up=False), NOW, observe_only=True
    )
    assert not state["consumed"]
    repo.claim_pipeline.assert_not_awaited()
    alpha_public.sync_alpha_public.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_observation_only_archives_expired_preparation_after_frozen_window(
    monkeypatch,
):
    mod, state, repo = isolate_edges(monkeypatch)
    now = NOW.replace(hour=9, minute=1)
    _, stored = stored_pipeline()
    stored.update(state="MISSED", errorClass="SourceWaitWindowExpired")
    # API DTO already projects MISSED for expired preparation; the worker must
    # still persist the terminal state rather than merely render that projection.
    repo.read_pipeline.side_effect = lambda *_: dict(stored)
    archived = []

    async def close_window(conn, stamp):
        archived.append(stamp)

    monkeypatch.setattr(
        repo, "finish_source_wait_window", AsyncMock(side_effect=close_window)
    )
    result = await mod.sync_daily_research(
        object(), settings(daily_research_catch_up=False), now, observe_only=True
    )
    assert result["status"] == "MISSED"
    assert archived == [now]
    assert not state["consumed"]
    repo.claim_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_time_start_keeps_waiting_for_its_sources_when_catch_up_is_disabled(
    monkeypatch,
):
    mod, state, repo = isolate_edges(monkeypatch)
    clock = [NOW.replace(hour=7)]
    row, stored = stored_pipeline(stage="WAITING_SOURCES")
    claim_count = 0

    async def claim(conn, now, **kwargs):
        nonlocal claim_count
        claim_count += 1
        return dict(row)

    repo.claim_pipeline.side_effect = claim
    repo.read_pipeline.side_effect = lambda *_: dict(stored) if claim_count else None
    repo.scheduled_source_batch.side_effect = [
        {
            "id": "source-batch",
            "run_date": clock[0].date(),
            "status": "CLAIMED",
            "completed_at": None,
        },
        {
            "id": "source-batch",
            "run_date": clock[0].date(),
            "status": "READY",
            "completed_at": clock[0],
        },
    ]
    monkeypatch.setattr(mod, "now_shanghai", lambda: clock[0])
    mod.load_public_packet.return_value = {"asOf": clock[0].isoformat()}
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        if seconds == 60:
            clock[0] += timedelta(seconds=60)
        else:
            event.set()

    await mod.run_daily_research_service(
        object(),
        settings(daily_research_catch_up=False),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert delays[0] == 60
    assert claim_count == 2
    assert state["terminal"][-1]["status"] == "READY"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_cross_day_restart_observes_old_owner_then_runs_todays_plan(monkeypatch):
    from backend.jobs import alpha_public

    mod, state, repo = isolate_edges(monkeypatch)
    yesterday = NOW.replace(hour=23, minute=59)
    clock = [yesterday + timedelta(minutes=11)]
    lease_end = clock[0] + timedelta(minutes=1)
    _, stored = stored_pipeline(started=yesterday)
    repo.read_pipeline.side_effect = (
        lambda *_: dict(stored) if stored["state"] == "RUNNING" else None
    )
    archives, claims = [], []

    async def archive(conn, stamp):
        if stored["state"] == "RUNNING" and stamp >= lease_end:
            archives.append(stamp)
            stored["state"] = "MISSED"
        return stored["state"] == "RUNNING"

    monkeypatch.setattr(
        repo, "archive_prior_pipelines", AsyncMock(side_effect=archive), raising=False
    )

    async def claim(conn, stamp, **kwargs):
        claims.append(stamp)
        assert stamp == NOW.replace(day=17, hour=23, minute=59)
        return {
            "id": "today-pipeline",
            "lease_token": "today-owner",
            "stage": "COLLECTING",
            "started_at": stamp,
            "scheduled_for": stamp,
            "start_window_ends_at": stamp + timedelta(hours=2),
            "runtime_deadline_at": stamp + timedelta(hours=2),
        }

    repo.claim_pipeline.side_effect = claim
    repo.scheduled_source_batch.side_effect = None
    repo.scheduled_source_batch.return_value = {
        "id": "today-batch",
        "run_date": clock[0].date(),
        "status": "READY",
        "completed_at": clock[0],
    }
    monkeypatch.setattr(mod, "now_shanghai", lambda: clock[0])
    mod.load_public_packet.side_effect = lambda *_, **__: {"asOf": clock[0].isoformat()}
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        if state["consumed"]:
            event.set()
        else:
            if stored["state"] == "RUNNING":
                assert seconds == 60, "yesterday's active owner must still be observed"
            assert len(delays) < 5, (
                "old owner observation must finish, not loop forever"
            )
            clock[0] += timedelta(seconds=seconds)

    await mod.run_daily_research_service(
        object(),
        settings(daily_research_schedule_time="23:59"),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert delays[0] == 60
    assert stored["state"] == "MISSED"
    assert archives
    assert claims == [NOW.replace(day=17, hour=23, minute=59)]
    assert state["terminal"][-1]["status"] == "READY"
    alpha_public.sync_alpha_public.assert_not_awaited()
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_preparation_crossing_midnight_is_missed_without_consuming_model_slot(
    monkeypatch,
):
    mod, state, _ = isolate_edges(monkeypatch)
    started = NOW.replace(hour=23, minute=59)
    monkeypatch.setattr(mod, "now_shanghai", lambda: started + timedelta(minutes=11))
    result = await mod._execute_daily_research(
        object(),
        settings(daily_research_schedule_time="23:59"),
        started,
        packet={"asOf": started.isoformat()},
    )
    assert result == {"status": "MISSED", "errorClass": "SourcePreparationCrossedDay"}
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_preparation_crossing_midnight_persists_known_missed_outcome(
    monkeypatch,
):
    mod, state, repo = isolate_edges(monkeypatch)
    started = NOW.replace(hour=23, minute=59)
    clock = [started]
    row, _ = stored_pipeline(started=started)
    monkeypatch.setattr(mod, "now_shanghai", lambda: clock[0])
    repo.scheduled_source_batch.side_effect = None
    repo.scheduled_source_batch.return_value = {
        "id": "yesterday-batch",
        "run_date": started.date(),
        "status": "READY",
        "completed_at": started,
    }

    async def prepare(*args, **kwargs):
        clock[0] += timedelta(minutes=11)
        return {}

    mod.prepare_research_inputs.side_effect = prepare
    mod.load_public_packet.return_value = {"asOf": started.isoformat()}
    result = await mod._run_owned_pipeline(
        object(), settings(daily_research_schedule_time="23:59"), started, row
    )
    assert result == {"status": "MISSED", "errorClass": "SourcePreparationCrossedDay"}
    assert state["pipeline_events"][-1]["state"] == "MISSED"
    assert state["pipeline_events"][-1]["error_class"] == "SourcePreparationCrossedDay"
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()
