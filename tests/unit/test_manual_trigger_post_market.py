"""Safety tests for the one-shot POST_MARKET operator command."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import manual_trigger_post_market as command


def test_manual_trigger_loads_from_its_repository_location(tmp_path: Path) -> None:
    """The server checkout must not depend on one developer's absolute path."""

    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "manual_trigger_post_market.py"
    probe = (
        "import runpy; "
        f"runpy.run_path({str(script)!r}, run_name='manual_trigger_import_check')"
    )

    result = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr


class FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_main_activates_worker_role_before_running_session(
    monkeypatch,
    capsys,
) -> None:
    """A NOINHERIT login must never reach the privileged runner directly."""

    settings = SimpleNamespace(
        worker_database_url="postgresql://worker-login:secret@db.example/postgres",
        db_statement_cache_size=0,
        is_production=True,
    )
    pool = FakePool()
    observed: dict[str, object] = {}

    def validate(candidate, *, process):
        observed["validation"] = (candidate, process)
        return candidate

    async def create_pool(
        dsn,
        *,
        min_size,
        max_size,
        statement_cache_size,
        runtime_role,
    ):
        observed["pool"] = {
            "dsn": dsn,
            "minSize": min_size,
            "maxSize": max_size,
            "statementCacheSize": statement_cache_size,
            "runtimeRole": runtime_role,
        }
        return pool

    async def verify(candidate, *, process, skip):
        observed["boundary"] = (candidate, process, skip)

    async def run_session(candidate, candidate_settings, session, _now):
        observed["run"] = (candidate, candidate_settings, session)
        return {"tenant-1": "COMPLETED"}

    monkeypatch.setattr(command, "get_settings", lambda: settings)
    monkeypatch.setattr(command, "validate_runtime_settings", validate, raising=False)
    monkeypatch.setattr(command, "create_pool", create_pool)
    monkeypatch.setattr(
        command,
        "verify_runtime_login_boundary",
        verify,
        raising=False,
    )
    monkeypatch.setattr(command.runner, "run_session", run_session)

    await command.main()

    assert observed["validation"] == (settings, "worker")
    assert observed["pool"] == {
        "dsn": settings.worker_database_url,
        "minSize": 1,
        "maxSize": 2,
        "statementCacheSize": 0,
        "runtimeRole": "wealthsail_worker",
    }
    assert observed["boundary"] == (pool, "worker", False)
    assert observed["run"] == (pool, settings, "POST_MARKET")
    assert "POST_MARKET_OUTCOMES={'tenant-1': 'COMPLETED'}" in capsys.readouterr().out
    assert pool.closed is True
