"""Daily research is global, calendar-based and never retried after dispatch."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from importlib import import_module, util
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 16, 8, tzinfo=SHANGHAI)  # Friday, inside the start window


def job():
    assert util.find_spec("backend.jobs.daily_research"), "daily execution job missing"
    return import_module("backend.jobs.daily_research")


def settings(**updates):
    return SimpleNamespace(
        **{
            "daily_research_enabled": True,
            "daily_research_catch_up": True,
            "daily_research_codex_path": "codex.exe",
            "daily_research_model": None,
            "daily_research_timeout_seconds": 900,
            **updates,
        }
    )


def isolate(monkeypatch):
    from backend.investment import discovery

    monkeypatch.setattr(
        discovery,
        "collect_discovery_nav_histories",
        AsyncMock(return_value={"fundHistories": [], "themeNavSourceResults": {}}),
    )
    mod, state = job(), {"consumed": False, "active": 0, "terminal": []}

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            state["in_transaction"] = True
            try:
                yield
            finally:
                state["in_transaction"] = False

    @asynccontextmanager
    async def connection(pool):
        state["active"] += 1
        try:
            yield Conn()
        finally:
            state["active"] -= 1

    async def claim(conn, stamp, packet, **kwargs):
        if state["consumed"]:
            return None
        state["consumed"] = True
        return {"run_id": "one"}

    async def finished(conn, run_id, stamp, **kwargs):
        state["terminal"].append(kwargs)
        state.setdefault("terminal_in_transactions", []).append(
            state.get("in_transaction", False)
        )

    async def provider(*args, **kwargs):
        assert state["active"] == 0, "no provider runs inside a DB connection"
        return {"summary": "public", "sources": []}

    monkeypatch.setattr(mod, "system_connection", connection)
    monkeypatch.setattr(
        mod, "consumed", AsyncMock(side_effect=lambda *_: state["consumed"])
    )
    monkeypatch.setattr(mod, "claim_daily_run", claim)
    monkeypatch.setattr(mod, "finish_daily_run", finished)
    monkeypatch.setattr(
        mod, "load_public_packet", AsyncMock(return_value={"asOf": NOW.isoformat()})
    )
    monkeypatch.setattr(mod, "collect_daily_sources", AsyncMock(side_effect=provider))

    async def preparation(pool, now, external, **kwargs):
        assert state["active"] == 0
        return external

    monkeypatch.setattr(
        mod, "prepare_research_inputs", AsyncMock(side_effect=preparation)
    )
    monkeypatch.setattr(
        mod,
        "preflight_codex",
        AsyncMock(
            return_value={"executable": "codex.exe", "version": "codex-cli test"}
        ),
    )
    monkeypatch.setattr(mod, "run_codex_research", AsyncMock(side_effect=provider))
    monkeypatch.setattr(mod, "verify_research_sources", AsyncMock(side_effect=provider))
    monkeypatch.setattr(mod, "build_prompt", lambda packet: "one prompt")
    monkeypatch.setattr(mod, "validate_report", lambda raw, *_, **__: raw)
    monkeypatch.setattr(mod, "now_shanghai", lambda: NOW)
    from backend.macro import daily_pipeline_repository as pipeline, alpha_repository
    from backend.jobs import alpha_public

    monkeypatch.setattr(
        pipeline,
        "claim_pipeline",
        AsyncMock(
            side_effect=lambda *_, **__: None
            if state["consumed"] or state.get("pipeline_closed")
            else {
                "id": "pipeline",
                "lease_token": "lease",
                "stage": "COLLECTING",
                "started_at": NOW,
                "scheduled_for": NOW.replace(hour=7),
                "start_window_ends_at": NOW.replace(hour=9),
                "runtime_deadline_at": NOW + timedelta(hours=2),
            }
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "scheduled_source_batch",
        AsyncMock(
            side_effect=[
                None,
                {
                    "id": "source-batch",
                    "run_date": NOW.date(),
                    "status": "READY",
                    "completed_at": NOW,
                },
            ]
        ),
    )
    monkeypatch.setattr(pipeline, "source_quality", lambda *_: {"allowed": True})
    monkeypatch.setattr(pipeline, "read_pipeline", AsyncMock(return_value=None))
    monkeypatch.setattr(pipeline, "advance_pipeline", AsyncMock(return_value=True))
    monkeypatch.setattr(
        alpha_repository, "read_alpha_public", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        alpha_public, "sync_alpha_public", AsyncMock(return_value={"status": "READY"})
    )

    async def stage(pool, claim, **fields):
        state.setdefault("pipeline_events", []).append(fields)
        claim["stage"] = fields["stage"]
        if fields.get("state", "RUNNING") != "RUNNING":
            state["pipeline_closed"] = True

    monkeypatch.setattr(mod, "_pipeline_stage", stage)
    return mod, state


@pytest.mark.asyncio
@pytest.mark.parametrize("pause", ["before_memory_flag", "after_memory_flag"])
async def test_heartbeat_cannot_turn_a_committed_terminal_result_into_ownership_loss(
    monkeypatch, pause
):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    state["committed"] = False
    owner = asyncio.current_task()
    monkeypatch.setattr(mod, "PIPELINE_HEARTBEAT_SECONDS", 0.001)

    async def renew(*args):
        return not state["committed"]

    monkeypatch.setattr(pipeline, "renew_pipeline_lease", AsyncMock(side_effect=renew))

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            state["in_transaction"] = True
            try:
                yield
                if state["terminal"]:
                    state["committed"] = True
                    if pause == "before_memory_flag":
                        await asyncio.sleep(0.035)
            finally:
                state["in_transaction"] = False

    @asynccontextmanager
    async def connection(pool):
        state["active"] += 1
        try:
            yield Conn()
        finally:
            state["active"] -= 1
            if (
                state["committed"]
                and pause == "after_memory_flag"
                and asyncio.current_task() is owner
            ):
                await asyncio.sleep(0.035)

    monkeypatch.setattr(mod, "system_connection", connection)
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert state["committed"]
    assert result["status"] == "READY"
    assert state["terminal"][-1]["status"] == "READY"


@pytest.mark.asyncio
async def test_heartbeat_cannot_misclassify_a_committed_source_wait_as_ownership_loss(
    monkeypatch,
):
    from backend.macro import daily_pipeline_repository as pipeline

    original_stage = job()._pipeline_stage
    mod, state = isolate(monkeypatch)
    owner = asyncio.current_task()
    state["released"] = False
    monkeypatch.setattr(mod, "_pipeline_stage", original_stage)
    monkeypatch.setattr(mod, "PIPELINE_HEARTBEAT_SECONDS", 0.001)
    pipeline.scheduled_source_batch.side_effect = None
    pipeline.scheduled_source_batch.return_value = {
        "id": "source-batch",
        "run_date": NOW.date(),
        "status": "CLAIMED",
        "completed_at": None,
    }

    async def advance(*args, **fields):
        if fields["stage"] == "WAITING_SOURCES":
            state["released"] = True
        return True

    async def renew(*args):
        return not state["released"]

    monkeypatch.setattr(pipeline, "advance_pipeline", AsyncMock(side_effect=advance))
    monkeypatch.setattr(pipeline, "renew_pipeline_lease", AsyncMock(side_effect=renew))

    @asynccontextmanager
    async def connection(pool):
        yield object()
        if state["released"] and asyncio.current_task() is owner:
            await asyncio.sleep(0.035)

    monkeypatch.setattr(mod, "system_connection", connection)
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "WAITING_PUBLIC_DATA"
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("new_mode", ["DAILY", "WEEKDAYS"])
async def test_recovery_uses_persisted_plan_window_instead_of_changed_environment(
    monkeypatch, new_mode
):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    # Saturday's existing DAILY task remains recoverable after changing to
    # WEEKDAYS. A changed environment governs new dates, not existing work.
    now = NOW.replace(day=17, hour=9, minute=10)
    due = now.replace(hour=8, minute=15)
    monkeypatch.setattr(mod, "now_shanghai", lambda: now)
    pipeline.read_pipeline.return_value = {
        "state": "RUNNING",
        "stage": "WAITING_SOURCES",
        "scheduledFor": due,
        "startWindowEndsAt": due + timedelta(hours=2),
        "runtimeDeadlineAt": due + timedelta(hours=2),
    }
    pipeline.claim_pipeline.side_effect = None
    pipeline.claim_pipeline.return_value = {
        "id": "pipeline",
        "lease_token": "lease",
        "stage": "COLLECTING",
        "started_at": due,
        "scheduled_for": due,
        "start_window_ends_at": due + timedelta(hours=2),
        "runtime_deadline_at": due + timedelta(hours=2),
    }
    pipeline.scheduled_source_batch.side_effect = None
    pipeline.scheduled_source_batch.return_value = {
        "id": "source-batch",
        "run_date": now.date(),
        "status": "READY",
        "completed_at": now,
    }
    mod.load_public_packet.return_value = {"asOf": now.isoformat()}
    result = await mod.sync_daily_research(
        object(),
        settings(
            daily_research_schedule_time="07:00", daily_research_schedule_mode=new_mode
        ),
        now,
    )
    assert result["status"] == "READY"
    assert state["consumed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "new_mode,new_time", [("WEEKDAYS", "07:00"), ("DAILY", "10:00")]
)
async def test_service_restarts_existing_plan_before_waiting_for_changed_schedule(
    monkeypatch, new_mode, new_time
):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, _ = isolate(monkeypatch)
    now = NOW.replace(day=17, hour=9, minute=10)
    pipeline.read_pipeline.return_value = {
        "state": "RUNNING",
        "stage": "WAITING_SOURCES",
        "scheduledFor": now.replace(hour=8, minute=15),
        "startWindowEndsAt": now.replace(hour=10, minute=15),
        "runtimeDeadlineAt": now.replace(hour=10, minute=15),
    }
    stop = asyncio.Event()
    sync = AsyncMock(return_value={"status": "READY"})
    monkeypatch.setattr(mod, "sync_daily_research", sync)

    async def wait(event, seconds):
        event.set()

    await mod.run_daily_research_service(
        object(),
        settings(
            daily_research_schedule_time=new_time, daily_research_schedule_mode=new_mode
        ),
        stop_event=stop,
        now_provider=lambda: now,
        wait=wait,
    )
    sync.assert_awaited_once()
    assert sync.await_args.args[2] == now


@pytest.mark.asyncio
async def test_service_polls_a_dead_pre_model_owner_until_lease_expiry_then_dispatches_once(
    monkeypatch,
):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    started = NOW.replace(hour=7, minute=0)
    expires = started + timedelta(minutes=30)
    current = started + timedelta(minutes=10)
    stop = asyncio.Event()
    delays, attempted = [], []
    monkeypatch.setattr(mod, "now_shanghai", lambda: current)
    pipeline.read_pipeline.return_value = {
        "id": "pipeline",
        "state": "RUNNING",
        "stage": "COLLECTING",
        "scheduledFor": started,
        "startWindowEndsAt": started + timedelta(hours=2),
        "runtimeDeadlineAt": started + timedelta(hours=2),
        "researchRunId": None,
    }

    async def claim(conn, stamp, **kwargs):
        attempted.append(stamp)
        if stamp < expires:
            return None
        return {
            "id": "pipeline",
            "lease_token": "new-owner",
            "stage": "COLLECTING",
            "started_at": started,
            "scheduled_for": started,
            "start_window_ends_at": started + timedelta(hours=2),
            "runtime_deadline_at": started + timedelta(hours=2),
        }

    pipeline.claim_pipeline.side_effect = claim
    pipeline.scheduled_source_batch.side_effect = None
    pipeline.scheduled_source_batch.return_value = {
        "id": "source-batch",
        "run_date": started.date(),
        "status": "READY",
        "completed_at": started,
    }
    mod.load_public_packet.return_value = {"asOf": started.isoformat()}

    async def wait(event, seconds):
        nonlocal current
        delays.append(seconds)
        if seconds > 60:
            event.set()
        else:
            current += timedelta(seconds=seconds)

    await mod.run_daily_research_service(
        object(), settings(), stop_event=stop, now_provider=lambda: current, wait=wait
    )
    assert attempted == [
        started + timedelta(minutes=minute) for minute in range(10, 31)
    ]
    assert delays[:20] == [60] * 20
    mod.run_codex_research.assert_awaited_once()
    assert state["terminal"][-1]["status"] == "READY"
    assert (await mod.sync_daily_research(object(), settings(), current))[
        "status"
    ] == "SKIPPED"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_name,stage,run_id",
    [
        ("FAILED", "COLLECTING", None),
        ("UNCERTAIN", "MODEL_RUNNING", "one"),
        ("RUNNING", "MODEL_RUNNING", "one"),
        ("READY", "PERSISTING", "one"),
    ],
)
async def test_claim_conflicts_with_terminal_or_model_started_owner_never_poll_for_replay(
    monkeypatch, state_name, stage, run_id
):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    pipeline.claim_pipeline.side_effect = None
    pipeline.claim_pipeline.return_value = None
    pipeline.read_pipeline.return_value = {
        "id": "pipeline",
        "state": state_name,
        "stage": stage,
        "researchRunId": run_id,
        "scheduledFor": NOW.replace(hour=7),
        "startWindowEndsAt": NOW.replace(hour=9),
        "runtimeDeadlineAt": NOW + timedelta(hours=2),
    }
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "SKIPPED"
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_first_pipeline_claim_is_rechecked_and_waited_for(monkeypatch):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, _ = isolate(monkeypatch)
    pipeline.claim_pipeline.side_effect = None
    pipeline.claim_pipeline.return_value = None
    pipeline.read_pipeline.side_effect = [
        None,
        {
            "id": "winner",
            "state": "RUNNING",
            "stage": "COLLECTING",
            "researchRunId": None,
            "scheduledFor": NOW.replace(hour=7),
            "startWindowEndsAt": NOW.replace(hour=9),
            "runtimeDeadlineAt": NOW + timedelta(hours=2),
        },
    ]
    assert await mod.sync_daily_research(object(), settings(), NOW) == {
        "status": "WAITING_PIPELINE_OWNER",
        "pipelineId": "winner",
    }
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason", ["consumed", "window_end"])
async def test_waiting_for_pre_model_owner_stops_at_consumed_slot_or_start_window_end(
    monkeypatch, stop_reason
):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    started = NOW.replace(hour=7, minute=0)
    current = started
    stop = asyncio.Event()
    attempts = []
    monkeypatch.setattr(mod, "now_shanghai", lambda: current)
    pipeline.read_pipeline.return_value = {
        "id": "pipeline",
        "state": "RUNNING",
        "stage": "COLLECTING",
        "scheduledFor": started,
        "startWindowEndsAt": started + timedelta(minutes=1),
        "runtimeDeadlineAt": started + timedelta(hours=2),
        "researchRunId": None,
    }
    pipeline.claim_pipeline.side_effect = None
    pipeline.claim_pipeline.return_value = None

    async def archive_expired_owner(*_):
        # This scenario's lease has expired, so the real closure query would
        # persist MISSED. A still-live owner is covered by the service regression.
        pipeline.read_pipeline.return_value["state"] = "MISSED"

    close_window = AsyncMock(side_effect=archive_expired_owner)
    monkeypatch.setattr(pipeline, "finish_source_wait_window", close_window)

    async def wait(event, seconds):
        nonlocal current
        attempts.append(seconds)
        if seconds > 60:
            event.set()
        else:
            current += timedelta(seconds=seconds)
            if stop_reason == "consumed":
                state["consumed"] = True

    await mod.run_daily_research_service(
        object(),
        settings(daily_research_start_window_minutes=1),
        stop_event=stop,
        now_provider=lambda: current,
        wait=wait,
    )
    assert attempts[0] == 60
    assert len(attempts) == 2 and attempts[1] > 60
    pipeline.claim_pipeline.assert_awaited_once()
    assert close_window.await_count == (1 if stop_reason == "window_end" else 0)
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_source_batch_is_reused_without_running_collection_again(
    monkeypatch,
):
    from backend.macro import daily_pipeline_repository as pipeline
    from backend.jobs import alpha_public

    mod, state = isolate(monkeypatch)
    pipeline.scheduled_source_batch.side_effect = None
    pipeline.scheduled_source_batch.return_value = {
        "id": "source-batch",
        "run_date": NOW.date(),
        "status": "READY",
        "completed_at": NOW,
    }
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "READY"
    alpha_public.sync_alpha_public.assert_not_awaited()
    assert state["consumed"]


@pytest.mark.asyncio
async def test_persisted_utc_plan_is_used_in_shanghai_time_when_env_time_changes(
    monkeypatch,
):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    pipeline.claim_pipeline.side_effect = None
    pipeline.claim_pipeline.return_value = {
        "id": "pipeline",
        "lease_token": "lease",
        "stage": "COLLECTING",
        "started_at": NOW,
        "scheduled_for": NOW.replace(hour=7).astimezone(timezone.utc),
        "start_window_ends_at": NOW.replace(hour=9).astimezone(timezone.utc),
        "runtime_deadline_at": (NOW + timedelta(hours=2)).astimezone(timezone.utc),
    }
    claim = AsyncMock(return_value={"run_id": "one"})
    monkeypatch.setattr(mod, "claim_daily_run", claim)
    result = await mod.sync_daily_research(
        object(), settings(daily_research_schedule_time="08:00"), NOW
    )
    assert result["status"] == "READY"
    assert claim.await_args.kwargs["schedule_time"] == "07:00"


@pytest.mark.asyncio
async def test_failed_source_batch_is_terminal_and_never_recollected(monkeypatch):
    from backend.macro import daily_pipeline_repository as pipeline
    from backend.jobs import alpha_public

    mod, state = isolate(monkeypatch)
    pipeline.scheduled_source_batch.side_effect = None
    pipeline.scheduled_source_batch.return_value = {
        "id": "source-batch",
        "run_date": NOW.date(),
        "status": "FAILED",
        "completed_at": NOW,
    }
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "errorClass"
    ] == "SourceCollectionFailed"
    alpha_public.sync_alpha_public.assert_not_awaited()
    assert not state["consumed"]


@pytest.mark.asyncio
async def test_pre_model_cancellation_releases_owned_pipeline_for_safe_recovery(
    monkeypatch,
):
    from backend.macro import daily_pipeline_repository as pipeline
    from backend.jobs import alpha_public

    mod, state = isolate(monkeypatch)
    released = AsyncMock(return_value=True)
    monkeypatch.setattr(
        pipeline, "release_pipeline_before_model", released, raising=False
    )
    alpha_public.sync_alpha_public.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await mod.sync_daily_research(object(), settings(), NOW)
    released.assert_awaited_once()
    assert not state["consumed"]
    assert not any(
        event.get("state") == "UNCERTAIN" for event in state["pipeline_events"]
    )


@pytest.mark.asyncio
async def test_valid_pipeline_can_prepare_input_after_start_window_and_dispatch(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)
    current = NOW.replace(hour=8, minute=55)
    monkeypatch.setattr(mod, "now_shanghai", lambda: current)

    async def prepare(*args, **kwargs):
        nonlocal current
        current = NOW.replace(hour=9, minute=2)
        return {}

    mod.prepare_research_inputs.side_effect = prepare
    assert (await mod.sync_daily_research(object(), settings(), current))[
        "status"
    ] == "READY"
    assert state["consumed"]


@pytest.mark.asyncio
async def test_source_quality_is_checked_again_immediately_before_model_dispatch(
    monkeypatch,
):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    quality = iter([{"allowed": True}, {"allowed": False}])
    monkeypatch.setattr(pipeline, "source_quality", lambda *_: next(quality))
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result == {"status": "FAILED", "errorClass": "SourceQualityRejected"}
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_heartbeat_renews_during_a_single_slow_stage(monkeypatch):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    renewed = asyncio.Event()
    count = 0

    async def heartbeat(*args):
        nonlocal count
        count += 1
        if count == 2:
            renewed.set()
        return True

    renew = AsyncMock(side_effect=heartbeat)
    monkeypatch.setattr(pipeline, "renew_pipeline_lease", renew, raising=False)
    monkeypatch.setattr(mod, "PIPELINE_HEARTBEAT_SECONDS", 0.001, raising=False)

    async def slow(*args, **kwargs):
        await asyncio.wait_for(renewed.wait(), 0.5)
        return {}

    mod.collect_daily_sources.side_effect = slow
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "READY"
    assert renew.await_count >= 2
    assert state["consumed"]


@pytest.mark.asyncio
async def test_heartbeat_ownership_loss_stops_input_before_model_dispatch(monkeypatch):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    renew = AsyncMock(return_value=False)
    monkeypatch.setattr(pipeline, "renew_pipeline_lease", renew, raising=False)
    monkeypatch.setattr(mod, "PIPELINE_HEARTBEAT_SECONDS", 0.001, raising=False)

    async def slow(*args, **kwargs):
        await asyncio.Event().wait()

    mod.collect_daily_sources.side_effect = slow
    result = await asyncio.wait_for(
        mod.sync_daily_research(object(), settings(), NOW), 0.2
    )
    assert result == {"status": "UNCERTAIN", "errorClass": "PipelineOwnershipLost"}
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_timeout_is_enforced_even_when_provider_does_not_enforce_it(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)

    async def slow(*args, **kwargs):
        await asyncio.Event().wait()

    mod.run_codex_research.side_effect = slow
    result = await asyncio.wait_for(
        mod.sync_daily_research(
            object(), settings(daily_research_timeout_seconds=0.01), NOW
        ),
        0.2,
    )
    assert result["status"] == "UNCERTAIN"
    assert result["errorClass"] == "TimeoutError"
    assert state["consumed"]


@pytest.mark.asyncio
async def test_recovery_past_total_runtime_limit_never_collects_or_consumes_model_slot(
    monkeypatch,
):
    from backend.macro import daily_pipeline_repository as pipeline
    from backend.jobs import alpha_public

    mod, state = isolate(monkeypatch)
    pipeline.claim_pipeline.side_effect = None
    pipeline.claim_pipeline.return_value = {
        "id": "pipeline",
        "lease_token": "lease",
        "stage": "COLLECTING",
        "started_at": NOW - timedelta(seconds=7201),
        "scheduled_for": NOW.replace(hour=7),
        "start_window_ends_at": NOW.replace(hour=9),
        "runtime_deadline_at": NOW - timedelta(seconds=1),
    }
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "FAILED"
    assert result["errorClass"] == "PipelineRuntimeExpired"
    assert not state["consumed"]
    alpha_public.sync_alpha_public.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_terminal_and_pipeline_terminal_commit_together(monkeypatch):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "READY"
    assert state["terminal_in_transactions"] == [True]
    assert any(
        call.kwargs.get("state") == "READY"
        for call in pipeline.advance_pipeline.await_args_list
    )


@pytest.mark.asyncio
async def test_model_started_before_window_end_can_finish_after_nine(monkeypatch):
    mod, state = isolate(monkeypatch)
    current = NOW.replace(hour=8, minute=59)
    monkeypatch.setattr(mod, "now_shanghai", lambda: current)

    async def model(*args, **kwargs):
        nonlocal current
        current = NOW.replace(hour=9, minute=10)
        return {"summary": "public", "sources": []}

    mod.run_codex_research.side_effect = model
    assert (await mod.sync_daily_research(object(), settings(), current))[
        "status"
    ] == "READY"
    assert state["terminal"][0]["status"] == "READY"


@pytest.mark.asyncio
async def test_expired_owner_cannot_consume_the_model_slot(monkeypatch):
    from backend.macro import daily_pipeline_repository as pipeline

    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(pipeline, "advance_pipeline", AsyncMock(return_value=False))
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "UNCERTAIN"
    assert state["consumed"] is False
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_waits_for_concurrent_public_batch_before_consuming_model_slot(
    monkeypatch,
):
    from backend.jobs import alpha_public

    mod, state = isolate(monkeypatch)

    class Conn:
        async def fetchrow(self, *args):
            return {"status": "CLAIMED", "claimed_at": NOW}

    @asynccontextmanager
    async def connection(pool):
        yield Conn()

    monkeypatch.setattr(mod, "system_connection", connection)
    monkeypatch.setattr(
        alpha_public, "sync_alpha_public", AsyncMock(return_value={"status": "SKIPPED"})
    )
    from backend.macro import daily_pipeline_repository

    monkeypatch.setattr(
        daily_pipeline_repository,
        "scheduled_source_batch",
        AsyncMock(
            return_value={
                "id": "batch",
                "run_date": NOW.date(),
                "status": "CLAIMED",
                "claimed_at": NOW - timedelta(minutes=20),
                "completed_at": None,
            }
        ),
    )
    result = await mod.sync_daily_research(
        object(), settings(alpha_public_enabled=True), NOW
    )
    assert result["status"] == "WAITING_PUBLIC_DATA"
    assert state["consumed"] is False
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_start_catches_up_once_on_weekday_and_restart_does_not_dispatch(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)
    first = await mod.sync_daily_research(object(), settings(), NOW)
    second = await mod.sync_daily_research(
        object(), settings(), NOW + timedelta(minutes=30)
    )
    assert first["status"] == "READY" and second["status"] == "SKIPPED"
    assert state["consumed"]
    mod.run_codex_research.assert_awaited_once()
    assert state["terminal"][0]["status"] == "READY"


@pytest.mark.asyncio
async def test_remote_codex_keeps_one_daily_slot_without_local_auth(monkeypatch):
    from backend.integrations import codex_client

    mod, state = isolate(monkeypatch)
    preflight = AsyncMock(
        return_value={"executable": "remote", "version": "codex-cli fixture"}
    )
    research = AsyncMock(return_value={"summary": "public", "sources": []})
    monkeypatch.setattr(codex_client, "preflight", preflight)
    monkeypatch.setattr(codex_client, "research", research)
    configured = settings(codex_runner_url="http://codex-runner:8090")
    assert (await mod.sync_daily_research(object(), configured, NOW))[
        "status"
    ] == "READY"
    assert (await mod.sync_daily_research(object(), configured, NOW))[
        "status"
    ] == "SKIPPED"
    research.assert_awaited_once()
    mod.preflight_codex.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()
    assert state["terminal"][0]["status"] == "READY"


@pytest.mark.asyncio
async def test_remote_dispatch_timeout_is_uncertain_and_never_retried(monkeypatch):
    from backend.integrations import codex_client

    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(
        codex_client,
        "preflight",
        AsyncMock(return_value={"version": "codex-cli fixture"}),
    )
    research = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(codex_client, "research", research)
    configured = settings(codex_runner_url="http://codex-runner:8090")
    assert (await mod.sync_daily_research(object(), configured, NOW))[
        "status"
    ] == "UNCERTAIN"
    assert (await mod.sync_daily_research(object(), configured, NOW))[
        "status"
    ] == "SKIPPED"
    assert state["terminal"][0]["status"] == "UNCERTAIN"
    research.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_or_before_0700_never_collects_or_dispatches(monkeypatch):
    mod, _ = isolate(monkeypatch)
    assert (
        await mod.sync_daily_research(
            object(), settings(daily_research_enabled=False), NOW
        )
    )["status"] == "DISABLED"
    assert (
        await mod.sync_daily_research(
            object(), settings(), NOW.replace(hour=6, minute=59)
        )
    )["status"] == "NOT_DUE"
    mod.collect_daily_sources.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception,status",
    [
        (TimeoutError, "UNCERTAIN"),
        (asyncio.CancelledError, "UNCERTAIN"),
        (RuntimeError, "FAILED"),
    ],
)
async def test_failed_or_cancelled_model_keeps_consumed_slot(
    monkeypatch, exception, status
):
    mod, state = isolate(monkeypatch)
    mod.run_codex_research.side_effect = exception()
    if exception is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            await mod.sync_daily_research(object(), settings(), NOW)
    else:
        assert (await mod.sync_daily_research(object(), settings(), NOW))[
            "status"
        ] == status
    assert state["terminal"][0]["status"] == status
    from backend.macro import daily_pipeline_repository as pipeline

    assert pipeline.advance_pipeline.await_args.kwargs["stage"] == "MODEL_RUNNING"
    assert pipeline.advance_pipeline.await_args.kwargs["state"] == status
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "SKIPPED"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_cli_unavailable_is_terminal_and_does_not_fallback_or_retry(monkeypatch):
    from backend.integrations.codex_research import CodexUnavailableError

    mod, state = isolate(monkeypatch)
    mod.preflight_codex.side_effect = CodexUnavailableError()
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "FAILED"
    assert state["consumed"] is False
    mod.run_codex_research.assert_not_awaited()
    assert state["terminal"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_public_model_result_is_audited_before_validation_without_retry(
    monkeypatch, invalid
):
    mod, state = isolate(monkeypatch)
    records = []

    def audit(settings, run_id, **record):
        records.append(record)
        return False  # an unavailable diagnostic directory is nonfatal

    def validate(raw, *args, **kwargs):
        assert records[-1]["stage"] == "SOURCES_CHECKED"
        if invalid:
            raise ValueError("unknown public direction key")
        return raw

    monkeypatch.setattr(mod, "write_research_audit", audit, raising=False)
    monkeypatch.setattr(mod, "validate_report", validate)
    outcome = await mod.sync_daily_research(object(), settings(), NOW)
    assert outcome["status"] == ("FAILED" if invalid else "READY")
    assert [r["stage"] for r in records] == [
        "MODEL_RETURNED",
        "SOURCES_CHECKED",
        "VALIDATION_FAILED" if invalid else "READY",
    ]
    assert all(r["raw"] == {"summary": "public", "sources": []} for r in records)
    assert records[0].get("source_checks") is None
    assert records[-1]["source_checks"] == {"summary": "public", "sources": []}
    assert isinstance(records[-1].get("error"), ValueError) is invalid
    assert state["terminal"][0]["error_class"] == ("ValueError" if invalid else None)
    assert state["pipeline_events"][-1]["stage"] == (
        "VERIFYING_OUTPUT" if invalid else "PERSISTING"
    )
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "SKIPPED"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_collection_crossing_midnight_does_not_dispatch_before_next_0700(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(
        mod, "now_shanghai", lambda: NOW.replace(day=18, hour=0, minute=1)
    )
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "MISSED"
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "catchup,expected", [(True, NOW), (False, NOW.replace(day=19, hour=7, minute=0))]
)
async def test_service_catchup_policy_and_next_shanghai_day(
    monkeypatch, catchup, expected
):
    mod, _ = isolate(monkeypatch)
    stamp, delays, calls = NOW.astimezone(timezone.utc), [], []
    stop = asyncio.Event()

    def clock():
        return stamp

    async def wait(event, seconds):
        nonlocal stamp
        delays.append(seconds)
        stamp += timedelta(seconds=seconds)
        if len(delays) == 2:
            event.set()

    async def sync(*args):
        calls.append(args[2])
        return {"status": "SKIPPED"}

    monkeypatch.setattr(mod, "sync_daily_research", sync)
    await mod.run_daily_research_service(
        object(),
        settings(
            daily_research_catch_up=catchup, daily_research_schedule_mode="WEEKDAYS"
        ),
        stop_event=stop,
        now_provider=clock,
        wait=wait,
    )
    assert calls[0] == expected
    assert all(seconds > 0 for seconds in delays)


@pytest.mark.asyncio
async def test_service_waits_one_minute_for_inflight_public_cache_then_uses_same_day(
    monkeypatch,
):
    mod, _ = isolate(monkeypatch)
    stamp = NOW
    stop = asyncio.Event()
    sync = AsyncMock(
        side_effect=[{"status": "WAITING_PUBLIC_DATA"}, {"status": "READY"}]
    )
    delays = []

    async def wait(event, seconds):
        nonlocal stamp
        delays.append(seconds)
        stamp += timedelta(seconds=seconds)
        if len(delays) == 2:
            event.set()

    monkeypatch.setattr(mod, "sync_daily_research", sync)
    await mod.run_daily_research_service(
        object(), settings(), stop_event=stop, now_provider=lambda: stamp, wait=wait
    )
    assert [call.args[2] for call in sync.await_args_list] == [
        NOW,
        NOW + timedelta(minutes=1),
    ]
    assert delays[0] == 60
    assert delays[1] > 3600
