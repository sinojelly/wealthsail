"""Read-only pipeline status follows frozen cutoffs and the model cost barrier."""

from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.macro.daily_pipeline_repository import dto


NOW = datetime(2026, 10, 31, 7, tzinfo=ZoneInfo("Asia/Shanghai"))


def pipeline_row(stage, *, consumed=False, lease_until=None):
    return {
        "id": uuid4(),
        "state": "RUNNING",
        "stage": stage,
        "scheduled_for": NOW,
        "started_at": NOW,
        "updated_at": NOW,
        "lease_until": lease_until or NOW + timedelta(minutes=30),
        "start_window_ends_at": NOW + timedelta(hours=2),
        "runtime_deadline_at": NOW + timedelta(hours=3),
        "scheduled_model_consumed": consumed,
    }


@pytest.mark.parametrize("stage", ["COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"])
def test_expired_pre_model_status_projects_missed_at_frozen_window_end(stage):
    row = pipeline_row(stage)
    result = dto(row, NOW + timedelta(hours=2))
    assert result["state"] == "MISSED"
    assert result["errorClass"] == "SourceWaitWindowExpired"
    assert result["startWindowEndsAt"] == NOW + timedelta(hours=2)
    assert result["runtimeDeadlineAt"] == NOW + timedelta(hours=3)
    assert row["state"] == "RUNNING"  # Reading does not mutate persisted state.


@pytest.mark.parametrize("stage", ["COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"])
def test_live_owner_remains_running_after_start_window_closes(stage):
    row = pipeline_row(stage, lease_until=NOW + timedelta(hours=2, minutes=1))
    assert dto(row, NOW + timedelta(hours=2))["state"] == "RUNNING"


@pytest.mark.parametrize("guard", ["scheduled_model_consumed", "research_run_id"])
def test_model_barrier_never_projects_missed_even_without_pipeline_foreign_key(guard):
    row = pipeline_row("WAITING_SOURCES")
    row[guard] = uuid4() if guard == "research_run_id" else True
    result = dto(row, NOW + timedelta(hours=2))
    assert result["state"] == "UNCERTAIN"
    assert result["errorClass"] == "PipelineLeaseExpired"


def test_pending_source_wait_remains_waiting_within_original_window():
    row = pipeline_row("WAITING_SOURCES")
    assert dto(row, NOW + timedelta(minutes=90))["state"] == "RUNNING"


def test_terminal_pipeline_is_not_rewritten_after_the_frozen_window():
    row = pipeline_row("PERSISTING")
    row.update(state="READY", finished_at=NOW + timedelta(minutes=20))
    assert dto(row, NOW + timedelta(hours=2))["state"] == "READY"


@pytest.mark.parametrize("stage", ["COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"])
def test_expired_previous_day_pre_model_status_is_missed_before_window_end(stage):
    started = NOW.replace(hour=23, minute=59)
    row = pipeline_row(stage)
    row.update(
        slot_date=started.date(),
        scheduled_for=started,
        started_at=started,
        updated_at=started,
        lease_until=started + timedelta(minutes=5),
        start_window_ends_at=started + timedelta(hours=2),
        runtime_deadline_at=started + timedelta(hours=2),
    )
    result = dto(row, started + timedelta(minutes=11))
    assert result["state"] == "MISSED"
    assert result["errorClass"] == "PipelineDayExpired"


@pytest.mark.parametrize("stage", ["COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"])
def test_previous_day_live_owner_is_observed_until_its_lease_expires(stage):
    started = NOW.replace(hour=23, minute=59)
    row = pipeline_row(stage)
    row.update(
        slot_date=started.date(),
        scheduled_for=started,
        started_at=started,
        lease_until=started + timedelta(minutes=30),
        start_window_ends_at=started + timedelta(hours=2),
    )
    assert dto(row, started + timedelta(minutes=11))["state"] == "RUNNING"


@pytest.mark.parametrize("guard", ["scheduled_model_consumed", "research_run_id"])
def test_previous_day_model_cost_barrier_remains_uncertain_instead_of_missed(guard):
    started = NOW.replace(hour=23, minute=59)
    row = pipeline_row("WAITING_SOURCES")
    row.update(
        slot_date=started.date(),
        scheduled_for=started,
        started_at=started,
        lease_until=started + timedelta(minutes=5),
        start_window_ends_at=started + timedelta(hours=2),
    )
    row[guard] = uuid4() if guard == "research_run_id" else True
    result = dto(row, started + timedelta(minutes=11))
    assert result["state"] == "UNCERTAIN"
    assert result["errorClass"] == "PipelineLeaseExpired"
