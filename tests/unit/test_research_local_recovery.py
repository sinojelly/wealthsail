"""Controlled reboot recovery uses fixtures; never releases a real guard."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pytest

NOW = datetime(2026, 11, 8, 5, tzinfo=timezone.utc)


def fixture(monkeypatch):
    mod = import_module("backend.jobs.research_local_recovery")
    run, owner, request, tenant, user, batch = (uuid4() for _ in range(6))
    row = dict(
        run_id=run,
        owner_token=owner,
        acquired_at=NOW - timedelta(hours=2),
        released_at=None,
        dispatched_at=NOW - timedelta(hours=2, seconds=1),
        run_mode="MANUAL",
        debug_request_id=request,
        source_batch_id=batch,
        request_mode="MANUAL",
        tenant_id=tenant,
        request_status="RUNNING",
        started_at=NOW - timedelta(hours=2, minutes=1),
        batch_id=None,
        report_run_id=None,
        source_mode="MANUAL",
        source_request_id=request,
        result_status="FAILED",
        recorded_at=NOW - timedelta(hours=1, minutes=59),
        exit_evidence=None,
    )
    state = dict(
        sql=[],
        locked=False,
        committed=False,
        owner=True,
        busy=False,
        running=[dict(id=request)],
        pipelines=[],
        cas=True,
        queues=2,
        table_busy=False,
        request_cas=True,
        outer_transaction=False,
    )

    class Conn:
        def is_in_transaction(self):
            return state["outer_transaction"]

        @asynccontextmanager
        async def transaction(self, **kwargs):
            assert state["locked"]
            try:
                yield
            except BaseException:
                state["committed"] = False
                raise
            else:
                state["committed"] = True

        async def fetchrow(self, sql, *args):
            state["sql"].append((sql, args))
            assert "daily_research_model_guards" in sql
            return row.copy()

        async def fetch(self, sql, *args):
            state["sql"].append((sql, args))
            if "daily_research_pipelines" in sql:
                return state["pipelines"]
            assert "status='RUNNING'" in sql and "FOR UPDATE" in sql
            return state["running"]

        async def fetchval(self, sql, *args):
            state["sql"].append((sql, args))
            if "pg_try_advisory_lock" in sql:
                state["locked"] = not state["busy"]
                return state["locked"]
            if "pg_advisory_unlock" in sql:
                state["locked"] = False
                return True
            if "clock_timestamp" in sql:
                return NOW
            if "identity.tenant_members" in sql:
                return state["owner"]
            if "SET released_at" in sql:
                assert (
                    "acquired_at=$3" in sql
                    and "owner_token=$2" in sql
                    and "released_at IS NULL" in sql
                )
                return row["run_id"] if state["cas"] else None
            if "status='QUEUED'" in sql:
                return False
            raise AssertionError(sql)

        async def execute(self, sql, *args):
            state["sql"].append((sql, args))
            assert state["locked"]
            if "lock_local_research_maintenance()" in sql and state["table_busy"]:

                class LockBusy(Exception):
                    sqlstate = "55P03"

                raise LockBusy()
            if "SET status=$2" in sql:
                return "UPDATE 1" if state["request_cas"] else "UPDATE 0"
            return (
                f"UPDATE {state['queues']}"
                if "status='EXPIRED'" in sql
                else "INSERT 0 1"
            )

    settings = SimpleNamespace(
        worker_database_url="postgresql://fixture:private@127.0.0.1:55432/fixture",
        codex_runner_url="",
    )
    monkeypatch.setattr(mod, "_platform_is_windows", lambda: True)
    monkeypatch.setattr(
        mod,
        "windows_boot_evidence",
        lambda: {
            "bootAt": NOW - timedelta(hours=1),
            "checkedAt": NOW,
            "source": "NT_SYSTEM_TIME_OF_DAY",
            "uptimeMs": 3600000,
        },
    )
    kwargs = dict(
        run_id=run,
        confirm_run=run,
        expected_snapshot=mod.guard_snapshot(row),
        tenant_id=tenant,
        user_id=user,
        attest_local_origin=True,
        attest_no_clock_change=True,
    )
    return mod, Conn(), settings, row, state, kwargs


@pytest.mark.asyncio
async def test_default_inspect_never_locks_writes_or_exposes_owner_token(monkeypatch):
    mod, conn, _, row, state, _ = fixture(monkeypatch)
    result = await mod.inspect_local_guard(conn, row["run_id"])
    assert result["snapshot"] == mod.guard_snapshot(row)
    assert result["evidenceState"] == "LOCAL_EVIDENCE_NOT_RETAINED"
    assert result["canRelease"] is False and result["automaticRecovery"] is False
    assert str(row["owner_token"]) not in str(result)
    assert len(state["sql"]) == 1


@pytest.mark.asyncio
async def test_exact_reboot_maintenance_terminalizes_old_queue_then_cas_and_audit(
    monkeypatch,
):
    mod, conn, settings, row, state, kwargs = fixture(monkeypatch)
    result = await mod.recover_legacy_local_reboot(conn, settings, **kwargs)
    assert result == {
        "status": "RELEASED",
        "evidenceKind": "LOCAL_WINDOWS_REBOOT",
        "expiredQueued": 2,
        "recoveredRequest": True,
    }
    assert state["committed"] is True and state["locked"] is False
    sqls = [sql for sql, _ in state["sql"]]
    terminal = next(i for i, s in enumerate(sqls) if "SET status=$2" in s)
    queue = next(i for i, s in enumerate(sqls) if "SET status='EXPIRED'" in s)
    cas = next(i for i, s in enumerate(sqls) if "SET released_at" in s)
    audit = next(
        i
        for i, s in enumerate(sqls)
        if "INSERT INTO research.local_research_recovery_audit" in s
    )
    assert terminal < queue < cas < audit
    assert any(
        "SELECT research.lock_local_research_maintenance()" in sql
        for sql in sqls[:terminal]
    )
    assert all(
        "UPDATE research.daily_research_results" not in s
        and "UPDATE research.daily_research_runs" not in s
        for s in sqls
    )
    terminal_args = state["sql"][terminal][1]
    assert (
        terminal_args[1] == "FAILED"
        and row["run_id"] in terminal_args
        and row["source_batch_id"] in terminal_args
    )
    assert "exitConfirmed" not in str(state["sql"][cas])
    assert state["sql"][audit][1][-1] == max(
        row["acquired_at"], NOW - timedelta(minutes=5)
    )
    assert str(row["owner_token"]) not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "remote",
        "dsn",
        "query_host",
        "platform",
        "no_attestation",
        "clock_attestation",
        "confirm",
        "snapshot",
        "owner",
        "preboot",
        "clock_skew",
        "busy",
        "other_running",
        "pipeline",
        "source_mismatch",
        "scope",
        "cas",
        "table_busy",
        "request_cas",
        "outer_transaction",
    ],
)
async def test_maintenance_preconditions_fail_closed(monkeypatch, defect):
    mod, conn, settings, row, state, kwargs = fixture(monkeypatch)
    if defect == "remote":
        settings.codex_runner_url = "https://fixture.invalid"
    elif defect == "dsn":
        settings.worker_database_url = "postgresql://fixture@10.0.0.2/fixture"
    elif defect == "query_host":
        settings.worker_database_url += "?host=10.0.0.2"
    elif defect == "platform":
        monkeypatch.setattr(mod, "_platform_is_windows", lambda: False)
    elif defect == "no_attestation":
        kwargs["attest_local_origin"] = False
    elif defect == "clock_attestation":
        kwargs["attest_no_clock_change"] = False
    elif defect == "confirm":
        kwargs["confirm_run"] = uuid4()
    elif defect == "snapshot":
        kwargs["expected_snapshot"] = "0" * 64
    elif defect == "owner":
        state["owner"] = False
    elif defect == "preboot":
        monkeypatch.setattr(
            mod,
            "windows_boot_evidence",
            lambda: {
                "bootAt": row["acquired_at"] + timedelta(seconds=30),
                "checkedAt": NOW,
                "source": "NT_SYSTEM_TIME_OF_DAY",
                "uptimeMs": 1,
            },
        )
    elif defect == "clock_skew":
        monkeypatch.setattr(
            mod,
            "windows_boot_evidence",
            lambda: {
                "bootAt": NOW - timedelta(hours=1),
                "checkedAt": NOW - timedelta(minutes=1),
                "source": "NT_SYSTEM_TIME_OF_DAY",
                "uptimeMs": 1,
            },
        )
    elif defect == "busy":
        state["busy"] = True
    elif defect == "other_running":
        state["running"].append(dict(id=uuid4()))
    elif defect == "pipeline":
        state["pipelines"].append(dict(id=uuid4()))
    elif defect == "source_mismatch":
        row["source_request_id"] = uuid4()
    elif defect == "scope":
        row["tenant_id"] = uuid4()
    elif defect == "cas":
        state["cas"] = False
    elif defect == "table_busy":
        state["table_busy"] = True
    elif defect == "request_cas":
        state["request_cas"] = False
    elif defect == "outer_transaction":
        state["outer_transaction"] = True
    with pytest.raises(mod.LocalRecoveryDenied):
        await mod.recover_legacy_local_reboot(conn, settings, **kwargs)
    assert state["locked"] is False and state["committed"] is False
    if defect != "cas":
        assert not any("SET released_at" in sql for sql, _ in state["sql"])


@pytest.mark.asyncio
async def test_no_result_recovers_request_as_uncertain_without_inventing_result(
    monkeypatch,
):
    mod, conn, settings, row, state, kwargs = fixture(monkeypatch)
    row.update(result_status=None, recorded_at=None)
    await mod.recover_legacy_local_reboot(conn, settings, **kwargs)
    args = next(args for sql, args in state["sql"] if "SET status=$2" in sql)
    assert args[1] == "UNCERTAIN" and "LocalRebootRecovered" in args


def test_cli_defaults_to_inspect_and_has_no_force_or_ttl_bypass():
    mod = import_module("backend.jobs.research_local_recovery")
    args = mod.parser().parse_args(["--run-id", str(uuid4())])
    assert args.action == "inspect"
    assert not args.attest_local_origin and not args.attest_no_clock_change
    assert "--force" not in mod.parser().format_help()
