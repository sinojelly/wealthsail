"""Readback recovery cannot be combined with migration-changing modes."""

import sys

import pytest

from scripts.migrate import migrate, parse_args


def test_readback_only_cli_is_explicit(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "migrate.py",
            "--environment",
            "staging",
            "--confirm-target",
            "db.example:5432/wealthsail",
            "--readback-only",
        ],
    )
    assert parse_args().readback_only is True


@pytest.mark.parametrize("conflict", [["--dry-run"], ["--reconcile-drift", "105"]])
def test_readback_only_cli_rejects_migration_changing_options(monkeypatch, conflict):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "migrate.py",
            "--environment",
            "staging",
            "--confirm-target",
            "db.example:5432/wealthsail",
            "--readback-only",
            *conflict,
        ],
    )
    with pytest.raises(SystemExit) as failure:
        parse_args()
    assert failure.value.code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options", [{"dry_run": True}, {"dry_run": False, "reconcile_drift": 105}]
)
async def test_readback_only_runner_rejects_conflicts_before_connecting(options):
    with pytest.raises(ValueError, match="readback.only"):
        await migrate("not-a-database-url", [], readback_only=True, **options)
