"""Behavior tests for the controlled four-session tenant-job backfill."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest


TENANT_ID = UUID("432784c8-8c04-5b0f-956c-eb5d7b5d36ed")


class _Transaction:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn

    async def __aenter__(self):
        self.conn.transaction_entries += 1
        return self.conn

    async def __aexit__(self, exc_type, *_args):
        self.conn.transaction_exits += 1
        self.conn.rolled_back = exc_type is not None
        return False


class FakeConn:
    def __init__(self, *, jobs: list[dict] | None = None) -> None:
        self.tenant = {
            "id": TENANT_ID,
            "name": "Jelly家庭投资账户",
            "status": "ACTIVE",
        }
        self.jobs = list(jobs or [])
        self.audit_rows: list[dict] = []
        self.transaction_entries = 0
        self.transaction_exits = 0
        self.rolled_back = False
        self.tenant_lock_used = False
        self.job_locks_used = False
        self.inserted_sessions: list[str] = []

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, sql: str, *args):
        if "FROM identity.tenants" in sql:
            self.tenant_lock_used = "FOR UPDATE" in sql
            return self.tenant if args[0] == TENANT_ID else None
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql: str, *_args):
        if "FROM workflow.jobs" in sql:
            self.job_locks_used = "FOR UPDATE" in sql
            return list(self.jobs)
        raise AssertionError(f"unexpected fetch: {sql}")

    async def execute(self, sql: str, *args):
        if "INSERT INTO workflow.jobs" in sql:
            names, crons, sessions = args[1], args[2], args[3]
            self.inserted_sessions.extend(sessions)
            for index, (name, cron, session) in enumerate(
                zip(names, crons, sessions, strict=True), start=1
            ):
                self.jobs.append(
                    {
                        "id": UUID(int=index),
                        "job_name": name,
                        "cron_expr": cron,
                        "report_session": session,
                        "is_enabled": True,
                    }
                )
            return f"INSERT 0 {len(sessions)}"
        if "INSERT INTO audit.audit_log" in sql:
            self.audit_rows.append(
                {
                    "tenant_id": args[0],
                    "resource_id": args[1],
                    "details": json.loads(args[2]),
                }
            )
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {sql}")


def _canonical_jobs() -> list[dict]:
    definitions = [
        ("pre_market_brief", "0 30 8 * * 1-5", "PRE_MARKET"),
        ("intraday_tracking", "*/5 9-14 * * 1-5", "INTRADAY"),
        ("fund_1430_estimate", "0 30 14 * * 1-5", "FUND_1430"),
        ("post_market_review", "0 30 15 * * 1-5", "POST_MARKET"),
    ]
    return [
        {
            "id": UUID(int=index),
            "job_name": name,
            "cron_expr": cron,
            "report_session": session,
            "is_enabled": True,
        }
        for index, (name, cron, session) in enumerate(definitions, start=1)
    ]


def test_backfill_command_can_be_launched_by_documented_file_path() -> None:
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "backfill_standard_report_jobs.py"),
            "--help",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--tenant-id" in result.stdout
    assert "--apply" in result.stdout


def test_canonical_schedule_contains_four_unique_sessions() -> None:
    from scripts.standard_report_jobs import STANDARD_REPORT_JOBS

    assert [job.session for job in STANDARD_REPORT_JOBS] == [
        "PRE_MARKET",
        "INTRADAY",
        "FUND_1430",
        "POST_MARKET",
    ]
    assert len({job.name for job in STANDARD_REPORT_JOBS}) == 4


@pytest.mark.asyncio
async def test_command_refuses_to_run_until_migration_040_is_applied(
    monkeypatch,
) -> None:
    """The operator command must not bypass database session uniqueness."""

    from scripts import backfill_standard_report_jobs as command

    class GateConn:
        async def fetchval(self, sql: str):
            if "current_user" in sql:
                return "postgres"
            # Simulate the production failure mode: 033 exists but the new
            # database uniqueness boundary has not yet reached migration 040.
            return "version = 33" in sql

        async def close(self):
            return None

    async def connect(*_args, **_kwargs):
        return GateConn()

    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql://postgres@db.example:5432/postgres",
    )
    monkeypatch.setattr(command.asyncpg, "connect", connect)
    args = SimpleNamespace(
        tenant_id=TENANT_ID,
        tenant_name="Jelly家庭投资账户",
        apply=False,
        confirm_target=None,
        confirm_tenant_id=None,
        confirm_tenant_name=None,
    )

    with pytest.raises(SystemExit, match="migration 040"):
        await command._run(args)


@pytest.mark.asyncio
async def test_preflight_is_read_only_for_an_exact_empty_tenant() -> None:
    from scripts import backfill_standard_report_jobs as command

    conn = FakeConn()
    request = command.BackfillRequest(TENANT_ID, "Jelly家庭投资账户")

    result = await command.backfill_standard_report_jobs(conn, request, apply=False)

    assert result["status"] == "READY"
    assert result["jobCount"] == 4
    assert result["sessions"] == [
        "PRE_MARKET",
        "INTRADAY",
        "FUND_1430",
        "POST_MARKET",
    ]
    assert conn.jobs == []
    assert conn.audit_rows == []
    assert conn.tenant_lock_used is True


@pytest.mark.asyncio
async def test_apply_inserts_all_four_jobs_and_one_audit_record() -> None:
    from scripts import backfill_standard_report_jobs as command

    conn = FakeConn()
    request = command.BackfillRequest(TENANT_ID, "Jelly家庭投资账户")

    result = await command.backfill_standard_report_jobs(conn, request, apply=True)

    assert result["status"] == "APPLIED"
    assert conn.jobs == _canonical_jobs()
    assert len(conn.audit_rows) == 1
    assert conn.audit_rows[0]["resource_id"] == str(TENANT_ID)
    assert conn.audit_rows[0]["details"]["sessions"] == result["sessions"]
    assert conn.audit_rows[0]["details"]["existingSessions"] == []
    assert conn.audit_rows[0]["details"]["insertedSessions"] == result["sessions"]
    assert conn.job_locks_used is True
    assert conn.rolled_back is False


@pytest.mark.asyncio
async def test_exact_existing_schedule_is_idempotent() -> None:
    from scripts import backfill_standard_report_jobs as command

    conn = FakeConn(jobs=_canonical_jobs())
    request = command.BackfillRequest(TENANT_ID, "Jelly家庭投资账户")

    result = await command.backfill_standard_report_jobs(conn, request, apply=True)

    assert result["status"] == "ALREADY_APPLIED"
    assert conn.jobs == _canonical_jobs()
    assert conn.audit_rows == []


@pytest.mark.asyncio
async def test_apply_repairs_an_exact_canonical_subset_without_replacing_it() -> None:
    """A historical POST-only tenant must gain the other three sessions safely."""

    from scripts import backfill_standard_report_jobs as command

    existing_post_market = _canonical_jobs()[3]
    conn = FakeConn(jobs=[existing_post_market])
    request = command.BackfillRequest(TENANT_ID, "Jelly家庭投资账户")

    result = await command.backfill_standard_report_jobs(conn, request, apply=True)

    assert result["status"] == "APPLIED"
    assert result["existingSessions"] == ["POST_MARKET"]
    assert result["insertedSessions"] == [
        "PRE_MARKET",
        "INTRADAY",
        "FUND_1430",
    ]
    assert conn.inserted_sessions == result["insertedSessions"]
    assert existing_post_market in conn.jobs
    assert {
        (
            row["job_name"],
            row["cron_expr"],
            row["report_session"],
            row["is_enabled"],
        )
        for row in conn.jobs
    } == {
        ("pre_market_brief", "0 30 8 * * 1-5", "PRE_MARKET", True),
        ("intraday_tracking", "*/5 9-14 * * 1-5", "INTRADAY", True),
        ("fund_1430_estimate", "0 30 14 * * 1-5", "FUND_1430", True),
        ("post_market_review", "0 30 15 * * 1-5", "POST_MARKET", True),
    }
    assert len(conn.audit_rows) == 1
    assert conn.audit_rows[0]["details"]["existingSessions"] == ["POST_MARKET"]
    assert (
        conn.audit_rows[0]["details"]["insertedSessions"] == result["insertedSessions"]
    )
    assert conn.rolled_back is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "jobs",
    [
        [{**_canonical_jobs()[0], "is_enabled": False}],
        [{**_canonical_jobs()[0], "cron_expr": "0 0 9 * * 1-5"}],
        [{**_canonical_jobs()[0], "job_name": "custom_pre_market"}],
        _canonical_jobs() + [{**_canonical_jobs()[0], "id": UUID(int=99)}],
    ],
)
async def test_conflicting_schedule_aborts_without_writes(jobs) -> None:
    from scripts import backfill_standard_report_jobs as command

    conn = FakeConn(jobs=jobs)
    request = command.BackfillRequest(TENANT_ID, "Jelly家庭投资账户")

    with pytest.raises(RuntimeError, match="manual review"):
        await command.backfill_standard_report_jobs(conn, request, apply=True)

    assert conn.jobs == jobs
    assert conn.audit_rows == []
    assert conn.rolled_back is True


@pytest.mark.asyncio
async def test_failed_readback_aborts_the_backfill_transaction() -> None:
    """A lost or corrupted insert must not be reported as successfully applied."""

    from scripts import backfill_standard_report_jobs as command

    class BrokenReadbackConn(FakeConn):
        async def fetch(self, sql: str, *_args):
            rows = await super().fetch(sql)
            if "FROM workflow.jobs" in sql and self.audit_rows:
                return rows[:-1]
            return rows

    conn = BrokenReadbackConn()
    request = command.BackfillRequest(TENANT_ID, "Jelly家庭投资账户")

    with pytest.raises(RuntimeError, match="readback failed"):
        await command.backfill_standard_report_jobs(conn, request, apply=True)

    assert conn.rolled_back is True
