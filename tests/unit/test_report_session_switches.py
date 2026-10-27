"""Automatic report switches stop work while explicit fund requests still run."""

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.config import Settings
from backend.jobs import alpha_opportunities, runner
from backend.jobs.scheduler import Scheduler
from backend import worker

SWITCHES = (
    "daily_research_enabled",
    "fund_1430_enabled",
    "post_market_review_enabled",
)
PHASES = (
    ("PRE_MARKET_MACRO", "PRE_MARKET", "daily_research_enabled", 8, 30),
    ("PRE_MARKET_PERSONALIZED", "PRE_MARKET", "daily_research_enabled", 8, 35),
    ("FUND_1430", "FUND_1430", "fund_1430_enabled", 14, 30),
    ("POST_MARKET", "POST_MARKET", "post_market_review_enabled", 15, 30),
)


@pytest.fixture(autouse=True)
def clear_switch_overrides(monkeypatch):
    for name in (*SWITCHES, "intraday_tracking_enabled"):
        monkeypatch.delenv(name.upper(), raising=False)


class UnusedPool:
    def acquire(self):
        raise AssertionError("disabled automatic task must not access the database")


@pytest.mark.parametrize("name", SWITCHES)
def test_automatic_report_switch_defaults_to_false(name):
    assert getattr(Settings(_env_file=None), name, None) is False


@pytest.mark.parametrize("name", SWITCHES)
@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_automatic_report_switch_reads_environment(monkeypatch, name, value, expected):
    monkeypatch.setenv(name.upper(), value)
    assert getattr(Settings(_env_file=None), name, None) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,session,name,hour,minute", PHASES)
@pytest.mark.parametrize("enabled", [False, True])
async def test_scheduler_only_dispatches_enabled_report(
    phase, session, name, hour, minute, enabled
):
    execute = AsyncMock()
    scheduler = Scheduler(
        Settings(_env_file=None, **{name: enabled}),
        execute,
        now_provider=lambda: datetime(
            2026, 10, 27, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
    )
    await scheduler._tick()
    # At 08:35 the 08:30 macro phase is still inside its catch-up window.
    expected = ["PRE_MARKET_MACRO", phase] if minute == 35 else [phase]
    assert [call.args[0] for call in execute.await_args_list] == (
        expected if enabled else []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,session,name,hour,minute", PHASES)
async def test_disabled_report_session_never_reads_jobs(
    phase, session, name, hour, minute
):
    assert (
        await runner.run_session(
            UnusedPool(),
            Settings(_env_file=None),
            phase,
            datetime.now(ZoneInfo("Asia/Shanghai")),
        )
        == {}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,session,name,hour,minute", PHASES)
async def test_disabled_persisted_report_never_claims_lease(
    phase, session, name, hour, minute
):
    assert (
        await runner._execute_with_lease(
            UnusedPool(),
            Settings(_env_file=None),
            {},
            f"job:old:2026-10-27:{phase}",
            session,
        )
        == f"SKIPPED:{session}_DISABLED"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,session,name,hour,minute", PHASES)
async def test_disabled_worker_report_does_not_refresh_market(
    monkeypatch, phase, session, name, hour, minute
):
    refreshes = []
    for method in ("refresh_market", "refresh_market_hot", "refresh_market_sentiment"):
        refresh = AsyncMock()
        monkeypatch.setattr(worker.market_service, method, refresh)
        refreshes.append(refresh)
    run = AsyncMock(return_value={})
    monkeypatch.setattr(runner, "run_session", run)
    await worker._execute_session(
        UnusedPool(),
        Settings(_env_file=None),
        phase,
        datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    for refresh in [*refreshes, run]:
        refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_opportunity_tasks_do_not_query_scope():
    assert await alpha_opportunities.sync_alpha_opportunities(
        UnusedPool(),
        Settings(
            _env_file=None,
            opportunity_tenant_id="00000000-0000-0000-0000-000000000001",
            opportunity_user_id="00000000-0000-0000-0000-000000000002",
            wecom_opportunity_webhook_url="https://example.test/webhook",
        ),
    ) == {"morning": {"status": "DISABLED"}, "fund1430": {"status": "DISABLED"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("daily,fund", [(True, False), (False, True)])
async def test_opportunity_switches_are_independent(monkeypatch, daily, fund):
    monkeypatch.setattr(alpha_opportunities, "_active", AsyncMock(return_value=True))
    report = AsyncMock(return_value=None)
    monkeypatch.setattr(alpha_opportunities, "_today_report", report)
    now = datetime(
        2026, 10, 27, 14, 30 if daily else 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    result = await alpha_opportunities.sync_alpha_opportunities(
        UnusedPool(),
        Settings(
            _env_file=None,
            daily_research_enabled=daily,
            fund_1430_enabled=fund,
            opportunity_tenant_id="00000000-0000-0000-0000-000000000001",
            opportunity_user_id="00000000-0000-0000-0000-000000000002",
            wecom_opportunity_webhook_url="https://example.test/webhook",
        ),
        now,
    )
    assert result["morning"]["status"] == ("NOT_READY" if daily else "DISABLED")
    assert result["fund1430"]["status"] == ("NOT_DUE" if fund else "DISABLED")
    assert report.await_count == int(daily)


@pytest.mark.asyncio
async def test_recovery_filters_disabled_reports_before_limit_but_keeps_manual_fund(
    monkeypatch,
):
    class Connection:
        async def fetch(self, sql, *args):
            assert set(args[3]) == {"PRE_MARKET", "INTRADAY", "FUND_1430"}
            assert "ANY($4::text[])" in sql.split("LIMIT")[0]
            assert "manual:FUND_1430:%" in sql
            assert "debug:FUND_1430:%" in sql
            return [
                {"report_session": "PRE_MARKET", "job_key": "old-pre"},
                {"report_session": "INTRADAY", "job_key": "old-intraday"},
                {"report_session": "FUND_1430", "job_key": "old-fund"},
                {"report_session": "POST_MARKET", "job_key": "post"},
                {"report_session": "FUND_1430", "job_key": "manual:FUND_1430:request"},
                {
                    "report_session": "FUND_1430",
                    "job_key": "debug:FUND_1430:00000000-0000-0000-0000-000000000001:request",
                },
            ]

    @asynccontextmanager
    async def connection(_pool):
        yield Connection()

    monkeypatch.setattr(runner, "system_connection", connection)
    execute = AsyncMock(return_value="COMPLETED")
    monkeypatch.setattr(runner, "_execute_with_lease", execute)
    results = await runner.run_recoverable_jobs(
        UnusedPool(), Settings(_env_file=None, post_market_review_enabled=True)
    )
    assert list(results) == [
        "post",
        "manual:FUND_1430:request",
        "debug:FUND_1430:00000000-0000-0000-0000-000000000001:request",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "manual:FUND_1430:request",
        "debug:FUND_1430:00000000-0000-0000-0000-000000000001:request",
    ],
)
async def test_explicit_fund_request_can_claim_when_automatic_fund_is_disabled(
    monkeypatch, key
):
    claim = AsyncMock(return_value=None)
    monkeypatch.setattr(runner, "claim_job_run", claim)
    assert (
        await runner._execute_with_lease(
            UnusedPool(), Settings(_env_file=None), {}, key, "FUND_1430"
        )
        == "SKIPPED:already-running-or-terminal"
    )
    claim.assert_awaited_once()


@pytest.mark.parametrize("service", ["api", "worker"])
def test_nas_configuration_has_all_automatic_report_switches_off(tmp_path, service):
    from dotenv import dotenv_values
    from scripts.configure_nas import generate_configuration

    paths = generate_configuration(tmp_path, web_base_url="https://wealth.example.test")
    values = dotenv_values(paths[service])
    assert {name.upper(): values.get(name.upper()) for name in SWITCHES} == {
        name.upper(): "false" for name in SWITCHES
    }
