"""Split pre-market runner: macro once per tenant, personalized per member.

Recovery must reconstruct the phase and audience UUID from the persisted key
so a restarted Worker never replays a personalized run as the legacy
tenant-wide PRE_MARKET path.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.config import Settings
from backend.jobs import runner
from backend.jobs.runner import (
    JobLease,
    business_job_key,
    parse_business_job_key,
    run_session,
)


class _Conn:
    def __init__(self, jobs):
        self._jobs = jobs

    async def fetch(self, _sql, *_args, **_kwargs):
        return self._jobs


class _PoolCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, jobs):
        self._jobs = jobs

    def acquire(self):
        return _PoolCtx(_Conn(self._jobs))

    def system_connection(self, *_args, **_kwargs):
        return _PoolCtx(_Conn(self._jobs))


def test_parse_business_job_key_reconstructs_phase_and_audience() -> None:
    phase, user = parse_business_job_key(
        "job:job-1:2026-09-02:PRE_MARKET_MACRO"
    )
    assert phase == "PRE_MARKET_MACRO" and user is None

    phase, user = parse_business_job_key(
        "job:job-1:2026-09-02:PRE_MARKET_PERSONALIZED:user-9"
    )
    assert phase == "PRE_MARKET_PERSONALIZED" and user == "user-9"

    with pytest.raises(ValueError):
        parse_business_job_key("not-a-key")


def test_parse_business_job_key_rejects_unknown_shapes() -> None:
    with pytest.raises(ValueError):
        parse_business_job_key("job:only:two")
    with pytest.raises(ValueError):
        parse_business_job_key("prefix:job-1:2026-09-02:PRE_MARKET_MACRO")


def test_personalized_business_key_includes_audience_uuid() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 2, 8, 35, tzinfo=shanghai)

    assert (
        business_job_key("job-1", "PRE_MARKET_MACRO", now)
        == "job:job-1:2026-09-02:PRE_MARKET_MACRO"
    )
    assert (
        business_job_key("job-1", "PRE_MARKET_PERSONALIZED", now, user_id="user-9")
        == "job:job-1:2026-09-02:PRE_MARKET_PERSONALIZED:user-9"
    )


def harness():
    captured: dict[str, object] = {}

    claim = AsyncMock(return_value=JobLease("run-1", "lease-1"))

    finish = AsyncMock(return_value=True)
    owners = AsyncMock(return_value="owner-1")
    members = AsyncMock(return_value=["user-1", "user-2"])
    generate = AsyncMock(return_value={"id": "report-1"})

    def install(monkeypatch):
        monkeypatch.setattr(runner, "claim_job_run", claim)
        monkeypatch.setattr(runner, "finish_job_run", finish)
        monkeypatch.setattr(runner, "_tenant_owner_user", owners)
        monkeypatch.setattr(runner, "_active_tenant_members", members)
        monkeypatch.setattr(runner, "generate_report", generate)
        captured["finish"] = finish
        captured["members"] = members
        captured["generate"] = generate
        captured["owner"] = owners

    return captured, install


@pytest.mark.asyncio
async def test_macro_phase_runs_once_per_tenant_via_public_session(
    monkeypatch,
) -> None:
    captured, install = harness()
    install(monkeypatch)
    jobs = [{"id": "job-1", "tenant_id": "tenant-1", "job_name": "pre-market"}]
    pool = _Pool(jobs)
    now = datetime(2026, 9, 2, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    outcomes = await run_session(pool, Settings(_env_file=None), "PRE_MARKET_MACRO", now)

    assert outcomes == {"tenant-1": "COMPLETED:report=report-1"}
    captured["generate"].assert_awaited_once()
    args = captured["generate"].await_args.args
    kwargs = captured["generate"].await_args.kwargs
    assert kwargs["report_variant"] == "PRE_MARKET_MACRO"
    assert args[3] == "PRE_MARKET"
    # Macro is tenant-neutral and broadcast to every PRE_MARKET subscriber.
    assert kwargs["audience_user_id"] is None
    assert kwargs["delivery_user_id"] is None


@pytest.mark.asyncio
async def test_personalized_phase_fans_out_per_active_member_and_isolates_failure(
    monkeypatch,
) -> None:
    captured, install = harness()
    install(monkeypatch)

    async def personalized(*_a, **_k):
        # The member context carries the audience UUID as its user_id.
        member_ctx = _a[1]
        if member_ctx.user_id == "user-2":
            raise RuntimeError("member transient failure")
        return {"id": "report-1"}

    monkeypatch.setattr(
        runner.pre_market_personalized,
        "generate_pre_market_personalized",
        personalized,
    )
    jobs = [{"id": "job-1", "tenant_id": "tenant-1", "job_name": "pre-market"}]
    pool = _Pool(jobs)
    now = datetime(2026, 9, 2, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai"))

    outcomes = await run_session(
        pool, Settings(_env_file=None), "PRE_MARKET_PERSONALIZED", now
    )

    assert outcomes == {
        "tenant-1:user-1": "COMPLETED:report=report-1",
        "tenant-1:user-2": "FAILED:RuntimeError",
    }
    # Each member gets a distinct terminal key carrying phase + audience UUID.
    keys = {call.args[2] for call in runner.claim_job_run.call_args_list}
    assert "job:job-1:2026-09-02:PRE_MARKET_PERSONALIZED:user-1" in keys
    assert "job:job-1:2026-09-02:PRE_MARKET_PERSONALIZED:user-2" in keys


@pytest.mark.asyncio
async def test_personalized_phase_skips_members_without_personal_context(
    monkeypatch,
) -> None:
    captured, install = harness()
    install(monkeypatch)
    # None means the member has neither holdings nor a watchlist.
    captured["generate"].return_value = {"id": "report-1"}
    personalized = AsyncMock(return_value=None)
    monkeypatch.setattr(
        runner.pre_market_personalized,
        "generate_pre_market_personalized",
        personalized,
    )
    jobs = [{"id": "job-1", "tenant_id": "tenant-1", "job_name": "pre-market"}]
    pool = _Pool(jobs)
    # Only one active member here: it has no personal context to report.
    monkeypatch.setattr(runner, "_active_tenant_members", AsyncMock(return_value=["user-1"]))
    now = datetime(2026, 9, 2, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai"))

    outcomes = await run_session(
        pool, Settings(_env_file=None), "PRE_MARKET_PERSONALIZED", now
    )

    assert outcomes == {"tenant-1:user-1": "SKIPPED:NO_PERSONAL_CONTEXT"}
    captured["finish"].assert_any_await(
        pool, "run-1", "lease-1", status="SKIPPED", error_class="NO_PERSONAL_CONTEXT"
    )


@pytest.mark.asyncio
async def test_already_terminal_key_returns_skip_without_generating(
    monkeypatch,
) -> None:
    captured, install = harness()
    install(monkeypatch)
    monkeypatch.setattr(runner, "_active_tenant_members", AsyncMock(return_value=["user-1"]))

    async def claim_none(*_a, **_k):
        return None

    monkeypatch.setattr(runner, "claim_job_run", claim_none)
    jobs = [{"id": "job-1", "tenant_id": "tenant-1", "job_name": "pre-market"}]
    pool = _Pool(jobs)
    now = datetime(2026, 9, 2, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai"))

    outcomes = await run_session(
        pool, Settings(_env_file=None), "PRE_MARKET_PERSONALIZED", now
    )

    assert outcomes == {"tenant-1:user-1": "SKIPPED:already-running-or-terminal"}
    captured["generate"].assert_not_awaited()
