"""Queue cancellation/expiry never confer authority to run or release a model."""

from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.errors import ForbiddenError, NotFoundError, VersionConflictError
from backend.macro import research_debug_repository as repo
from tests.unit.test_research_debug_repository import NOW, row


@pytest.mark.parametrize("elapsed", [5, 36])
def test_never_started_queue_has_its_own_deadline_before_running_window(elapsed):
    original = row(status="QUEUED", started_at=None)
    dto = repo.request_dto(original, NOW + timedelta(minutes=elapsed))
    assert dto["status"] == "EXPIRED" and dto["errorClass"] == "QueueWaitExpired"
    assert dto["queueExpiresAt"] == NOW + timedelta(minutes=5)
    assert dto["canCancel"] is False
    assert original["status"] == "QUEUED" and original["started_at"] is None


def test_recently_claimed_request_uses_started_clock_not_original_queue_deadline():
    original = row(requested_at=NOW - timedelta(minutes=4), started_at=NOW)
    dto = repo.request_dto(original, NOW + timedelta(minutes=6))
    assert dto["status"] == "RUNNING" and dto["errorClass"] is None
    assert dto["queueWaitSeconds"] == 240
    assert dto["canCancel"] is False


def test_running_timeout_does_not_grant_cancellation_or_new_execution_authority():
    original = row()
    dto = repo.request_dto(original, NOW + timedelta(minutes=35))
    assert (
        dto["status"] == "UNCERTAIN" and dto["errorClass"] == "ExecutionWindowExpired"
    )
    assert dto["canCancel"] is False and original["status"] == "RUNNING"


def test_future_stage_and_unknown_block_metadata_are_never_projected_as_execution_progress():
    original = row(
        execution_stage="MODEL_RUNNING",
        stage_updated_at=NOW + timedelta(seconds=1),
        queue_observation={"blockedReasonCode": "PRIVATE_INVALID", "canCancel": True},
    )
    dto = repo.request_dto(original, NOW)
    assert (
        dto["executionStage"]
        is dto["stageUpdatedAt"]
        is dto["blockedReasonCode"]
        is None
    )
    assert dto["canCancel"] is False


def test_cancelled_queue_has_no_batch_run_or_running_stage_and_keeps_identity():
    original = row(
        status="CANCELLED",
        started_at=None,
        completed_at=NOW,
        error_class="CancelledBeforeStart",
    )
    dto = repo.request_dto(original, NOW)
    assert dto["id"] == str(original["id"]) and dto["status"] == "CANCELLED"
    assert dto["batchId"] is dto["reportRunId"] is dto["executionStage"] is None
    assert dto["canCancel"] is False


@pytest.mark.parametrize(
    "case", ["valid", "unknown", "future", "before_guard", "other_block"]
)
def test_local_exit_evidence_is_visible_only_for_a_current_matching_guard_observation(
    case,
):
    checked = NOW + timedelta(seconds=1) if case == "future" else NOW
    if case == "before_guard":
        checked = NOW - timedelta(minutes=3)
    original = row(
        status="QUEUED",
        started_at=None,
        queue_observation={
            "blockedReasonCode": "WAITING_WORKER"
            if case == "other_block"
            else "MODEL_GUARD_ACTIVE",
            "blockedSince": NOW - timedelta(minutes=2),
            "modelExitEvidenceState": "UNTRUSTED"
            if case == "unknown"
            else "LOCAL_RUNNING",
            "modelExitEvidenceCheckedAt": checked,
        },
    )
    dto = repo.request_dto(original, NOW)
    assert dto["modelExitEvidenceState"] == (
        "LOCAL_RUNNING" if case == "valid" else None
    )
    assert dto["modelExitEvidenceCheckedAt"] == (NOW if case == "valid" else None)
    assert set(dto).isdisjoint({"canRelease", "ownerToken", "blockingRunId"})


@pytest.mark.asyncio
async def test_actor_lookup_persists_expiry_and_exposes_only_scoped_safe_block_reason():
    original = row(status="QUEUED", started_at=None)
    conn = AsyncMock()
    conn.fetchrow.return_value = original
    conn.fetchval.side_effect = [
        0,
        {
            "blockedReasonCode": "MODEL_GUARD_ACTIVE",
            "blockedSince": NOW - timedelta(hours=2),
            "queuePosition": 1,
            "canCancel": True,
        },
    ]
    dto = await repo.read_refresh(
        conn, original["tenant_id"], original["user_id"], original["id"], NOW
    )
    expiry, observed = conn.fetchval.await_args_list
    assert "expire_queued_research_refresh" in expiry.args[0]
    assert expiry.args[1:] == (original["tenant_id"], original["user_id"], NOW)
    assert "research_refresh_queue_observation" in observed.args[0]
    assert observed.args[1:] == (
        original["tenant_id"],
        original["user_id"],
        original["id"],
        NOW,
    )
    assert dto["blockedReasonCode"] == "MODEL_GUARD_ACTIVE" and dto["canCancel"] is True
    assert dto["queuePosition"] == 1
    assert set(dto).isdisjoint({"blockingRunId", "blockingUserId", "ownerToken"})
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_requires_a_current_owner_not_an_admin_or_old_token():
    conn = AsyncMock()
    conn.fetchval.return_value = False
    with pytest.raises(ForbiddenError):
        await repo.cancel_refresh(conn, uuid4(), uuid4(), uuid4(), NOW)
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["RUNNING", "READY", "EXPIRED"])
async def test_cancellation_losing_claim_or_expiry_race_cannot_change_running_or_terminal_request(
    state,
):
    original = row(
        status=state,
        started_at=NOW if state != "EXPIRED" else None,
        completed_at=None if state == "RUNNING" else NOW,
    )
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = original
    with pytest.raises(VersionConflictError):
        await repo.cancel_refresh(
            conn, original["tenant_id"], original["user_id"], original["id"], NOW
        )
    assert "cancel_queued_research_refresh" in conn.fetchrow.await_args.args[0]
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_replay_returns_original_audit_identity_without_deleting_request():
    original = row(
        status="CANCELLED",
        started_at=None,
        completed_at=NOW,
        error_class="CancelledBeforeStart",
    )
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = original
    result = await repo.cancel_refresh(
        conn, original["tenant_id"], original["user_id"], original["id"], NOW
    )
    assert result["id"] == str(original["id"]) and result["status"] == "CANCELLED"
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_uses_database_observation_without_trusting_a_future_terminal_stamp():
    original = row(
        status="CANCELLED",
        started_at=None,
        completed_at=NOW + timedelta(milliseconds=1),
        error_class="CancelledBeforeStart",
    )
    assert repo.request_dto(original, NOW)["status"] == "QUEUED"
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {
        **original,
        "observed_at": NOW + timedelta(milliseconds=2),
    }
    result = await repo.cancel_refresh(
        conn, original["tenant_id"], original["user_id"], original["id"], NOW
    )
    assert result["status"] == "CANCELLED"
    assert result["completedAt"] == original["completed_at"]
    assert "clock_timestamp() AS observed_at" in conn.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_actor_read_uses_trusted_database_clock_after_expiry_mutation():
    original = row(
        status="EXPIRED",
        started_at=None,
        requested_at=NOW - timedelta(minutes=5),
        completed_at=NOW + timedelta(milliseconds=1),
        error_class="QueueWaitExpired",
        observed_at=NOW + timedelta(milliseconds=2),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = original
    conn.fetchval.return_value = 1
    result = await repo.read_refresh(
        conn, original["tenant_id"], original["user_id"], original["id"], NOW
    )
    assert (
        result["status"] == "EXPIRED"
        and result["completedAt"] == original["completed_at"]
    )
    assert conn.fetchval.await_count == 1
    assert "clock_timestamp() AS observed_at" in conn.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_cancel_missing_or_other_actor_request_is_not_found():
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = None
    with pytest.raises(NotFoundError):
        await repo.cancel_refresh(conn, uuid4(), uuid4(), uuid4(), NOW)


@pytest.mark.asyncio
async def test_owner_revoked_between_lookup_and_cas_fails_closed_as_forbidden():
    from asyncpg import InsufficientPrivilegeError

    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.side_effect = InsufficientPrivilegeError()
    with pytest.raises(ForbiddenError):
        await repo.cancel_refresh(conn, uuid4(), uuid4(), uuid4(), NOW)
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_command_replay_keeps_original_mapping_and_never_enqueues_a_replacement():
    original = row(
        status="EXPIRED",
        requested_at=NOW - timedelta(minutes=6),
        started_at=None,
        completed_at=NOW,
        error_class="QueueWaitExpired",
    )
    conn = AsyncMock()

    @asynccontextmanager
    async def transaction():
        yield

    conn.transaction = transaction
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.return_value = original
    result = await repo.enqueue_refresh(
        conn, original["tenant_id"], original["user_id"], original["command_key"], NOW
    )
    assert result["status"] == "EXPIRED" and result["id"] == str(original["id"])
    assert conn.fetchrow.await_count == 1
    assert "daily_research_refresh_commands" in conn.fetchrow.await_args.args[0]
    assert not any(
        "INSERT" in args.args[0] or "DELETE" in args.args[0]
        for args in conn.execute.await_args_list
    )


@pytest.mark.asyncio
async def test_queue_expires_before_busy_guard_check_and_never_releases_existing_guard(
    monkeypatch,
):
    calls = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def execute(self, sql, *args):
            calls.append((sql, args))

    async def occupied(conn):
        assert calls and "status='EXPIRED'" in calls[0][0]
        raise repo.ResearchModelBusy()

    monkeypatch.setattr(repo, "lock_model_dispatch", occupied)
    assert await repo.claim_refresh(Conn(), NOW, allowed_modes=["MANUAL"]) is None
    sql, args = calls[0]
    assert "status='QUEUED'" in sql and "started_at IS NULL" in sql
    assert NOW - timedelta(minutes=5) in args
    assert "local_research_recovery_audit" in sql
    assert "QueueInvalidatedByMaintenance" in sql and "QueueWaitExpired" in sql
    assert all("daily_research_model_guards" not in query for query, _ in calls)


@pytest.mark.asyncio
async def test_queue_claim_compare_and_swap_rejects_cancelled_expired_or_already_started_rows(
    monkeypatch,
):
    conn = AsyncMock()

    @asynccontextmanager
    async def transaction():
        yield

    conn.transaction = transaction
    conn.fetchval.return_value = False
    conn.fetchrow.return_value = None
    monkeypatch.setattr(repo, "lock_model_dispatch", AsyncMock())
    assert await repo.claim_refresh(conn, NOW, allowed_modes=["MANUAL"]) is None
    sql, *args = conn.fetchrow.await_args.args
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "q.status='QUEUED'" in sql and "q.started_at IS NULL" in sql
    assert NOW - timedelta(minutes=5) in args
    assert "requested_at+interval '5 minutes'>GREATEST($1,clock_timestamp())" in sql
    assert sql.count("local_research_recovery_audit") == 2


@pytest.mark.asyncio
async def test_stage_updates_are_running_only_and_monotonic_without_model_guard_changes():
    conn = AsyncMock()
    conn.fetchval.return_value = True
    assert await repo.set_refresh_stage(conn, uuid4(), NOW, "MODEL_RUNNING") is True
    sql = conn.fetchval.await_args.args[0]
    assert "status='RUNNING'" in sql and "stage_updated_at" in sql
    assert "daily_research_model_guards" not in sql
    with pytest.raises(ValueError):
        await repo.set_refresh_stage(conn, uuid4(), NOW, "CANCELLED")


@pytest.mark.asyncio
async def test_waiting_for_model_slot_is_a_distinct_phase_before_actual_model_running():
    conn = AsyncMock()
    conn.fetchval.return_value = uuid4()
    assert (
        await repo.set_refresh_stage(conn, uuid4(), NOW, "WAITING_MODEL_SLOT") is True
    )
    stages = conn.fetchval.await_args.args[-1]
    assert (
        stages.index("COLLECTING")
        < stages.index("WAITING_MODEL_SLOT")
        < stages.index("MODEL_RUNNING")
    )
