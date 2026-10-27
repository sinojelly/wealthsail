"""Condition-triggered INTRADAY alert: evaluation, content, dedup, delivery.

The scheduler still ticks INTRADAY every 5 minutes; these tests pin the
"only push when a threshold is breached" behavior and the 30-minute per-user
per-alert cooldown.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from backend.config import Settings
from backend.jobs import runner
from backend.jobs.runner import JobLease, run_session
from backend.reports import intraday
from backend.reports.intraday import (
    INTRADAY_ALERT_DEDUP_WINDOW,
    INTRADAY_ALERT_VARIANT,
    INTRADAY_PORTFOLIO_THRESHOLD_PCT,
    INTRADAY_SINGLE_THRESHOLD_PCT,
    IntradayTrigger,
    build_intraday_alert_content,
    evaluate_triggers,
)


def _position(name, symbol, instrument_id, change_pct):
    return {
        "instrumentId": instrument_id,
        "symbol": symbol,
        "name": name,
        "changePct": change_pct,
    }


# --------------------------------------------------------------------------- #
# Pure evaluation
# --------------------------------------------------------------------------- #
def test_single_holding_breach_is_detected() -> None:
    positions = [
        _position("茅台", "600519", "inst-1", "3.5"),
        _position("平安", "601318", "inst-2", "-1.2"),
    ]
    triggers = evaluate_triggers(positions, None)

    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.kind == "SINGLE_HOLDING"
    assert trigger.alert_key == "HOLDING:inst-1"
    assert trigger.change_pct == Decimal("3.5")
    assert trigger.direction == "UP"
    assert trigger.instrument_id == "inst-1"
    assert trigger.symbol == "600519"
    assert trigger.name == "茅台"


def test_holding_within_threshold_is_not_a_trigger() -> None:
    positions = [_position("平安", "601318", "inst-2", "2.9")]
    assert evaluate_triggers(positions, None) == []


def test_negative_breach_direction_is_down() -> None:
    positions = [_position("平安", "601318", "inst-2", "-3.1")]
    triggers = evaluate_triggers(positions, None)

    assert len(triggers) == 1
    assert triggers[0].direction == "DOWN"
    assert triggers[0].change_pct == Decimal("-3.1")


def test_missing_change_pct_is_skipped() -> None:
    positions = [_position("平安", "601318", "inst-2", None)]
    assert evaluate_triggers(positions, None) == []


def test_portfolio_breach_is_detected() -> None:
    positions = [_position("平安", "601318", "inst-2", "1.0")]
    triggers = evaluate_triggers(positions, Decimal("-2.1"))

    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.kind == "PORTFOLIO"
    assert trigger.alert_key == "PORTFOLIO"
    assert trigger.direction == "DOWN"


def test_both_single_and_portfolio_breach_produce_two_alerts() -> None:
    positions = [_position("茅台", "600519", "inst-1", "4.0")]
    triggers = evaluate_triggers(positions, Decimal("2.5"))

    keys = {t.alert_key for t in triggers}
    assert keys == {"HOLDING:inst-1", "PORTFOLIO"}


def test_portfolio_within_threshold_is_not_a_trigger() -> None:
    positions = [_position("平安", "601318", "inst-2", "1.0")]
    assert evaluate_triggers(positions, Decimal("1.9")) == []


# --------------------------------------------------------------------------- #
# Portfolio day-change derivation (dailyPnlRate is intentionally null)
# --------------------------------------------------------------------------- #
def test_portfolio_day_change_pct_derivation() -> None:
    # base = 50000, daily = 1000 -> 2%
    summary = {"dailyPnl": "1000.00", "totalAsset": "51000.00"}
    assert intraday._portfolio_day_change_pct(summary) == Decimal("2")


def test_portfolio_day_change_pct_negative() -> None:
    summary = {"dailyPnl": "-500.00", "totalAsset": "49500.00"}
    assert intraday._portfolio_day_change_pct(summary) == Decimal("-1")


def test_portfolio_day_change_pct_missing_inputs() -> None:
    assert (
        intraday._portfolio_day_change_pct({"dailyPnl": None, "totalAsset": "100"})
        is None
    )
    assert (
        intraday._portfolio_day_change_pct({"dailyPnl": "1", "totalAsset": None})
        is None
    )


# --------------------------------------------------------------------------- #
# Alert content includes the trigger condition and current data
# --------------------------------------------------------------------------- #
def test_build_content_includes_condition_and_live_data() -> None:
    triggers = [
        IntradayTrigger(
            "SINGLE_HOLDING",
            "HOLDING:inst-1",
            Decimal("3.50"),
            INTRADAY_SINGLE_THRESHOLD_PCT,
            "UP",
            "inst-1",
            "600519",
            "茅台",
        ),
        IntradayTrigger(
            "PORTFOLIO",
            "PORTFOLIO",
            Decimal("-2.10"),
            INTRADAY_PORTFOLIO_THRESHOLD_PCT,
            "DOWN",
        ),
    ]
    content = build_intraday_alert_content(
        triggers, Decimal("-2.10"), as_of=datetime(2026, 9, 2, 14, 35)
    )

    assert "茅台" in content
    assert "+3.50%" in content
    assert "±3%" in content
    assert "组合整体今日涨跌幅 -2.10%" in content
    assert "±2%" in content
    assert "数据截至 14:35" in content


# --------------------------------------------------------------------------- #
# Orchestrator: gating + dedup + delivery
# --------------------------------------------------------------------------- #
class _Conn:
    pass


class _Txn:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, *_a):
        return None


def _ctx():
    from backend.common.tenancy import TenantContext

    return TenantContext("user-1", "tenant-1", tenant_role="OWNER")


def _patch_orchestrator(positions, summary, change_map, claim_return):
    """Patch every external dependency of maybe_generate_intraday_alert."""
    from contextlib import asynccontextmanager

    mocks = {}
    mocks["build"] = AsyncMock(
        return_value={"positions": positions, "summary": summary}
    )
    mocks["day"] = AsyncMock(return_value=change_map)
    mocks["claim"] = AsyncMock(return_value=claim_return)
    mocks["gen"] = AsyncMock(return_value={"id": "report-1"})

    @asynccontextmanager
    async def _txn(*_a, **_k):
        yield _Conn()

    ctx = patch.multiple(
        intraday,
        tenant_transaction=_txn,
        build_report_context=mocks["build"],
        generate_report=mocks["gen"],
    )
    ctx.__enter__()
    patch.object(intraday.repository, "latest_day_change_pct", new=mocks["day"]).start()
    patch.object(
        intraday.repository, "claim_intraday_alert", new=mocks["claim"]
    ).start()
    return mocks


def _stop_patches() -> None:
    patch.stopall()


@pytest.mark.asyncio
async def test_orchestrator_returns_none_when_no_trigger() -> None:
    mocks = _patch_orchestrator(
        [_position("平安", "601318", "inst-2", "1.0")],
        {"dailyPnl": "0", "totalAsset": "100"},
        {"inst-2": Decimal("1.0")},
        claim_return=True,
    )
    try:
        result = await intraday.maybe_generate_intraday_alert(
            _pool(), _ctx(), Settings(_env_file=None, intraday_tracking_enabled=True)
        )
        # No breach -> generate must not be called, and no cooldown recorded.
        assert result is None
        mocks["gen"].assert_not_awaited()
        mocks["claim"].assert_not_awaited()
    finally:
        _stop_patches()


@pytest.mark.asyncio
async def test_orchestrator_skips_when_cooldown_active() -> None:
    mocks = _patch_orchestrator(
        [_position("茅台", "600519", "inst-1", "3.5")],
        {"dailyPnl": "0", "totalAsset": "100"},
        {"inst-1": Decimal("3.5")},
        claim_return=False,  # already sent within 30 minutes
    )
    try:
        result = await intraday.maybe_generate_intraday_alert(
            _pool(), _ctx(), Settings(_env_file=None, intraday_tracking_enabled=True)
        )
    finally:
        _stop_patches()

    assert result is None
    mocks["gen"].assert_not_awaited()
    mocks["claim"].assert_awaited_once()


@pytest.mark.asyncio
async def test_orchestrator_generates_when_triggered_and_not_cooled_down() -> None:
    mocks = _patch_orchestrator(
        [_position("茅台", "600519", "inst-1", "3.5")],
        {"dailyPnl": "0", "totalAsset": "100"},
        {"inst-1": Decimal("3.5")},
        claim_return=True,
    )
    try:
        result = await intraday.maybe_generate_intraday_alert(
            _pool(),
            _ctx(),
            Settings(_env_file=None, intraday_tracking_enabled=True),
            job_run_id="job-1",
        )
    finally:
        _stop_patches()

    assert result == {"id": "report-1"}
    mocks["gen"].assert_awaited_once()
    kwargs = mocks["gen"].call_args.kwargs
    assert kwargs["report_variant"] == INTRADAY_ALERT_VARIANT
    assert kwargs["force"] is True
    assert kwargs["audience_user_id"] == "user-1"
    assert kwargs["delivery_user_id"] == "user-1"
    assert kwargs["push_content_override"] is not None
    assert "茅台" in kwargs["push_content_override"]
    # Cooldown recorded for the specific alert key (conn omitted from equality).
    claim_args = mocks["claim"].call_args.args
    assert claim_args[1] == "tenant-1"
    assert claim_args[2] == "user-1"
    assert claim_args[3] == "HOLDING:inst-1"
    assert claim_args[5] == INTRADAY_ALERT_DEDUP_WINDOW


def _pool():
    return object()


# --------------------------------------------------------------------------- #
# Runner: INTRADAY fans out per member and skips gracefully without an alert
# --------------------------------------------------------------------------- #
class _RunnerConn:
    def __init__(self, jobs):
        self._jobs = jobs

    async def fetch(self, _sql, *_args, **_kwargs):
        return self._jobs


class _RunnerPoolCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return None


class _RunnerPool:
    def __init__(self, jobs):
        self._jobs = jobs

    def acquire(self):
        return _RunnerPoolCtx(_RunnerConn(self._jobs))

    def system_connection(self, *_a, **_k):
        return _RunnerPoolCtx(_RunnerConn(self._jobs))


def _harness():
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

    return captured, install


@pytest.mark.asyncio
async def test_intraday_phase_fans_out_per_member_and_skips_without_alert(
    monkeypatch,
) -> None:
    captured, install = _harness()
    install(monkeypatch)
    alerts = AsyncMock(return_value=None)  # no breach -> graceful skip
    monkeypatch.setattr(runner.intraday, "maybe_generate_intraday_alert", alerts)

    jobs = [{"id": "job-1", "tenant_id": "tenant-1", "job_name": "intraday"}]
    pool = _RunnerPool(jobs)
    now = datetime(2026, 9, 2, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    outcomes = await run_session(
        pool, Settings(_env_file=None, intraday_tracking_enabled=True), "INTRADAY", now
    )

    assert outcomes == {
        "tenant-1:user-1": "SKIPPED:INTRADAY_NO_ALERT",
        "tenant-1:user-2": "SKIPPED:INTRADAY_NO_ALERT",
    }
    assert alerts.await_count == 2
    # The bucket (1400) is part of the idempotency key, never a member UUID.
    assert [call.args[1].user_id for call in alerts.await_args_list] == [
        "user-1",
        "user-2",
    ]
    # No generic per-tenant generation for INTRADAY.
    captured["generate"].assert_not_awaited()


@pytest.mark.asyncio
async def test_intraday_phase_completes_when_alert_fires(monkeypatch) -> None:
    captured, install = _harness()
    install(monkeypatch)
    alerts = AsyncMock(return_value={"id": "alert-1"})
    monkeypatch.setattr(runner.intraday, "maybe_generate_intraday_alert", alerts)

    jobs = [{"id": "job-1", "tenant_id": "tenant-1", "job_name": "intraday"}]
    pool = _RunnerPool(jobs)
    now = datetime(2026, 9, 2, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    outcomes = await run_session(
        pool, Settings(_env_file=None, intraday_tracking_enabled=True), "INTRADAY", now
    )

    assert outcomes == {
        "tenant-1:user-1": "COMPLETED:report=alert-1",
        "tenant-1:user-2": "COMPLETED:report=alert-1",
    }
    # Each member runs under its own lease key (phase + 5-min bucket + audience).
    keys = {call.args[2] for call in runner.claim_job_run.call_args_list}
    assert "job:job-1:2026-09-02:INTRADAY:1400:user-1" in keys
    assert "job:job-1:2026-09-02:INTRADAY:1400:user-2" in keys


@pytest.mark.parametrize(
    ("key", "user_id"),
    [
        ("job:job-1:2026-09-02:INTRADAY:0930:user-1", "user-1"),
        ("job:job-1:2026-09-02:INTRADAY:1400:user-2", "user-2"),
        ("job:job-1:2026-09-02:INTRADAY:0930", None),
    ],
)
def test_intraday_business_key_separates_bucket_from_audience(key, user_id) -> None:
    assert runner.parse_business_job_key(key) == ("INTRADAY", user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", ["user-2", None])
async def test_intraday_recovery_preserves_member_or_legacy_owner(
    monkeypatch, audience
) -> None:
    _, install = _harness()
    install(monkeypatch)
    alerts = AsyncMock(return_value=None)
    monkeypatch.setattr(runner.intraday, "maybe_generate_intraday_alert", alerts)
    key = "job:job-1:2026-09-02:INTRADAY:1400"
    if audience is not None:
        key += f":{audience}"
    pool = _RunnerPool(
        [
            {
                "id": "job-1",
                "tenant_id": "tenant-1",
                "job_name": "intraday",
                "report_session": "INTRADAY",
                "job_key": key,
            }
        ]
    )

    outcomes = await runner.run_recoverable_jobs(
        pool, Settings(_env_file=None, intraday_tracking_enabled=True)
    )

    assert outcomes == {key: "SKIPPED:INTRADAY_NO_ALERT"}
    assert alerts.await_args.args[1].user_id == (audience or "owner-1")
