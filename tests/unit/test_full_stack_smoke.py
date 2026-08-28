"""Safety contracts for the production-data full-stack smoke runner."""

from __future__ import annotations

import pytest

from scripts.full_stack_smoke import build_api_environment, validate_database_target


def test_remote_smoke_accepts_only_least_privileged_api_login() -> None:
    target = validate_database_target(
        "postgresql://wealthsail_api_login.project-ref:secret@"
        "aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
        "?sslmode=require&statement_cache_size=0",
        expected_host="aws-0-ap-southeast-1.pooler.supabase.com",
    )

    assert target.host == "aws-0-ap-southeast-1.pooler.supabase.com"
    assert target.database == "postgres"
    assert target.login_role == "wealthsail_api_login"
    assert "secret" not in target.safe_label


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres:secret@db.example.com/postgres?sslmode=require",
        "postgresql://wealthsail_worker_login:secret@db.example.com/postgres?sslmode=require",
        "postgresql://service_role:secret@db.example.com/postgres?sslmode=require",
    ],
)
def test_remote_smoke_rejects_privileged_or_worker_logins(dsn: str) -> None:
    with pytest.raises(ValueError, match="wealthsail_api_login"):
        validate_database_target(dsn, expected_host="db.example.com")


def test_remote_smoke_requires_exact_host_confirmation_and_tls() -> None:
    dsn = "postgresql://wealthsail_api_login:secret@db.example.com/postgres"

    with pytest.raises(ValueError, match="expected host"):
        validate_database_target(dsn, expected_host="")
    with pytest.raises(ValueError, match="host confirmation"):
        validate_database_target(dsn + "?sslmode=require", expected_host="other.example.com")
    with pytest.raises(ValueError, match="sslmode"):
        validate_database_target(dsn, expected_host="db.example.com")


def test_loopback_database_requires_explicit_local_mode() -> None:
    dsn = (
        "postgresql://wealthsail_api_login:ci-only@127.0.0.1:5432/postgres"
        "?sslmode=disable"
    )

    with pytest.raises(ValueError, match="loopback"):
        validate_database_target(dsn, expected_host="127.0.0.1")

    target = validate_database_target(
        dsn,
        expected_host="127.0.0.1",
        allow_loopback=True,
    )
    assert target.login_role == "wealthsail_api_login"
    assert target.host == "127.0.0.1"


def test_transaction_pooler_requires_disabled_statement_cache() -> None:
    dsn = (
        "postgresql://wealthsail_api_login.project-ref:secret@"
        "pooler.supabase.com:6543/postgres?sslmode=require"
    )

    with pytest.raises(ValueError, match="statement_cache_size=0"):
        validate_database_target(dsn, expected_host="pooler.supabase.com")


def test_api_child_environment_drops_worker_and_delivery_authority() -> None:
    child_env = build_api_environment(
        {
            "PATH": "test-path",
            "SystemDrive": "C:",
            "WORKER_DATABASE_URL": "must-not-leak",
            "WECOM_CORP_SECRET": "must-not-leak",
            "WECOM_BOT_SECRET": "must-not-leak",
            "MODEL_API_KEY": "must-not-leak",
        },
        database_url="postgresql://wealthsail_api_login:secret@db.example/postgres",
        ephemeral_secret="x" * 64,
    )

    assert child_env["APP_ENV"] == "staging"
    assert child_env["DATABASE_URL"].startswith("postgresql://wealthsail_api_login:")
    assert child_env["DB_POOL_MIN_SIZE"] == "1"
    assert child_env["DB_POOL_MAX_SIZE"] == "2"
    assert child_env["TIANTIAN_IMPORT_ENABLED"] == "false"
    assert child_env["JWT_SECRET"] == "x" * 64
    assert child_env["PATH"] == "test-path"
    assert child_env["SystemDrive"] == "C:"
    for forbidden in (
        "WORKER_DATABASE_URL",
        "WECOM_CORP_SECRET",
        "WECOM_BOT_SECRET",
        "MODEL_API_KEY",
    ):
        assert forbidden not in child_env
