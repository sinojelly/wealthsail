"""The daily AI pipeline cannot enable legacy reports or queued notifications."""

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.jobs import alpha_opportunities, outbox
from backend.jobs.session_switches import session_enabled


@pytest.mark.parametrize(
    "phase", ["PRE_MARKET", "PRE_MARKET_MACRO", "PRE_MARKET_PERSONALIZED"]
)
def test_ai_pipeline_does_not_enable_old_reports(phase):
    settings = Settings(_env_file=None, daily_research_enabled=True)
    assert session_enabled(settings, phase) is False
    assert session_enabled(
        Settings(_env_file=None, pre_market_reports_enabled=True), phase
    )


def test_morning_switch_defaults_and_start_window_are_explicit():
    settings = Settings(_env_file=None)
    assert settings.pre_market_reports_enabled is False
    assert settings.morning_opportunity_push_enabled is False
    assert settings.daily_research_start_window_minutes == 120
    for value in (29, 361):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, daily_research_start_window_minutes=value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "daily,push,expected",
    [(True, False, "DISABLED"), (False, True, "DISABLED"), (True, True, "NOT_READY")],
)
async def test_morning_push_requires_its_own_switch_and_ai_pipeline(
    monkeypatch, daily, push, expected
):
    monkeypatch.setattr(alpha_opportunities, "_active", AsyncMock(return_value=True))
    report = AsyncMock(return_value=None)
    monkeypatch.setattr(alpha_opportunities, "_today_report", report)
    settings = Settings(
        _env_file=None,
        daily_research_enabled=daily,
        morning_opportunity_push_enabled=push,
        fund_1430_enabled=False,
        opportunity_tenant_id="00000000-0000-0000-0000-000000000001",
        opportunity_user_id="00000000-0000-0000-0000-000000000002",
        wecom_opportunity_webhook_url="https://example.test/webhook",
    )
    result = await alpha_opportunities.sync_alpha_opportunities(
        object(),
        settings,
        datetime(2026, 10, 30, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert result["morning"]["status"] == expected
    assert report.await_count == int(daily and push)


class ClaimConnection:
    def __init__(self):
        self.sql, self.args = "", ()

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetch(self, sql, *args):
        self.sql, self.args = sql, args
        return []


class ClaimPool:
    def __init__(self):
        self.conn = ClaimConnection()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pytest.mark.asyncio
async def test_disabled_legacy_messages_are_filtered_before_claim_limit():
    pool = ClaimPool()
    await outbox.claim_batch(
        pool,
        worker_id="worker",
        limit=20,
        lease_seconds=60,
        settings=Settings(_env_file=None, daily_research_enabled=True),
    )
    selection = pool.conn.sql.split("LIMIT")[0]
    assert "REPORT_PUBLISHED" in selection
    assert "PRE_MARKET_PERSONALIZED" in selection
    assert "PRE_MARKET_MACRO" in selection
    assert "reporting.reports" in selection
    assert pool.conn.args[-1] is False
    assert "DELETE" not in pool.conn.sql.upper()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,extra",
    [
        ({"session": "PRE_MARKET"}, {}),
        ({"variant": "PRE_MARKET_MACRO"}, {}),
        ({"report_variant": "PRE_MARKET_PERSONALIZED"}, {}),
        (
            {},
            {
                "aggregate_type": "reporting.reports",
                "idempotency_key": "PRE_MARKET:2026-10-29:1:report:binding:x",
            },
        ),
    ],
)
async def test_previously_claimed_legacy_messages_are_released_without_sending(
    monkeypatch, payload, extra
):
    row = {
        "id": "outbox",
        "claim_token": "lease",
        "topic": "REPORT_PUBLISHED",
        "payload": payload,
        **extra,
    }
    monkeypatch.setattr(outbox, "mark_stale_sending_unknown", AsyncMock(return_value=0))
    monkeypatch.setattr(outbox, "claim_batch", AsyncMock(return_value=[row]))
    binding = AsyncMock()
    send = AsyncMock()
    release = AsyncMock()
    monkeypatch.setattr(outbox, "_load_binding", binding)
    monkeypatch.setattr(outbox, "_send", send)
    monkeypatch.setattr(outbox, "_cas_suppressed", release, raising=False)
    await outbox.deliver_batch(
        object(),
        Settings(_env_file=None, wecom_enabled=True, daily_research_enabled=True),
    )
    binding.assert_not_awaited()
    send.assert_not_awaited()
    release.assert_awaited_once()


def test_other_report_and_alert_notifications_remain_independent():
    settings = Settings(
        _env_file=None,
        fund_1430_enabled=True,
        post_market_review_enabled=True,
        intraday_tracking_enabled=True,
    )
    for session in ("FUND_1430", "POST_MARKET", "INTRADAY"):
        assert session_enabled(settings, session)
        assert outbox.notification_enabled(
            {"topic": "REPORT_PUBLISHED", "payload": {"session": session}}, settings
        )
    assert outbox.notification_enabled(
        {"topic": "ALERT_EVENT", "payload": {"session": "PRE_MARKET"}}, settings
    )


@pytest.mark.asyncio
async def test_switch_change_during_binding_lookup_prevents_network_send(monkeypatch):
    settings = Settings(_env_file=None, pre_market_reports_enabled=True)
    row = {
        "id": "outbox",
        "claim_token": "lease",
        "topic": "REPORT_PUBLISHED",
        "payload": {"session": "PRE_MARKET"},
    }

    async def load_binding(*_args):
        settings.pre_market_reports_enabled = False
        return {}

    monkeypatch.setattr(outbox, "mark_stale_sending_unknown", AsyncMock(return_value=0))
    monkeypatch.setattr(outbox, "claim_batch", AsyncMock(return_value=[row]))
    monkeypatch.setattr(outbox, "_load_binding", load_binding)
    monkeypatch.setattr(
        outbox, "build_delivery_command", lambda *_args, **_kwargs: object()
    )
    send, release = AsyncMock(), AsyncMock()
    monkeypatch.setattr(outbox, "_send", send)
    monkeypatch.setattr(outbox, "_cas_suppressed", release)
    await outbox.deliver_batch(object(), settings)
    send.assert_not_awaited()
    release.assert_awaited_once()


@pytest.mark.parametrize("service", ["api", "worker"])
def test_generated_nas_env_keeps_new_notifications_disabled(tmp_path, service):
    from dotenv import dotenv_values
    from scripts.configure_nas import generate_configuration

    values = dotenv_values(
        generate_configuration(tmp_path, web_base_url="https://wealth.example.test")[
            service
        ]
    )
    assert values["PRE_MARKET_REPORTS_ENABLED"] == "false"
    assert values["MORNING_OPPORTUNITY_PUSH_ENABLED"] == "false"
    assert values["DAILY_RESEARCH_START_WINDOW_MINUTES"] == "120"


@pytest.mark.asyncio
async def test_morning_switch_is_rechecked_after_async_reads_before_webhook(
    monkeypatch,
):
    settings = Settings(
        _env_file=None,
        daily_research_enabled=True,
        morning_opportunity_push_enabled=True,
        opportunity_tenant_id="00000000-0000-0000-0000-000000000001",
        opportunity_user_id="00000000-0000-0000-0000-000000000002",
        wecom_opportunity_webhook_url="https://example.test/webhook",
    )
    now = datetime(2026, 10, 30, 7, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(alpha_opportunities, "_active", AsyncMock(return_value=True))
    monkeypatch.setattr(
        alpha_opportunities,
        "_today_report",
        AsyncMock(return_value={"payload": {}, "run_id": "report"}),
    )

    async def known(*_args, **_kwargs):
        settings.morning_opportunity_push_enabled = False
        return []

    async def slot(_pool, _scope, _day, _slot, _now, action, _provider):
        state, result = await action()
        return {"status": state, **result}

    monkeypatch.setattr(alpha_opportunities, "_known_public_plans", known)
    monkeypatch.setattr(alpha_opportunities, "_run_slot", slot)
    send = AsyncMock()
    monkeypatch.setattr(alpha_opportunities, "send_opportunity_webhook", send)
    result = await alpha_opportunities.sync_alpha_opportunities(
        object(), settings, now, now_provider=lambda: now
    )
    assert result["morning"]["status"] == "EXPIRED"
    send.assert_not_awaited()
