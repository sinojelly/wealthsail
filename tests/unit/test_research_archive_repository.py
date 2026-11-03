"""Durable archive queues freeze original evidence and reveal only safe status."""

from datetime import timedelta
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.macro import research_acceptance_repository as repo
from tests.unit.test_research_acceptance_repository import NOW, RUN, outcome


@pytest.mark.asyncio
async def test_enqueue_freezes_original_descriptors_and_safe_receipt_once():
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "run_id": RUN,
        "status": "AUDIT_PENDING",
        "attempts": 0,
        "created_at": NOW,
        "updated_at": NOW,
        "next_attempt_at": NOW,
        "last_error_class": None,
    }
    result = await repo.enqueue_research_archive(
        conn,
        RUN,
        {"PRIVATE_PACKET": True},
        None,
        None,
        NOW,
        outcome(validatorRevision="a" * 64, revisionStatus="AVAILABLE"),
        raw_metadata={
            "sha256": "b" * 64,
            "byteLength": 3_000_000,
            "omissionReason": "SIZE_LIMIT",
        },
        recorded_at=NOW,
    )
    args = conn.execute.await_args.args
    bundle = json.loads(args[3])
    assert bundle["artifacts"]["raw_output"]["omissionReason"] == "SIZE_LIMIT"
    assert bundle["artifacts"]["raw_output"]["sha256"] == "b" * 64
    assert bundle["artifacts"]["source_checks"]["omissionReason"] == "MISSING"
    assert bundle["validatedAt"] == NOW.isoformat()
    assert bundle["outcome"]["validatorRevision"] == "a" * 64
    assert "ON CONFLICT(run_id) DO NOTHING" in args[0]
    assert result["archiveState"] == "AUDIT_PENDING"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.asyncio
async def test_claim_final_expired_attempt_is_marked_unrecoverable_before_skip_locked():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    assert await repo.claim_research_archive(conn, NOW) is None
    sql = conn.execute.await_args.args[0]
    assert "UNRECOVERABLE" in sql and "lease_expires_at" in sql
    assert "SKIP LOCKED LIMIT 20" in sql
    claim_sql = conn.fetchrow.await_args.args[0]
    assert "SKIP LOCKED" in claim_sql and "attempts<8" in claim_sql


@pytest.mark.asyncio
async def test_persist_does_not_refreeze_missing_descriptors_or_revalidate(monkeypatch):
    descriptors = {
        "packet": repo.freeze_artifact({}, repo.PACKET_LIMIT),
        "raw_output": {
            "content": None,
            "sha256": "c" * 64,
            "byteLength": 3_000_000,
            "omissionReason": "SIZE_LIMIT",
        },
        "source_checks": repo.freeze_artifact({}, repo.CHECKS_LIMIT),
    }
    claim = {
        "run_id": RUN,
        "lease_token": uuid4(),
        "attempts": 1,
        "frozen_bundle": {
            "validatedAt": NOW.isoformat(),
            "recordedAt": NOW.isoformat(),
            "artifacts": descriptors,
            "outcome": outcome(),
        },
    }
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"run_id": RUN},
        {"run_id": RUN, "validated_at": NOW, **descriptors},
    ]
    save = AsyncMock(return_value={"id": str(uuid4())})
    finish = AsyncMock(return_value={"status": "ARCHIVED"})
    monkeypatch.setattr(repo, "save_validation_outcome", save)
    monkeypatch.setattr(repo, "finish_research_archive", finish)

    def forbidden(*args, **kwargs):
        raise AssertionError("recovery must write the exact frozen descriptors")

    monkeypatch.setattr(repo, "freeze_artifact", forbidden)
    monkeypatch.setattr(
        "backend.macro.research_acceptance.validate_report_outcome", forbidden
    )
    await repo.persist_research_archive(conn, claim, NOW)
    inserted = conn.execute.await_args.args
    assert json.loads(inserted[4]) == descriptors["raw_output"]
    assert save.await_args.kwargs["recorded_at"] == NOW
    assert finish.await_args.kwargs["status"] == "ARCHIVED"


@pytest.mark.asyncio
async def test_finish_fences_stale_lease_and_exhausts_attempts():
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "status": "UNRECOVERABLE",
        "attempts": 8,
        "next_attempt_at": NOW,
    }
    result = await repo.finish_research_archive(
        conn,
        {"run_id": RUN, "lease_token": uuid4(), "attempts": 8},
        NOW,
        status="AUDIT_PENDING",
        error_class="ConnectionError",
    )
    args = conn.fetchrow.await_args.args
    assert "lease_token=$2" in args[0] and "ARCHIVING" in args[0]
    assert result["status"] == "UNRECOVERABLE"
    conn.fetchrow.return_value = None
    assert (
        await repo.finish_research_archive(
            conn,
            {"run_id": RUN, "lease_token": uuid4(), "attempts": 1},
            NOW + timedelta(seconds=1),
            status="AUDIT_PENDING",
            error_class="PRIVATE password",
        )
        is None
    )
