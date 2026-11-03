"""Replay failures are durably accounted for before the API raises a safe error."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from backend.errors import DataUnavailableError, RateLimitedError
from backend.macro import research_acceptance_repository as repo
from tests.unit.test_research_acceptance_repository import NOW, RUN


@pytest.mark.asyncio
async def test_replay_budget_rejection_does_not_load_artifacts_or_start_child(
    monkeypatch,
):
    conn = AsyncMock()
    conn.transaction = Mock(return_value=AsyncMock())
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = {"status": "ACTOR_LIMIT"}
    read = AsyncMock()
    monkeypatch.setattr(repo, "read_artifacts_admin", read)
    result = await repo.replay_validation(conn, RUN, uuid4(), uuid4(), uuid4(), NOW)
    assert isinstance(result, repo.ReplayFailure)
    assert isinstance(result.error, RateLimitedError)
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_finishes_the_reserved_attempt_and_returns_failure_for_commit(
    monkeypatch,
):
    from backend.macro.research_acceptance_replay import OfflineReplayError

    conn = AsyncMock()
    conn.transaction = Mock(return_value=AsyncMock())
    conn.fetchrow.return_value = None
    attempt = uuid4()
    conn.fetchval.side_effect = [{"id": str(attempt), "status": "RUNNING"}, True]
    descriptors = [
        repo.freeze_artifact({}, 1024),
        repo.freeze_artifact("{}", 1024, raw=True),
        repo.freeze_artifact({}, 1024),
    ]
    monkeypatch.setattr(
        repo,
        "read_artifacts_admin",
        AsyncMock(
            return_value={
                "complete": True,
                "validatedAt": NOW.isoformat(),
                "items": [
                    {"kind": kind, **item}
                    for kind, item in zip(repo.KINDS, descriptors)
                ],
            }
        ),
    )
    runner = AsyncMock(side_effect=OfflineReplayError("TIMEOUT"))
    monkeypatch.setattr(
        "backend.macro.research_acceptance_replay.run_offline_validation", runner
    )
    result = await repo.replay_validation(conn, RUN, uuid4(), uuid4(), uuid4(), NOW)
    assert isinstance(result, repo.ReplayFailure) and isinstance(
        result.error, DataUnavailableError
    )
    args = conn.fetchval.await_args.args
    assert "finish_validation_replay" in args[0]
    assert args[1:] == (attempt, "TIMED_OUT", None, "TIMEOUT")
