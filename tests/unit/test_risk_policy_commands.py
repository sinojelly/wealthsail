"""Risk policy command integrity tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.errors import DataUnavailableError
from backend.risk import service
from backend.risk.service import _preview_dto, canonical_policy_hash


def test_policy_hash_covers_l1_inputs_and_excludes_deferred_asset_budgets() -> None:
    first = canonical_policy_hash(
        {
            "benchmark": "CSI300",
            "horizonMonths": 24,
            "maxPosition": "25.00",
            "maxDrawdown": "15.00",
            "minCash": "5.00",
            "maxAlertsPerDay": 5,
            "silenceMinutes": 60,
        }
    )
    reordered = canonical_policy_hash(
        {
            "silenceMinutes": 60,
            "maxAlertsPerDay": 5,
            "minCash": "5.0",
            "maxDrawdown": "15",
            "maxPosition": "25",
            "horizonMonths": 24,
            "benchmark": "CSI300",
        }
    )

    assert first == reordered
    assert first.startswith("sha256:")


class _PreviewConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetchval(self, sql: str, *_args):
        self.queries.append(" ".join(sql.split()))
        return Decimal("10") if len(self.queries) == 1 else 0


@pytest.mark.asyncio
async def test_risk_preview_abstains_from_partial_cross_account_nav() -> None:
    conn = _PreviewConnection()
    row = {
        "id": "draft-1",
        "version": 1,
        "single_position_limit": Decimal("20"),
        "policy_hash": "sha256:test",
    }

    await _preview_dto(conn, "tenant-1", row)

    aggregate_sql = conn.queries[1].upper()
    assert "COUNT(*) = COUNT(TOTAL_NAV)" in aggregate_sql


class _PolicyConnection:
    async def fetchrow(self, _sql: str, *_args):
        return {
            "single_position_limit": Decimal("20"),
            "max_drawdown_limit": Decimal("15"),
            "cash_target_pct": Decimal("5"),
        }


@pytest.mark.asyncio
async def test_risk_checks_fail_closed_when_any_account_nav_is_incomplete(
    monkeypatch,
) -> None:
    async def incomplete_nav(_conn, _tenant):
        return [{"total_nav": None, "cash_balance": Decimal("100")}]

    monkeypatch.setattr(
        service.portfolio_repo, "latest_nav_per_account", incomplete_nav
    )

    with pytest.raises(DataUnavailableError, match="估值不完整"):
        await service._compute_checks(_PolicyConnection(), "tenant-1")
