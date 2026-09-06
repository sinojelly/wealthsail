"""Persistence contracts for append-only market-observation revisions."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.market import repository


@pytest.mark.asyncio
async def test_insert_observations_appends_revision_only_when_fact_changes() -> None:
    captured: dict[str, object] = {}

    class Connection:
        async def executemany(self, sql: str, rows: list[tuple]) -> None:
            captured["sql"] = sql
            captured["rows"] = rows

    row = (
        "FUND",
        "00000000-0000-0000-0000-000000000001",
        "ESTIMATED_NAV",
        Decimal("1.2345"),
        None,
        None,
        "元/份",
        "CNY",
        datetime(2026, 9, 4, 8, 4, tzinfo=timezone.utc),
        datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc),
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000003",
        "ESTIMATED",
        "sina/fund-estimate/v1",
        '{"valuationDateKind":"LATEST_TRADING_DAY"}',
    )

    await repository.insert_observations(Connection(), [row])

    sql = str(captured["sql"])
    assert "ORDER BY revision DESC" in sql
    assert "max(revision)" not in sql.lower()
    assert "latest.revision + 1" in sql
    assert "IS NOT DISTINCT FROM" in sql
    assert captured["rows"] == [row]
