"""Daily research is global, calendar-based and never retried after dispatch."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from importlib import import_module, util
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 17, 9, tzinfo=SHANGHAI)  # Saturday


def job():
    assert util.find_spec("backend.jobs.daily_research"), "daily execution job missing"
    return import_module("backend.jobs.daily_research")


def settings(**updates):
    return SimpleNamespace(
        **{
            "daily_research_enabled": True,
            "daily_research_catch_up": True,
            "daily_research_codex_path": "codex.exe",
            "daily_research_model": None,
            "daily_research_timeout_seconds": 900,
            **updates,
        }
    )


def isolate(monkeypatch):
    from backend.investment import discovery

    monkeypatch.setattr(
        discovery,
        "collect_discovery_nav_histories",
        AsyncMock(return_value={"fundHistories": [], "themeNavSourceResults": {}}),
    )
    mod, state = job(), {"consumed": False, "active": 0, "terminal": []}

    @asynccontextmanager
    async def connection(pool):
        state["active"] += 1
        try:
            yield object()
        finally:
            state["active"] -= 1

    async def claim(conn, stamp, packet, **kwargs):
        if state["consumed"]:
            return None
        state["consumed"] = True
        return {"run_id": "one"}

    async def finished(conn, run_id, stamp, **kwargs):
        state["terminal"].append(kwargs)

    async def provider(*args, **kwargs):
        assert state["active"] == 0, "no provider runs inside a DB connection"
        return {"summary": "public", "sources": []}

    monkeypatch.setattr(mod, "system_connection", connection)
    monkeypatch.setattr(
        mod, "consumed", AsyncMock(side_effect=lambda *_: state["consumed"])
    )
    monkeypatch.setattr(mod, "claim_daily_run", claim)
    monkeypatch.setattr(mod, "finish_daily_run", finished)
    monkeypatch.setattr(
        mod, "load_public_packet", AsyncMock(return_value={"asOf": NOW.isoformat()})
    )
    monkeypatch.setattr(mod, "collect_daily_sources", AsyncMock(side_effect=provider))
    monkeypatch.setattr(
        mod,
        "preflight_codex",
        AsyncMock(
            return_value={"executable": "codex.exe", "version": "codex-cli test"}
        ),
    )
    monkeypatch.setattr(mod, "run_codex_research", AsyncMock(side_effect=provider))
    monkeypatch.setattr(mod, "verify_research_sources", AsyncMock(side_effect=provider))
    monkeypatch.setattr(mod, "build_prompt", lambda packet: "one prompt")
    monkeypatch.setattr(mod, "validate_report", lambda raw, *_, **__: raw)
    monkeypatch.setattr(mod, "now_shanghai", lambda: NOW)
    return mod, state


@pytest.mark.asyncio
async def test_daily_waits_for_concurrent_public_batch_before_consuming_model_slot(
    monkeypatch,
):
    from backend.jobs import alpha_public

    mod, state = isolate(monkeypatch)

    class Conn:
        async def fetchrow(self, *args):
            return {"status": "CLAIMED", "claimed_at": NOW}

    @asynccontextmanager
    async def connection(pool):
        yield Conn()

    monkeypatch.setattr(mod, "system_connection", connection)
    monkeypatch.setattr(
        alpha_public, "sync_alpha_public", AsyncMock(return_value={"status": "SKIPPED"})
    )
    result = await mod.sync_daily_research(
        object(), settings(alpha_public_enabled=True), NOW
    )
    assert result["status"] == "WAITING_PUBLIC_DATA"
    assert state["consumed"] is False
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_start_catches_up_once_on_weekend_and_restart_does_not_dispatch(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)
    first = await mod.sync_daily_research(object(), settings(), NOW)
    second = await mod.sync_daily_research(
        object(), settings(), NOW + timedelta(hours=3)
    )
    assert first["status"] == "READY" and second["status"] == "SKIPPED"
    assert state["consumed"]
    mod.run_codex_research.assert_awaited_once()
    assert state["terminal"][0]["status"] == "READY"


@pytest.mark.asyncio
async def test_disabled_or_before_0830_never_collects_or_dispatches(monkeypatch):
    mod, _ = isolate(monkeypatch)
    assert (
        await mod.sync_daily_research(
            object(), settings(daily_research_enabled=False), NOW
        )
    )["status"] == "DISABLED"
    assert (
        await mod.sync_daily_research(
            object(), settings(), NOW.replace(hour=8, minute=29)
        )
    )["status"] == "NOT_DUE"
    mod.collect_daily_sources.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception,status",
    [
        (TimeoutError, "UNCERTAIN"),
        (asyncio.CancelledError, "UNCERTAIN"),
        (RuntimeError, "FAILED"),
    ],
)
async def test_failed_or_cancelled_model_keeps_consumed_slot(
    monkeypatch, exception, status
):
    mod, state = isolate(monkeypatch)
    mod.run_codex_research.side_effect = exception()
    if exception is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            await mod.sync_daily_research(object(), settings(), NOW)
    else:
        assert (await mod.sync_daily_research(object(), settings(), NOW))[
            "status"
        ] == status
    assert state["terminal"][0]["status"] == status
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "SKIPPED"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_cli_unavailable_is_terminal_and_does_not_fallback_or_retry(monkeypatch):
    from backend.integrations.codex_research import CodexUnavailableError

    mod, state = isolate(monkeypatch)
    mod.preflight_codex.side_effect = CodexUnavailableError()
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "FAILED"
    assert state["consumed"]
    mod.run_codex_research.assert_not_awaited()
    assert state["terminal"][0]["error_class"] == "CodexUnavailableError"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_public_model_result_is_audited_before_validation_without_retry(
    monkeypatch, invalid
):
    mod, state = isolate(monkeypatch)
    records = []

    def audit(settings, run_id, **record):
        records.append(record)
        return False  # an unavailable diagnostic directory is nonfatal

    def validate(raw, *args, **kwargs):
        assert records[-1]["stage"] == "SOURCES_CHECKED"
        if invalid:
            raise ValueError("unknown public direction key")
        return raw

    monkeypatch.setattr(mod, "write_research_audit", audit, raising=False)
    monkeypatch.setattr(mod, "validate_report", validate)
    outcome = await mod.sync_daily_research(object(), settings(), NOW)
    assert outcome["status"] == ("FAILED" if invalid else "READY")
    assert [r["stage"] for r in records] == [
        "MODEL_RETURNED",
        "SOURCES_CHECKED",
        "VALIDATION_FAILED" if invalid else "READY",
    ]
    assert all(r["raw"] == {"summary": "public", "sources": []} for r in records)
    assert records[0].get("source_checks") is None
    assert records[-1]["source_checks"] == {"summary": "public", "sources": []}
    assert isinstance(records[-1].get("error"), ValueError) is invalid
    assert state["terminal"][0]["error_class"] == ("ValueError" if invalid else None)
    assert (await mod.sync_daily_research(object(), settings(), NOW))[
        "status"
    ] == "SKIPPED"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_collection_crossing_midnight_does_not_dispatch_before_next_0830(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)
    monkeypatch.setattr(
        mod, "now_shanghai", lambda: NOW.replace(day=18, hour=0, minute=1)
    )
    assert (
        await mod.sync_daily_research(
            object(), settings(), NOW.replace(hour=23, minute=59)
        )
    )["status"] == "SKIPPED"
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "catchup,expected", [(True, NOW), (False, NOW.replace(day=18, hour=8, minute=30))]
)
async def test_service_catchup_policy_and_next_shanghai_day(
    monkeypatch, catchup, expected
):
    mod = job()
    stamp, delays, calls = NOW.astimezone(timezone.utc), [], []
    stop = asyncio.Event()

    def clock():
        return stamp

    async def wait(event, seconds):
        nonlocal stamp
        delays.append(seconds)
        stamp += timedelta(seconds=seconds)
        if len(delays) == 2:
            event.set()

    async def sync(*args):
        calls.append(args[2])
        return {"status": "SKIPPED"}

    monkeypatch.setattr(mod, "sync_daily_research", sync)
    await mod.run_daily_research_service(
        object(),
        settings(daily_research_catch_up=catchup),
        stop_event=stop,
        now_provider=clock,
        wait=wait,
    )
    assert calls[0] == expected
    assert all(seconds > 0 for seconds in delays)


@pytest.mark.asyncio
async def test_service_waits_one_minute_for_inflight_public_cache_then_uses_same_day(
    monkeypatch,
):
    mod = job()
    stamp = NOW
    stop = asyncio.Event()
    sync = AsyncMock(
        side_effect=[{"status": "WAITING_PUBLIC_DATA"}, {"status": "READY"}]
    )
    delays = []

    async def wait(event, seconds):
        nonlocal stamp
        delays.append(seconds)
        stamp += timedelta(seconds=seconds)
        if len(delays) == 2:
            event.set()

    monkeypatch.setattr(mod, "sync_daily_research", sync)
    await mod.run_daily_research_service(
        object(), settings(), stop_event=stop, now_provider=lambda: stamp, wait=wait
    )
    assert [call.args[2] for call in sync.await_args_list] == [
        NOW,
        NOW + timedelta(minutes=1),
    ]
    assert delays[0] == 60
    assert delays[1] > 3600
