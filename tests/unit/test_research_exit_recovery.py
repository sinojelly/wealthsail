"""Late trusted receipts recover only their durable guard, without another model."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from backend.integrations.research_lifecycle import ResearchLifecycle
from tests.unit.test_daily_research_job import NOW


def boundary(monkeypatch, count=1):
    mod = import_module("backend.jobs.research_exit_recovery")
    records = [
        {"run_id": uuid4(), "owner_token": uuid4(), "released": False}
        for _ in range(count)
    ]
    state = {"connections": 0, "reads": [], "writes": 0, "releaseTimes": []}

    class Conn:
        async def fetch(self, sql, limit):
            state["reads"].append((sql, limit))
            assert "released_at IS NULL" in sql and "LIMIT $1" in sql
            return [row.copy() for row in records if not row["released"]][:limit]

        async def fetchval(self, sql, run, owner, now):
            assert "UPDATE research.daily_research_model_guards" in sql
            assert "owner_token=$2" in sql and "released_at IS NULL" in sql
            state["writes"] += 1
            state["releaseTimes"].append(now)
            for row in records:
                if (
                    row["run_id"] == run
                    and row["owner_token"] == owner
                    and not row["released"]
                ):
                    row["released"] = True
                    return run
            return None

    @asynccontextmanager
    async def connection(_pool):
        state["connections"] += 1
        try:
            yield Conn()
        finally:
            state["connections"] -= 1

    monkeypatch.setattr(mod, "system_connection", connection)
    return mod, records, state


def proof(identity):
    lifecycle = ResearchLifecycle(identity)
    lifecycle.started()
    lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
    return lifecycle.snapshot()


def test_invocation_identity_is_reconstructible_and_changes_with_run_or_owner():
    mod = import_module("backend.jobs.research_exit_recovery")
    run, owner = uuid4(), uuid4()
    first = mod.invocation_identity(run, owner)
    assert first == mod.invocation_identity(str(run), str(owner))
    assert first["runId"] == str(run) and first["ownerToken"] == str(owner)
    assert UUID(first["callId"]).version == 5
    assert mod.invocation_identity(uuid4(), owner)["callId"] != first["callId"]
    assert mod.invocation_identity(run, uuid4())["callId"] != first["callId"]
    for bad in (None, "invalid", 1, True):
        with pytest.raises(ValueError):
            mod.invocation_identity(bad, owner)
        with pytest.raises(ValueError):
            mod.invocation_identity(run, bad)


@pytest.mark.asyncio
async def test_late_proof_after_recovery_window_and_worker_restart_releases_only_guard(
    monkeypatch,
):
    mod, records, state = boundary(monkeypatch)
    row = records[0]
    seen = []
    clock = [NOW]
    monkeypatch.setattr(mod, "now_shanghai", lambda: clock[0])

    async def status(_settings, identity):
        assert state["connections"] == 0
        seen.append(identity)
        if len(seen) == 1:
            return ResearchLifecycle(identity).snapshot()
        return proof(identity)

    monkeypatch.setattr(mod.codex_client, "research_status", status)
    never_model = AsyncMock(side_effect=AssertionError("must not infer"))
    monkeypatch.setattr(mod.codex_client, "research", never_model)
    settings = SimpleNamespace(codex_runner_url="http://local-fixture.invalid")
    assert (await mod.recover_research_exits(object(), settings))["released"] == 0
    # No process-local callId memory is needed after a later poll/restart.
    clock[0] += timedelta(seconds=65)
    recovered = await mod.recover_research_exits(object(), settings)
    assert recovered["released"] == 1
    assert row["released"] is True
    assert (
        seen[0] == seen[1] == mod.invocation_identity(row["run_id"], row["owner_token"])
    )
    assert state["writes"] == 1
    assert state["releaseTimes"] == [NOW + timedelta(seconds=65)]
    assert (await mod.recover_research_exits(object(), settings))["checked"] == 0
    never_model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["runId", "ownerToken", "callId"])
async def test_foreign_or_restarted_runner_evidence_keeps_guard(monkeypatch, field):
    mod, records, state = boundary(monkeypatch)

    async def wrong(_settings, identity):
        return proof({**identity, field: str(uuid4())})

    monkeypatch.setattr(mod.codex_client, "research_status", wrong)
    settings = SimpleNamespace(codex_runner_url="http://local-fixture.invalid")
    assert (await mod.recover_research_exits(object(), settings))["released"] == 0
    monkeypatch.setattr(
        mod.codex_client, "research_status", AsyncMock(return_value=None)
    )
    assert (await mod.recover_research_exits(object(), settings))["released"] == 0
    assert records[0]["released"] is False and state["writes"] == 0


@pytest.mark.asyncio
async def test_recovery_is_bounded_and_one_transport_failure_does_not_skip_others(
    monkeypatch, caplog
):
    mod, records, state = boundary(monkeypatch, 7)
    checked = []

    async def status(_settings, identity):
        assert state["connections"] == 0
        checked.append(identity)
        if len(checked) == 1:
            raise ConnectionError("PRIVATE prompt URL owner body")
        return proof(identity)

    monkeypatch.setattr(mod.codex_client, "research_status", status)
    with caplog.at_level("INFO"):
        result = await mod.recover_research_exits(
            object(), SimpleNamespace(codex_runner_url="http://local-fixture.invalid")
        )
    assert result["checked"] == 4 and result["released"] == 3 and result["errors"] == 1
    assert state["reads"][0][1] == 4 and len(checked) == 4
    assert sum(row["released"] for row in records) == 3
    assert "PRIVATE" not in caplog.text
    assert all(str(row["owner_token"]) not in caplog.text for row in records)


@pytest.mark.asyncio
async def test_local_cli_has_no_cross_process_proof_and_does_not_touch_guards(
    monkeypatch,
):
    mod, records, state = boundary(monkeypatch)
    status = AsyncMock(side_effect=AssertionError("local cannot query runner"))
    monkeypatch.setattr(mod.codex_client, "research_status", status)
    result = await mod.recover_research_exits(
        object(), SimpleNamespace(codex_runner_url="")
    )
    assert result["checked"] == 0 and result["status"] == "LOCAL_UNSUPPORTED"
    assert not state["reads"] and records[0]["released"] is False


@pytest.mark.asyncio
async def test_owner_changed_while_network_receipt_arrives_cannot_release_new_owner(
    monkeypatch,
):
    mod, records, _state = boundary(monkeypatch)

    async def status(_settings, identity):
        records[0]["owner_token"] = uuid4()
        return proof(identity)

    monkeypatch.setattr(mod.codex_client, "research_status", status)
    result = await mod.recover_research_exits(
        object(), SimpleNamespace(codex_runner_url="http://local-fixture.invalid")
    )
    assert result["released"] == 0 and records[0]["released"] is False


@pytest.mark.asyncio
async def test_duplicate_concurrent_proofs_release_guard_only_once(monkeypatch):
    mod, records, state = boundary(monkeypatch)

    async def status(_settings, identity):
        await asyncio.sleep(0)
        return proof(identity)

    monkeypatch.setattr(mod.codex_client, "research_status", status)
    settings = SimpleNamespace(codex_runner_url="http://local-fixture.invalid")
    results = await asyncio.gather(
        *(mod.recover_research_exits(object(), settings) for _ in range(2))
    )
    assert sum(result["released"] for result in results) == 1
    assert records[0]["released"] is True and state["writes"] == 2


@pytest.mark.asyncio
async def test_service_runs_immediately_and_keeps_polling_after_safe_failure(
    monkeypatch, caplog
):
    mod = import_module("backend.jobs.research_exit_recovery")
    stop = asyncio.Event()
    seen = []

    async def recover(*args):
        seen.append("recover")
        if seen.count("recover") == 1:
            raise RuntimeError("PRIVATE URI")
        return {"checked": 0, "released": 0, "pending": 0, "errors": 0}

    async def wait(event, seconds):
        seen.append(seconds)
        if seen.count("recover") == 2:
            event.set()

    monkeypatch.setattr(mod, "recover_research_exits", recover)
    with caplog.at_level("WARNING"):
        await mod.run_research_exit_recovery_service(
            object(), SimpleNamespace(), stop_event=stop, wait=wait
        )
    assert seen == ["recover", 60, "recover", 60]
    assert "PRIVATE" not in caplog.text


@pytest.mark.asyncio
async def test_runtime_starts_guard_recovery_even_when_every_inference_mode_is_disabled(
    monkeypatch,
):
    mod = import_module("backend.jobs.research_exit_recovery")
    from backend.jobs.alpha_runtime import run_alpha_runtime

    entered, stop = asyncio.Event(), asyncio.Event()
    settings = SimpleNamespace(
        daily_research_enabled=False,
        daily_research_manual_enabled=False,
        daily_research_debug_enabled=False,
    )

    async def recover(pool, actual_settings, *, stop_event):
        assert actual_settings is settings
        entered.set()
        await stop_event.wait()

    monkeypatch.setattr(mod, "run_research_exit_recovery_service", recover)
    task = asyncio.create_task(run_alpha_runtime(object(), settings, stop_event=stop))
    try:
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        stop.set()
        await task


@pytest.mark.asyncio
async def test_failed_recovery_service_does_not_stop_other_runtime_services(
    monkeypatch,
):
    mod = import_module("backend.jobs.research_exit_recovery")
    from backend.jobs import research_archive
    from backend.jobs.alpha_runtime import run_alpha_runtime

    entered, failed, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def archive(_pool, *, stop_event):
        entered.set()
        await stop_event.wait()

    async def recover(*args, **kwargs):
        failed.set()
        raise RuntimeError("fixture")

    monkeypatch.setattr(research_archive, "run_research_archive_service", archive)
    monkeypatch.setattr(mod, "run_research_exit_recovery_service", recover)
    task = asyncio.create_task(
        run_alpha_runtime(
            object(), SimpleNamespace(daily_research_enabled=False), stop_event=stop
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(failed.wait(), 1)
        assert not task.done()
    finally:
        stop.set()
        await task
