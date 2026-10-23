"""Authentication setup preserves refreshed tokens and never runs inference."""

import json
import os
from unittest.mock import AsyncMock

import pytest

from scripts import codex_admin, configure_codex
from scripts.configure_nas import generate_configuration


def cache(**overrides):
    return json.dumps({"auth_mode": "chatgpt", "tokens": {
        "access_token": "fixture-access", "refresh_token": "fixture-refresh"}, **overrides}).encode()


def test_import_only_creates_private_auth_file_and_never_overwrites(tmp_path):
    directory = tmp_path / "auth"
    codex_admin.import_auth(cache(), directory)
    target = directory / "auth.json"
    before = target.read_bytes()
    assert list(directory.iterdir()) == [target]
    with pytest.raises(FileExistsError):
        codex_admin.import_auth(cache(tokens={"access_token": "new", "refresh_token": "new"}), directory)
    assert target.read_bytes() == before
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("content", [b"not-json", b'{}', b'{"tokens":{},"tokens":{}}',
    b'x' * (codex_admin.MAX_AUTH_BYTES + 1), cache(OPENAI_API_KEY="fixture-key")],
    ids=["bad-json", "empty", "duplicate", "oversized", "api-key"])
def test_bad_or_api_key_auth_cache_is_rejected_without_writes(tmp_path, content):
    with pytest.raises(ValueError):
        codex_admin.import_auth(content, tmp_path / "auth")
    assert not (tmp_path / "auth").exists()


def test_check_only_calls_safe_preflight_and_sanitizes_failures(monkeypatch, capsys):
    check = AsyncMock(return_value={"version": "codex-cli fixture", "selectedModel": None})
    monkeypatch.setattr(codex_admin, "preflight_codex", check)
    assert codex_admin.main(["check"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    check.assert_awaited_once()
    check.side_effect = RuntimeError("sensitive auth payload")
    assert codex_admin.main(["check"]) == 1
    assert "sensitive" not in capsys.readouterr().err


def legacy_configuration(root):
    for name, text in {"env/api.env": "JWT_SECRET='keep-api'\nDATABASE_URL='keep-db'\n",
                       "env/worker.env": "WORKER_DATABASE_URL='keep-worker'\n",
                       "nas.env": "POSTGRES_ADMIN_USER='admin'\n"}.items():
        path = root / "deployment" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_upgrade_adds_runner_without_rotating_original_credentials(tmp_path):
    legacy_configuration(tmp_path)
    before = {p: p.read_bytes() for p in (tmp_path / "deployment").rglob("*.env")}
    paths = configure_codex.add_codex_configuration(tmp_path)
    for path, original in before.items():
        assert path.read_bytes().startswith(original)
    first = {p: p.read_bytes() for p in paths.values()}
    configure_codex.add_codex_configuration(tmp_path)
    assert {p: p.read_bytes() for p in paths.values()} == first
    runner = paths["codex"].read_text("utf-8")
    assert "DATABASE_URL" not in runner and "JWT_SECRET" not in runner


def test_fresh_configuration_is_unchanged_by_addon(tmp_path):
    paths = generate_configuration(tmp_path, web_base_url="https://wealth.example.test")
    before = {p: p.read_bytes() for p in paths.values()}
    configure_codex.add_codex_configuration(tmp_path)
    assert {p: p.read_bytes() for p in paths.values()} == before


def test_disagreeing_tokens_fail_before_changing_any_file(tmp_path):
    legacy_configuration(tmp_path)
    api = tmp_path / "deployment/env/api.env"
    worker = tmp_path / "deployment/env/worker.env"
    api.write_text(api.read_text() + "CODEX_RUNNER_TOKEN='" + "a" * 64 + "'\n")
    worker.write_text(worker.read_text() + "CODEX_RUNNER_TOKEN='" + "b" * 64 + "'\n")
    before = {p: p.read_bytes() for p in (tmp_path / "deployment").rglob("*.env")}
    with pytest.raises(ValueError, match="disagree"):
        configure_codex.add_codex_configuration(tmp_path)
    assert {p: p.read_bytes() for p in before} == before
    assert not (tmp_path / "deployment/env/codex.env").exists()
