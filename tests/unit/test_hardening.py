"""Regression tests for the security and tenancy boundaries found in Review 09/10."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.common.database import create_pool, normalize_asyncpg_dsn, tenant_transaction
from backend.common.tenancy import resolve_tenant_context
from backend.config import Settings, validate_runtime_settings
from backend.errors import ForbiddenError
from backend.identity.service import load_login_identity, protect_routing_value
from backend.common.security import envelope_decrypt, hmac_digest
from backend.main import app


def test_asyncpg_dsn_removes_client_only_statement_cache_option() -> None:
    """A PgBouncer-only option must not leak through as a PostgreSQL server setting."""
    dsn, options = normalize_asyncpg_dsn(
        "postgresql://user:p%40ss@db.example/app"
        "?sslmode=require&statement_cache_size=0&application_name=wealthsail"
    )

    parsed = urlsplit(dsn)
    assert parse_qs(parsed.query) == {
        "sslmode": ["require"],
        "application_name": ["wealthsail"],
    }
    assert options == {"statement_cache_size": 0}
    assert parsed.netloc == "user:p%40ss@db.example"


@pytest.mark.parametrize(
    "query",
    [
        "statement_cache_size=0&sslmode=require",
        "sslmode=require&statement_cache_size=0",
        "sslmode=require&statement_cache_size=0&application_name=wealthsail",
    ],
)
def test_asyncpg_dsn_normalization_is_independent_of_query_order(query: str) -> None:
    dsn, options = normalize_asyncpg_dsn(f"postgresql://user:pass@db/app?{query}")

    assert "statement_cache_size" not in parse_qs(urlsplit(dsn).query)
    assert options["statement_cache_size"] == 0


def test_tenant_header_cannot_rebind_a_token_to_another_tenant() -> None:
    """A token-bound role must never be reused for a caller-selected tenant."""
    with pytest.raises(ForbiddenError):
        resolve_tenant_context(
            user_id="user-1",
            tenant_id="tenant-a",
            header_tenant_id="tenant-b",
            tenant_role="OWNER",
        )


def test_production_api_rejects_development_secrets_and_worker_credentials() -> None:
    """The API must fail closed when production starts with dev or Worker authority."""
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql://api@db/app",
        worker_database_url="postgresql://worker@db/app",
        jwt_secret="dev-only-jwt-secret-change-me",
        auth_dev_mode=False,
        wecom_callback_token="callback-token",
        wecom_callback_aes_key="A" * 43,
        wecom_corp_id="corp-id",
    )

    with pytest.raises(ValueError, match="JWT_SECRET"):
        validate_runtime_settings(settings, process="api")


def test_production_worker_does_not_require_or_accept_jwt_authority() -> None:
    """A Worker process must not receive the API signing secret."""
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="",
        worker_database_url="postgresql://worker@db/app",
        jwt_secret="a-production-signing-key-that-worker-must-not-have",
        auth_dev_mode=False,
        model_api_key="model-key",
        wecom_corp_secret="corp-secret",
        wecom_agent_id="1000002",
        routing_encryption_key="routing-key-with-at-least-32-bytes!",
    )

    with pytest.raises(ValueError, match="JWT_SECRET"):
        validate_runtime_settings(settings, process="worker")


class _LoginConnection:
    def __init__(self) -> None:
        self.sql = ""

    async def fetch(self, sql: str, email: str):
        self.sql = sql
        return [
            {
                "user_id": "user-1",
                "email": email,
                "nickname": "Pilot",
                "password_hash": "hash",
                "tenant_id": "tenant-1",
                "tenant_name": "Pilot tenant",
                "tenant_role": "OWNER",
            }
        ]


@pytest.mark.asyncio
async def test_login_lookup_does_not_call_a_hash_returning_security_definer() -> None:
    """Credential lookup stays inside the API role instead of an exposed RPC."""
    conn = _LoginConnection()

    rows = await load_login_identity(conn, "pilot@example.test")

    assert rows[0]["tenant_id"] == "tenant-1"
    assert "authenticate_credentials" not in conn.sql
    assert "identity.profiles" in conn.sql


def test_wecom_route_is_searchable_by_hmac_but_recoverable_only_by_worker_key() -> None:
    settings = Settings(
        _env_file=None,
        binding_hmac_key="binding-key",
        routing_encryption_key="routing-key-with-at-least-32-bytes!",
    )

    digest, ciphertext = protect_routing_value(settings, "wecom-user-7")

    assert digest == hmac_digest("binding-key", "wecom-user-7")
    assert "wecom-user-7" not in ciphertext
    assert (
        envelope_decrypt("routing-key-with-at-least-32-bytes!", ciphertext)
        == "wecom-user-7"
    )


@pytest.mark.asyncio
async def test_tenant_transaction_switches_to_dedicated_api_role() -> None:
    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Connection:
        def __init__(self):
            self.statements: list[tuple[str, tuple[object, ...]]] = []

        def transaction(self):
            return Transaction()

        async def execute(self, sql: str, *args):
            self.statements.append((sql, args))

    class Acquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *_args):
            return None

    class Pool:
        def __init__(self, conn):
            self.conn = conn

        def acquire(self):
            return Acquire(self.conn)

    conn = Connection()
    async with tenant_transaction(Pool(conn), "user-1", "tenant-1"):
        pass

    assert conn.statements[0][0] == "SET LOCAL ROLE wealthsail_api;"


@pytest.mark.asyncio
async def test_worker_pool_sets_the_bypass_role_on_every_connection(monkeypatch) -> None:
    captured = {}

    async def fake_create_pool(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("backend.common.database.asyncpg.create_pool", fake_create_pool)
    await create_pool(
        "postgresql://worker@db/app",
        runtime_role="wealthsail_worker",
    )

    class Connection:
        def __init__(self):
            self.sql = None

        async def execute(self, sql):
            self.sql = sql

    conn = Connection()
    await captured["init"](conn)
    assert conn.sql == "SET ROLE wealthsail_worker;"


def test_forward_migration_grants_worker_system_io_and_projection_rebuild() -> None:
    """The least-privilege Worker must be usable on a real, non-superuser DB."""
    sql = (
        Path(__file__).parents[2]
        / "migrations"
        / "013_mvp_trust_delivery_and_projection_hardening.sql"
    ).read_text(encoding="utf-8").upper()

    assert "ALTER ROLE WEALTHSAIL_WORKER NOINHERIT BYPASSRLS" in sql
    assert "GRANT WEALTHSAIL_API TO WEALTHSAIL_WORKER" in sql
    assert "REFERENCE.DATA_SOURCES" in sql and "TO WEALTHSAIL_WORKER" in sql
    assert "MARKET.SOURCE_SNAPSHOTS" in sql
    assert "MARKET.MARKET_OBSERVATIONS" in sql
    assert "GRANT DELETE ON LEDGER.POSITION_SNAPSHOTS TO WEALTHSAIL_API" in sql
    assert "KNOWN_VALUE, VALUATION_COVERAGE_PCT, UNPRICED_POSITION_COUNT" in sql


def test_reports_trade_date_query_keeps_the_frontend_camel_case_contract() -> None:
    parameters = app.openapi()["paths"]["/api/v1/reports/list"]["get"]["parameters"]
    names = {parameter["name"] for parameter in parameters if parameter["in"] == "query"}

    assert "tradeDate" in names
    assert "trade_date" not in names
