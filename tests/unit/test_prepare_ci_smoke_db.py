"""Guards for the destructive, ephemeral CI database fixture preparer."""

from __future__ import annotations

import pytest

from scripts.prepare_ci_smoke_db import validate_admin_target


def test_ci_fixture_preparer_accepts_only_loopback_postgres_admin() -> None:
    validate_admin_target(
        "postgresql://postgres:postgres@127.0.0.1:5432/postgres?sslmode=disable"
    )

    with pytest.raises(ValueError, match="loopback"):
        validate_admin_target(
            "postgresql://postgres:secret@db.production.example/postgres?sslmode=require"
        )
    with pytest.raises(ValueError, match="postgres admin"):
        validate_admin_target(
            "postgresql://wealthsail_api_login:secret@127.0.0.1/postgres"
        )
