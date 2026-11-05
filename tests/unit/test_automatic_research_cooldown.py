"""Automatic model dispatch pauses after any durable research dispatch."""

from datetime import timedelta, timezone
import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.macro import daily_repository as repository
from tests.unit.test_daily_research_job import NOW, isolate, settings
from tests.unit.test_daily_repository import run


def blocked(stamp=NOW):
    return {
        "active": True,
        "hours": 23,
        "lastAiDispatchAt": stamp,
        "automaticEligibleAt": stamp + timedelta(hours=23),
    }


@pytest.mark.parametrize(
    "age,active", [(0, True), (22.999, True), (23, False), (24, False), (-1, True)]
)
def test_exact_23_hour_boundary_and_uncertain_future_clock_fail_closed(age, active):
    last = NOW - timedelta(hours=age)
    value = repository.automatic_research_cooldown(last, NOW.astimezone(timezone.utc))
    assert value == {
        "active": active,
        "hours": 23,
        "lastAiDispatchAt": last,
        "automaticEligibleAt": last + timedelta(hours=23),
    }


def test_pre_dispatch_failure_has_no_cost_barrier_and_no_cooldown():
    assert repository.automatic_research_cooldown(None, NOW) == {
        "active": False,
        "hours": 23,
        "lastAiDispatchAt": None,
        "automaticEligibleAt": None,
    }


@pytest.mark.asyncio
async def test_scheduler_skips_before_any_collection_and_consumes_no_slot(monkeypatch):
    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(
        mod,
        "read_automatic_research_cooldown",
        AsyncMock(return_value=blocked()),
        raising=False,
    )
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert json.loads(json.dumps(result)) == {
        "status": "COOLDOWN",
        "errorClass": "ResearchCooldownActive",
        "automaticEligibleAt": (NOW + timedelta(hours=23)).isoformat(),
    }
    assert not state["consumed"] and not state["terminal"]
    mod.collect_daily_sources.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_transaction_rechecks_manual_dispatch_during_collection(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)
    observations = []

    async def check(conn, now):
        observations.append(state.get("in_transaction", False))
        return blocked() if observations[-1] else {"active": False}

    monkeypatch.setattr(mod, "read_automatic_research_cooldown", check, raising=False)
    result = await mod._execute_daily_research(
        object(),
        settings(),
        NOW,
        packet={"asOf": NOW.isoformat()},
    )
    assert result["status"] == "COOLDOWN"
    assert observations == [False, True]
    mod.lock_model_dispatch.assert_awaited_once()
    assert not state["consumed"] and not state["terminal"]
    mod.create_model_guard.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_model_pipeline_cooldown_finishes_without_failed_ai_result(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)
    values = iter([{"active": False}, {"active": False}, blocked()])
    monkeypatch.setattr(
        mod,
        "read_automatic_research_cooldown",
        AsyncMock(side_effect=lambda *_: next(values)),
        raising=False,
    )
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "COOLDOWN"
    assert state["pipeline_events"][-1]["state"] == "MISSED"
    assert state["pipeline_events"][-1]["error_class"] == "ResearchCooldownActive"
    assert not any(row["stage"] == "MODEL_RUNNING" for row in state["pipeline_events"])
    assert not state["consumed"] and not state["terminal"]
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorized_manual_research_keeps_explicit_opt_in_and_bypasses_automatic_cooldown(
    monkeypatch,
):
    from backend.macro import alpha_repository, daily_pipeline_repository

    mod, _ = isolate(monkeypatch)
    batch = uuid4()
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value={"batchId": str(batch)}),
    )
    monkeypatch.setattr(
        daily_pipeline_repository, "source_quality", lambda *_: {"allowed": True}
    )
    read = AsyncMock(return_value=blocked())
    monkeypatch.setattr(mod, "read_automatic_research_cooldown", read, raising=False)
    result = await mod._execute_daily_research(
        object(),
        settings(daily_research_manual_enabled=True),
        NOW,
        manual_request_id=uuid4(),
        source_batch_id=batch,
        packet={"asOf": NOW.isoformat()},
    )
    assert result["status"] == "READY"
    read.assert_not_awaited()
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_daily_projection_shows_safe_cooldown_without_any_new_query_or_identity_leak():
    latest = run(
        status="READY",
        recorded_at=NOW,
        payload={"summary": "retained"},
        dispatched_at=NOW - timedelta(hours=2),
        run_mode="MANUAL",
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = latest
    value = await repository.read_daily_research(conn, NOW)
    assert value["automaticResearchCooldown"] == blocked(NOW - timedelta(hours=2))
    assert value["state"] == "READY" and value["report"]["summary"] == "retained"
    assert (
        value["nextRunAt"] >= value["automaticResearchCooldown"]["automaticEligibleAt"]
    )
    assert set(value["automaticResearchCooldown"]) == {
        "active",
        "hours",
        "lastAiDispatchAt",
        "automaticEligibleAt",
    }


@pytest.mark.asyncio
async def test_cooldown_lookup_failure_never_dispatches_or_consumes_a_slot(monkeypatch):
    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(
        mod,
        "read_automatic_research_cooldown",
        AsyncMock(side_effect=OSError("lookup unavailable")),
    )
    with pytest.raises(OSError):
        await mod._execute_daily_research(
            object(), settings(), NOW, packet={"asOf": NOW.isoformat()}
        )
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_skips_occurrence_without_automatic_retry_or_extending_window(
    monkeypatch,
):
    mod, _ = isolate(monkeypatch)
    stop = asyncio.Event()
    delays = []
    sync = AsyncMock(return_value={"status": "COOLDOWN"})

    async def wait(event, seconds):
        delays.append(seconds)
        event.set()

    monkeypatch.setattr(mod, "sync_daily_research", sync)
    await mod.run_daily_research_service(
        object(), settings(), stop_event=stop, now_provider=lambda: NOW, wait=wait
    )
    sync.assert_awaited_once()
    assert delays == [(NOW.replace(hour=7) + timedelta(days=1) - NOW).total_seconds()]
    mod.run_codex_research.assert_not_awaited()
