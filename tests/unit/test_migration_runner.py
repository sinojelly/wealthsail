"""Migration runner safety contract tests."""

from pathlib import Path

from scripts.migrate import discover_migrations, safe_target_name


def test_production_migration_discovery_never_includes_dev_seeds() -> None:
    root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(root / "migrations")

    assert migrations
    assert all(path.parent.name == "migrations" for path in migrations)
    assert all("seed" not in path.name.lower() for path in migrations)
    assert any(path.name.startswith("013_") for path in migrations)


def test_safe_target_name_never_contains_password_or_query_secret() -> None:
    target = safe_target_name(
        "postgresql://deploy:super-secret@db.example:5432/wealthsail?sslmode=require"
    )

    assert target == "db.example:5432/wealthsail"
    assert "secret" not in target
    assert "deploy" not in target
