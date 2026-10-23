"""Installation guards run before any connection or schema mutation."""

from urllib.parse import unquote, urlsplit

import pytest

API_DSN = "postgresql://wealthsail_api_login:api-secret@postgres:5432/wealthsail?sslmode=disable"
WORKER_DSN = "postgresql://wealthsail_worker_login:worker-secret@postgres:5432/wealthsail?sslmode=disable"


def test_validate_target_accepts_direct_nas_database() -> None:
    from scripts.initialize_postgres import validate_runtime_targets

    target = validate_runtime_targets(API_DSN, WORKER_DSN, "postgres:5432/wealthsail")
    assert target.database == "wealthsail"
    assert target.display == "postgres:5432/wealthsail"


@pytest.mark.parametrize("api,worker,confirmation", [
    (API_DSN, WORKER_DSN, "postgres:5432/prismscore_db"),
    (API_DSN.replace("/wealthsail?", "/prismscore_db?"), WORKER_DSN, "postgres:5432/wealthsail"),
    (API_DSN, WORKER_DSN.replace("@postgres:", "@other:"), "postgres:5432/wealthsail"),
    (API_DSN.replace("wealthsail_api_login:", "admin:"), WORKER_DSN, "postgres:5432/wealthsail"),
    (API_DSN, WORKER_DSN.replace("worker-secret", "api-secret"), "postgres:5432/wealthsail"),
    (API_DSN.replace("postgresql:", "sqlite:"), WORKER_DSN, "postgres:5432/wealthsail"),
])
def test_bad_runtime_target_is_rejected_without_connecting(api, worker, confirmation) -> None:
    from scripts.initialize_postgres import validate_runtime_targets

    with pytest.raises(ValueError):
        validate_runtime_targets(api, worker, confirmation)


def test_admin_password_is_url_encoded_without_changing_host_or_target() -> None:
    from scripts.initialize_postgres import validate_runtime_targets

    target = validate_runtime_targets(API_DSN, WORKER_DSN, "postgres:5432/wealthsail")
    password = "test-only@:/#' secret"
    parsed = urlsplit(target.admin_dsn("admin", password, maintenance=True))
    assert unquote(parsed.password) == password
    assert parsed.hostname == "postgres"
    assert parsed.path == "/postgres"
    assert "admin" not in target.display and password not in target.display


@pytest.mark.asyncio
async def test_wrong_confirmation_fails_before_a_connection(monkeypatch) -> None:
    from scripts import initialize_postgres as installer

    async def no_connection(*_args, **_kwargs):
        pytest.fail("target guard must run before connecting")

    monkeypatch.setattr(installer.asyncpg, "connect", no_connection)
    with pytest.raises(ValueError):
        await installer.initialize_database(
            API_DSN, WORKER_DSN, admin_user="admin", admin_password="private",
            confirm_target="other:5432/wealthsail",
        )


@pytest.mark.asyncio
async def test_unknown_cluster_roles_are_rejected_before_create_database(monkeypatch) -> None:
    from scripts import initialize_postgres as installer

    class Maintenance:
        statements = []

        async def execute(self, sql, *_args):
            self.statements.append(sql)

        async def fetchval(self, sql, *_args):
            if "rolsuper" in sql:
                return True
            if "server_version_num" in sql:
                return "180000"
            if "pg_database" in sql:
                return False
            if "authenticated" in sql:
                return False
            raise AssertionError(sql)

        async def fetch(self, sql, *_args):
            if "wealthsail" in sql or "rolname = ANY" in sql:
                return [{"rolname": "wealthsail_api"}]
            return []

        async def close(self):
            pass

    connection = Maintenance()

    async def connect(_dsn):
        return connection

    monkeypatch.setattr(installer.asyncpg, "connect", connect)
    with pytest.raises(RuntimeError, match="role.*conflict"):
        await installer.initialize_database(
            API_DSN, WORKER_DSN, admin_user="admin", admin_password="private",
            confirm_target="postgres:5432/wealthsail",
        )
    assert not any("CREATE DATABASE" in sql for sql in connection.statements)
