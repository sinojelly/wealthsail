"""NAS credentials must survive generation without depending on Supabase."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from dotenv import dotenv_values


def test_generated_configuration_separates_runtime_credentials(tmp_path: Path) -> None:
    from scripts.configure_nas import generate_configuration

    paths = generate_configuration(tmp_path, web_base_url="https://wealth.example.test")
    api = dotenv_values(paths["api"])
    worker = dotenv_values(paths["worker"])
    nas = dotenv_values(paths["nas"])
    api_db = urlsplit(api["DATABASE_URL"])
    worker_db = urlsplit(worker["WORKER_DATABASE_URL"])
    assert (api_db.hostname, api_db.port, api_db.path) == ("postgres", 5432, "/wealthsail")
    assert api_db.username == "wealthsail_api_login"
    assert worker_db.username == "wealthsail_worker_login"
    assert api_db.password != worker_db.password
    assert parse_qs(api_db.query) == {"sslmode": ["disable"]}
    assert worker["WORKER_DATABASE_POOL_MODE"] == "direct"
    assert api["WECOM_ENABLED"] == worker["WECOM_ENABLED"] == "false"
    assert api["PASSWORD_RESET_EMAIL_ENABLED"] == "false"
    assert api["ROUTING_ENCRYPTION_KEY"] == worker["ROUTING_ENCRYPTION_KEY"]
    assert "JWT_SECRET" not in worker and "DATABASE_URL" not in worker
    assert len({api[key] for key in (
        "JWT_SECRET", "REFRESH_TOKEN_HMAC_KEY", "PASSWORD_RESET_HMAC_KEY", "BINDING_HMAC_KEY"
    )}) == 4
    assert nas["POSTGRES_NETWORK_NAME"] == "global-postgres-network"
    assert nas["POSTGRES_ADMIN_USER"] == "admin"
    assert nas["WEB_BIND_ADDRESS"] == "127.0.0.1"
    assert len(api_db.password) >= 64


def test_configuration_rerun_never_rotates_existing_secrets(tmp_path: Path) -> None:
    from scripts.configure_nas import generate_configuration

    paths = generate_configuration(tmp_path, web_base_url="https://wealth.example.test")
    before = {name: path.read_bytes() for name, path in paths.items()}
    with pytest.raises(FileExistsError):
        generate_configuration(tmp_path, web_base_url="https://other.example.test")
    assert {name: path.read_bytes() for name, path in paths.items()} == before


@pytest.mark.parametrize("url", [
    "http://nas:8080", "https://", "https://nas/path", "https://nas?q=x",
    "https://exa'mple.test", "https://@nas", "https://:@nas", "https://bad_host.test",
])
def test_invalid_public_origin_writes_nothing(tmp_path: Path, url: str) -> None:
    from scripts.configure_nas import generate_configuration

    with pytest.raises(ValueError, match="HTTPS"):
        generate_configuration(tmp_path, web_base_url=url)
    assert not list(tmp_path.rglob("*.env"))


@pytest.mark.parametrize("database", ["postgres", "template0", "template1", "prismscore_db", 'bad"name'])
def test_generator_refuses_system_or_other_project_databases(tmp_path: Path, database: str) -> None:
    from scripts.configure_nas import generate_configuration

    with pytest.raises(ValueError, match="database"):
        generate_configuration(tmp_path, web_base_url="https://wealth.example.test", database=database)


def test_partial_existing_configuration_does_not_create_other_files(tmp_path: Path) -> None:
    from scripts.configure_nas import generate_configuration

    existing = tmp_path / "deployment/env/worker.env"
    existing.parent.mkdir(parents=True)
    existing.write_text("operator-managed\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        generate_configuration(tmp_path, web_base_url="https://wealth.example.test")
    assert existing.read_text("utf-8") == "operator-managed\n"
    assert not (existing.parent / "api.env").exists()


def test_generated_envs_pass_production_validation(tmp_path: Path) -> None:
    from backend.config import Settings, validate_runtime_settings
    from scripts.configure_nas import generate_configuration

    paths = generate_configuration(tmp_path, web_base_url="https://wealth.example.test")
    for process in ("api", "worker"):
        settings = Settings(_env_file=paths[process])
        assert validate_runtime_settings(settings, process=process) is settings
