"""Daily service lifecycle and local configuration, without model calls."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.config import Settings
from scripts import local_dev


def test_local_explicitly_enables_research_with_native_executable_only(
    tmp_path, monkeypatch
):
    from backend.integrations import codex_research

    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(
        codex_research,
        "resolve_codex_executable",
        lambda *_args, **_kw: "C:/Public/Codex/codex.exe",
    )
    local_dev.configure_macro({"worker_password": "fixture"}, 55432)
    content = (tmp_path / "macro.env").read_text("utf-8")
    assert "DAILY_RESEARCH_ENABLED=true" in content
    assert "C:/Public/Codex/codex.exe" in content
    assert "codex.cmd" not in content


def test_api_display_uses_enabled_flag_without_cli_or_worker_authority(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    local_dev.configure(SimpleNamespace(web_port=5173, db_port=55432))
    content = (tmp_path / "api.env").read_text("utf-8")
    assert "DAILY_RESEARCH_ENABLED=true" in content
    assert "DAILY_RESEARCH_CODEX_PATH" not in content
    assert "WORKER_DATABASE_URL" not in content


@pytest.mark.asyncio
async def test_macro_worker_cancels_and_awaits_daily_service_before_return(monkeypatch):
    from backend.jobs import macro_worker

    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def daily(pool, settings, *, stop_event):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    async def macro(pool, *, stop_event):
        await entered.wait()

    monkeypatch.setattr(
        macro_worker, "run_daily_research_service", daily, raising=False
    )
    monkeypatch.setattr(macro_worker, "run_macro_service", macro)
    for name in (
        "run_macro_evidence_service",
        "run_opportunity_market_service",
        "run_opportunity_monitor_service",
        "run_index_research_service",
    ):
        monkeypatch.setattr(macro_worker, name, AsyncMock())
    # `_serve` must receive the same explicit settings as main, not reload .env.
    assert "settings" in inspect.signature(macro_worker._serve).parameters
    await asyncio.wait_for(macro_worker._serve(object(), Settings(_env_file=None)), 1)
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_macro_once_never_starts_daily_research(monkeypatch, capsys):
    from backend.jobs import macro_worker

    settings = Settings(
        _env_file=None,
        worker_database_url="postgresql://worker@localhost/db",
        worker_database_pool_mode="direct",
        daily_research_enabled=True,
    )
    pool = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(macro_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(macro_worker, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(macro_worker, "verify_runtime_login_boundary", AsyncMock())
    monkeypatch.setattr(
        macro_worker,
        "refresh_macro_history",
        AsyncMock(return_value={"status": "COMPLETED"}),
    )
    monkeypatch.setattr(
        macro_worker,
        "sync_macro_evidence",
        AsyncMock(return_value={"status": "COMPLETED"}),
    )
    monkeypatch.setattr(
        macro_worker,
        "sync_opportunity_market",
        AsyncMock(return_value={"status": "COMPLETED"}),
    )
    daily = AsyncMock()
    monkeypatch.setattr(
        macro_worker, "run_daily_research_service", daily, raising=False
    )
    assert await macro_worker.main(["--once"]) == 0
    daily.assert_not_awaited()


def test_migration_readback_checks_daily_append_only_boundary():
    from scripts.migrate import _readback

    source = inspect.getsource(_readback)
    assert "daily research boundary is missing or unsafe" in source
    assert "research.daily_research_runs" in source
    assert "research.daily_research_results" in source
    assert "daily_research_runs_slot_date_key" in source
