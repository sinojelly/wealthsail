"""Local launcher must never pick up a developer's remote database/secrets."""

import pytest
import zipfile

from scripts.local_dev import child_environment, node_supported, validate_local_target
from scripts.local_postgres import extract_postgres


def test_local_generated_api_and_worker_keep_the_selected_research_schedule(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from dotenv import dotenv_values
    from scripts import local_dev
    from backend.integrations import codex_research

    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(codex_research, "resolve_codex_executable", lambda *_: None)
    monkeypatch.setenv("DAILY_RESEARCH_SCHEDULE_MODE", "WEEKDAYS")
    monkeypatch.setenv("DAILY_RESEARCH_SCHEDULE_TIME", "08:15")
    monkeypatch.setenv("DAILY_RESEARCH_PIPELINE_TIMEOUT_SECONDS", "9000")
    local_dev.configure(SimpleNamespace(db_port=55432, web_port=5173))
    for filename in ("api.env", "macro.env"):
        settings = dotenv_values(tmp_path / filename)
        assert settings["DAILY_RESEARCH_SCHEDULE_MODE"] == "WEEKDAYS"
        assert settings["DAILY_RESEARCH_SCHEDULE_TIME"] == "08:15"
        assert settings["DAILY_RESEARCH_PIPELINE_TIMEOUT_SECONDS"] == "9000"


def test_local_fixture_accepts_only_its_dedicated_loopback_database():
    validate_local_target("postgresql://postgres:secret@127.0.0.1:55432/wealthsail_local")
    for dsn in (
        "postgresql://postgres:secret@db.example/wealthsail_local",
        "postgresql://postgres:secret@127.0.0.1/postgres",
        "postgresql://wealthsail_api_login:secret@127.0.0.1/wealthsail_local",
        "sqlite:///wealthsail_local",
    ):
        with pytest.raises(ValueError):
            validate_local_target(dsn)


def test_child_environment_ignores_remote_authority_and_frontend_overrides(tmp_path):
    env = child_environment(
        {"PATH": "tools", "SYSTEMROOT": "windows", "DATABASE_URL": "remote-secret",
         "MODEL_API_KEY": "paid-key", "VITE_API_BASE_URL": "https://real-api.example",
         "WEALTHSAIL_ENV_FILE": "production.env"},
        tmp_path,
    )
    assert env["PATH"] == "tools"
    assert env["SYSTEMROOT"] == "windows"
    assert "DATABASE_URL" not in env
    assert "MODEL_API_KEY" not in env
    assert "VITE_API_BASE_URL" not in env
    assert "WEALTHSAIL_ENV_FILE" not in env
    assert env["TEMP"] == str(tmp_path / "tmp")


@pytest.mark.parametrize("version,expected", [
    ("v20.19.0", False), ("v22.11.0", False), ("v22.12.0", True),
    ("v24.12.0", True),
])
def test_launcher_requires_supported_node(version, expected):
    assert node_supported(version) is expected


def test_settings_can_select_an_isolated_env_file(tmp_path, monkeypatch):
    from backend.config import get_settings

    env_file = tmp_path / "api.env"
    env_file.write_text("DATABASE_URL=local-only\nAPP_ENV=development\n", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("WEALTHSAIL_ENV_FILE", str(env_file))
    get_settings.cache_clear()
    try:
        assert get_settings().database_url == "local-only"
        assert get_settings().app_env == "development"
    finally:
        get_settings.cache_clear()


def test_portable_archive_keeps_runtime_only_and_rejects_path_escape(tmp_path):
    archive = tmp_path / "postgres.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("pgsql/bin/postgres.exe", "runtime")
        handle.writestr("pgsql/lib/plugin.dll", "extension")
        handle.writestr("pgsql/lib/postgres.lib", "static-build-library")
        handle.writestr("pgsql/share/postgresql.conf.sample", "config")
        handle.writestr("pgsql/include/postgres.h", "headers")
        handle.writestr("pgsql/bin/postgres.pdb", "debug-symbols")
    target = tmp_path / "portable"
    extract_postgres(archive, target)
    assert (target / "pgsql/bin/postgres.exe").read_text() == "runtime"
    assert (target / "pgsql/lib/plugin.dll").exists()
    assert (target / "pgsql/share/postgresql.conf.sample").exists()
    assert not (target / "pgsql/include").exists()
    assert not (target / "pgsql/bin/postgres.pdb").exists()
    assert not (target / "pgsql/lib/postgres.lib").exists()
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("pgsql/bin/../../../escaped.txt", "unsafe")
    with pytest.raises(ValueError, match="outside"):
        extract_postgres(archive, target)
    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.parametrize("skip_install", [False, True])
def test_runtime_configures_with_selected_python_after_installation(
    tmp_path, monkeypatch, skip_install
):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock

    from scripts import local_dev, local_postgres

    selected = tmp_path / "venv with spaces/Scripts/python.exe"
    settings = {"postgres_password": "fixture"}
    calls = []
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(local_dev, "VENV_PY", selected)
    monkeypatch.setattr(local_dev, "require_tools", lambda *_: None)
    monkeypatch.setattr(local_dev, "check_ports", lambda *_: None)
    monkeypatch.setattr(local_dev, "check_stop_request", lambda: None)
    monkeypatch.setattr(local_dev, "select_python", lambda *_args, **_kwargs: selected)
    monkeypatch.setattr(
        local_dev, "configure",
        Mock(side_effect=AssertionError("bootstrap Python has no installed dependencies")),
    )
    monkeypatch.setattr(local_postgres, "postgres_bin", lambda *_: Path("postgres/bin"))
    monkeypatch.setenv("DAILY_RESEARCH_ENABLED", "false")
    monkeypatch.setenv("DAILY_RESEARCH_SCHEDULE_TIME", "08:15")
    monkeypatch.setenv("CODEX_MODEL", "configured-local-model")
    monkeypatch.setenv("DATABASE_URL", "remote-authority-must-not-be-inherited")
    monkeypatch.setenv("MODEL_API_KEY", "private-key-must-not-be-inherited")

    def run(command, env, **_kwargs):
        calls.append(command)
        if "--configure" in command:
            assert command[0] == str(selected)
            pip = [str(selected), "-m", "pip", "install"]
            assert any(call[:4] == pip for call in calls[:-1]) is not skip_install
            assert env["DAILY_RESEARCH_SCHEDULE_TIME"] == "08:15"
            assert env["DAILY_RESEARCH_ENABLED"] == "false"
            assert env["CODEX_MODEL"] == "configured-local-model"
            assert "DATABASE_URL" not in env and "MODEL_API_KEY" not in env
            (tmp_path / "secrets.json").write_text(json.dumps(settings), encoding="utf-8")
        return ""

    monkeypatch.setattr(local_dev, "run", run)
    args = SimpleNamespace(
        db_port=55432, api_port=8000, web_port=5173,
        skip_install=skip_install, postgres_bin=None,
    )
    actual, binary_dir = local_dev.prepare_runtime(args, {"PATH": "tools"})
    assert actual == settings and binary_dir == Path("postgres/bin")
    configurations = [command for command in calls if "--configure" in command]
    assert len(configurations) == 1
    assert configurations[0][-4:] == ["--db-port", "55432", "--web-port", "5173"]
