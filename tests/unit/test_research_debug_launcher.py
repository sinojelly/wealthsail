"""Local operators explicitly queue a refresh; ordinary startup does not."""

import json
from pathlib import Path
from types import SimpleNamespace

from scripts import local_dev


def test_local_debug_command_preserves_api_config_and_uses_running_database(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    (tmp_path / "secrets.json").write_text(
        json.dumps({"postgres_password": "old-db", "api_password": "old-api"}),
        encoding="utf8",
    )
    (tmp_path / "api.env").write_text("keep", encoding="utf8")
    monkeypatch.setattr(
        local_dev, "read_state", lambda: {"phase": "ready", "db_port": 55433}
    )
    monkeypatch.setattr(local_dev, "state_alive", lambda *a: True)
    monkeypatch.setattr(local_dev, "select_python", lambda *a, **k: Path("python.exe"))
    calls = []
    monkeypatch.setattr(
        local_dev, "run", lambda command, env, **kwargs: calls.append((command, env))
    )
    local_dev.refresh_local_research(
        SimpleNamespace(
            db_port=55432, research_request_key="12345678-1234-1234-1234-123456789abc"
        ),
        {"PATH": "tools"},
    )
    assert "--initialize" in calls[0][0] and calls[0][0][-1] == "55433"
    assert calls[1][0][1:3] == ["-m", "backend.jobs.research_debug"]
    assert "--request-key" in calls[1][0] and "--email" in calls[1][0]
    assert calls[1][1]["WEALTHSAIL_ENV_FILE"] == str(tmp_path / "macro.env")
    assert (tmp_path / "api.env").read_text() == "keep"
    assert "DAILY_RESEARCH_DEBUG_ENABLED=true" in (tmp_path / "macro.env").read_text()


def test_local_configuration_exposes_same_explicit_debug_capability_to_api_and_macro(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    args = SimpleNamespace(db_port=55432, api_port=8000, web_port=5173)
    local_dev.configure(args)
    assert "DAILY_RESEARCH_DEBUG_ENABLED=true" in (tmp_path / "api.env").read_text()
    assert "DAILY_RESEARCH_DEBUG_ENABLED=true" in (tmp_path / "macro.env").read_text()
