"""Migration runner safety contract tests."""

from pathlib import Path

from scripts.migrate import (
    discover_migrations,
    is_loopback_database,
    load_migrations,
    safe_target_name,
)


def test_production_migration_discovery_never_includes_dev_seeds() -> None:
    root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(root / "migrations")

    assert migrations
    assert all(path.parent.name == "migrations" for path in migrations)
    assert all("seed" not in path.name.lower() for path in migrations)
    assert any(path.name.startswith("013_") for path in migrations)


def test_production_migrations_have_one_file_per_version() -> None:
    """The deploy runner must be able to load the complete forward-only chain."""
    root = Path(__file__).resolve().parents[2]

    migrations = load_migrations(root / "migrations")

    versions = [migration.version for migration in migrations]
    assert len(versions) == len(set(versions))
    assert versions[-1] == 24
    assert migrations[-1].path.name == "024_password_reset_and_tenant_creation.sql"


def test_safe_target_name_never_contains_password_or_query_secret() -> None:
    target = safe_target_name(
        "postgresql://deploy:super-secret@db.example:5432/wealthsail?sslmode=require"
    )

    assert target == "db.example:5432/wealthsail"
    assert "secret" not in target
    assert "deploy" not in target


def test_loopback_detection_cannot_be_bypassed_with_an_explicit_port() -> None:
    assert is_loopback_database("postgresql://postgres@127.0.0.1/wealthsail")
    assert is_loopback_database("postgresql://postgres@127.0.0.1:55432/wealthsail")
    assert is_loopback_database("postgresql://postgres@localhost:5432/wealthsail")
    assert is_loopback_database("postgresql://postgres@[::1]:5432/wealthsail")
    assert not is_loopback_database(
        "postgresql://deploy@db.example:5432/wealthsail"
    )
