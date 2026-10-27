"""Turning off automatic intraday tracking must stop work, not just delivery."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.config import Settings
from backend.jobs import runner
from backend.jobs.scheduler import Scheduler
from backend.reports import intraday


@pytest.fixture(autouse=True)
def clear_intraday_override(monkeypatch):
    monkeypatch.delenv("INTRADAY_TRACKING_ENABLED", raising=False)


def test_intraday_tracking_defaults_to_disabled():
    assert getattr(Settings(_env_file=None), "intraday_tracking_enabled", None) is False


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_intraday_tracking_can_be_set_from_environment(monkeypatch, value, expected):
    monkeypatch.setenv("INTRADAY_TRACKING_ENABLED", value)
    assert (
        getattr(Settings(_env_file=None), "intraday_tracking_enabled", None) is expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,expected", [(False, ["FUND_1430"]), (True, ["INTRADAY", "FUND_1430"])]
)
async def test_disabled_intraday_does_not_dispatch_but_1430_still_runs(
    enabled, expected
):
    seen = []

    async def execute(phase, _now):
        seen.append(phase)

    scheduler = Scheduler(
        Settings(
            _env_file=None, intraday_tracking_enabled=enabled, fund_1430_enabled=True
        ),
        execute,
        now_provider=lambda: datetime(
            2026, 10, 27, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
    )
    await scheduler._tick()
    assert seen == expected


class UnusedPool:
    def acquire(self):
        raise AssertionError("disabled tracking must not access the database")


@pytest.mark.asyncio
async def test_disabled_intraday_session_does_not_query_jobs_or_create_runs():
    result = await runner.run_session(
        UnusedPool(),
        Settings(_env_file=None),
        "INTRADAY",
        datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    assert result == {}


@pytest.mark.asyncio
async def test_disabled_intraday_recovery_cannot_claim_an_old_lease():
    result = await runner._execute_with_lease(
        UnusedPool(),
        Settings(_env_file=None),
        {},
        "job:old:2026-10-27:INTRADAY:1400:member",
        "INTRADAY",
    )
    assert result == "SKIPPED:INTRADAY_DISABLED"


@pytest.mark.asyncio
async def test_disabled_alert_check_does_not_read_holdings_even_if_called_directly():
    assert (
        await intraday.maybe_generate_intraday_alert(
            UnusedPool(), None, Settings(_env_file=None)
        )
        is None
    )


def test_nas_configuration_generates_disabled_worker_switch(tmp_path):
    from dotenv import dotenv_values
    from scripts.configure_nas import generate_configuration

    paths = generate_configuration(tmp_path, web_base_url="https://wealth.example.test")
    assert dotenv_values(paths["worker"]).get("INTRADAY_TRACKING_ENABLED") == "false"


@pytest.mark.asyncio
async def test_disabled_recovery_ignores_old_intraday_but_keeps_other_sessions(
    monkeypatch,
):
    class Connection:
        async def fetch(self, sql, *args):
            assert "ANY($4::text[])" in sql
            assert "INTRADAY" in args[-1]
            assert "FUND_1430" not in args[-1]
            return [
                {"report_session": "INTRADAY", "job_key": "old-intraday"},
                {"report_session": "FUND_1430", "job_key": "fund"},
            ]

    class Acquire:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_args):
            return None

    class Pool:
        def acquire(self):
            return Acquire()

    execute = AsyncMock(return_value="COMPLETED")
    monkeypatch.setattr(runner, "_execute_with_lease", execute)
    result = await runner.run_recoverable_jobs(
        Pool(), Settings(_env_file=None, fund_1430_enabled=True)
    )
    assert result == {"fund": "COMPLETED"}
    assert execute.await_args.args[-1] == "FUND_1430"
    assert execute.await_count == 1
