"""Local market-data collection uses a separate restricted login and env file."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import local_dev

AUTOMATIC_SWITCHES = (
    "DAILY_RESEARCH_ENABLED",
    "FUND_1430_ENABLED",
    "POST_MARKET_REVIEW_ENABLED",
    "INTRADAY_TRACKING_ENABLED",
)


def args(**changes):
    values = {"web_port": 5173, "api_port": 8000, "db_port": 55432, "skip_macro": False}
    values.update(changes)
    return SimpleNamespace(**values)


def test_macro_configuration_preserves_existing_secrets_and_separates_api_authority(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    before = {
        "postgres_password": "old-db",
        "api_password": "old-api",
        "jwt": "old-jwt",
        "refresh": "old-refresh",
        "reset": "old-reset",
        "binding": "old-binding",
        "routing": "old-routing",
    }
    (tmp_path / "secrets.json").write_text(json.dumps(before), encoding="utf-8")
    first = local_dev.configure(args())
    assert all(first[key] == value for key, value in before.items())
    assert first["worker_password"] != first["api_password"]
    second = local_dev.configure(args())
    assert second["worker_password"] == first["worker_password"]
    api = (tmp_path / "api.env").read_text(encoding="utf-8")
    macro = (tmp_path / "macro.env").read_text(encoding="utf-8")
    assert "WORKER_DATABASE_URL" not in api and first["worker_password"] not in api
    assert (
        "wealthsail_worker_login:" in macro
        and "WORKER_DATABASE_POOL_MODE=direct" in macro
    )
    keys = {line.partition("=")[0] for line in macro.splitlines()}
    assert not keys.intersection(
        ("DATABASE_URL", "JWT_SECRET", "WECOM_CORP_SECRET", "MODEL_API_KEY")
    )


@pytest.mark.parametrize(
    "switch,value",
    [(None, None)]
    + [(name, value) for name in AUTOMATIC_SWITCHES for value in ("true", "false")],
)
def test_generated_local_automatic_switches_default_off_and_follow_launch_environment(
    tmp_path, monkeypatch, switch, value
):
    from dotenv import dotenv_values
    from backend.integrations import codex_research

    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(codex_research, "resolve_codex_executable", lambda *_: None)
    for name in AUTOMATIC_SWITCHES:
        monkeypatch.delenv(name, raising=False)
    if switch is not None:
        monkeypatch.setenv(switch, value)
    # Generated automatic flags follow this launch, rather than retaining
    # an old automatic opt-in from either service's previous environment file.
    previous = "\n".join(f"{name}=true" for name in AUTOMATIC_SWITCHES) + "\n"
    for service in ("api", "macro"):
        (tmp_path / f"{service}.env").write_text(previous, encoding="utf-8")

    local_dev.configure(args())

    expected = {name: "false" for name in AUTOMATIC_SWITCHES}
    if switch is not None:
        expected[switch] = value
    for service in ("api", "macro"):
        actual = dotenv_values(tmp_path / f"{service}.env")
        assert {name: actual.get(name) for name in AUTOMATIC_SWITCHES} == expected


def test_default_local_services_include_only_macro_collector_and_not_report_worker():
    commands = local_dev.service_commands(args())
    assert [name for name, _, _ in commands] == ["api", "frontend", "macro"]
    macro = commands[-1][1]
    assert macro[-2:] == ["-m", "backend.jobs.macro_worker"]
    assert "backend.worker" not in macro
    assert [
        name for name, _, _ in local_dev.service_commands(args(skip_macro=True))
    ] == ["api", "frontend"]


def test_macro_service_receives_its_own_env_and_leaves_api_env_selected(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    base = {"WEALTHSAIL_ENV_FILE": str(tmp_path / "api.env"), "PATH": "tools"}
    assert local_dev.service_environment("macro", base)["WEALTHSAIL_ENV_FILE"] == str(
        tmp_path / "macro.env"
    )
    assert local_dev.service_environment("api", base) == base
    assert base["WEALTHSAIL_ENV_FILE"] == str(tmp_path / "api.env")


def test_manual_macro_refresh_uses_running_local_db_port_without_rewriting_api_configuration(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    (tmp_path / "secrets.json").write_text(
        json.dumps({"postgres_password": "old-db", "api_password": "old-api"}),
        encoding="utf-8",
    )
    api = tmp_path / "api.env"
    api.write_text("keep existing runtime settings", encoding="utf-8")
    monkeypatch.setattr(
        local_dev, "read_state", lambda: {"db_port": 55433, "phase": "ready"}
    )
    monkeypatch.setattr(local_dev, "state_alive", lambda *_args: True)
    monkeypatch.setattr(
        local_dev, "select_python", lambda *_args, **_kwargs: Path("python.exe")
    )
    calls = []
    monkeypatch.setattr(
        local_dev, "run", lambda command, env, **kwargs: calls.append((command, env))
    )
    local_dev.refresh_local_macro(args(), {"PATH": "tools"})
    assert len(calls) == 2
    assert "--initialize" in calls[0][0]
    assert calls[0][0][-1] == "55433"
    assert calls[1][0][-3:] == ["-m", "backend.jobs.macro_worker", "--once"]
    assert calls[1][1]["WEALTHSAIL_ENV_FILE"] == str(tmp_path / "macro.env")
    assert "@127.0.0.1:55433/wealthsail_local" in (tmp_path / "macro.env").read_text(
        encoding="utf-8"
    )
    assert api.read_text(encoding="utf-8") == "keep existing runtime settings"


@pytest.mark.parametrize(
    "phase,alive", [("preparing", True), ("starting", True), ("ready", False)]
)
def test_manual_macro_refresh_rejects_stopped_or_transitioning_services_before_configuration_changes(
    tmp_path, monkeypatch, phase, alive
):
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    secret_file = tmp_path / "secrets.json"
    secret_file.write_text(
        json.dumps({"postgres_password": "old-db", "api_password": "old-api"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        local_dev, "read_state", lambda: {"phase": phase, "db_port": 55432}
    )
    monkeypatch.setattr(local_dev, "state_alive", lambda *_args: alive)
    monkeypatch.setattr(
        local_dev, "select_python", lambda *_args, **_kwargs: Path("python.exe")
    )
    calls = []
    monkeypatch.setattr(local_dev, "run", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(RuntimeError, match="ready"):
        local_dev.refresh_local_macro(args(), {"PATH": "tools"})
    assert not calls
    assert not (tmp_path / "macro.env").exists()
    assert "worker_password" not in json.loads(secret_file.read_text(encoding="utf-8"))
