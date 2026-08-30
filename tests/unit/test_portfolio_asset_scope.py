"""Portfolio scope behavior: mine, tenant and one active member."""

from __future__ import annotations

from datetime import date
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.errors import NotFoundError, ValidationError_
from backend.portfolio import api, repository, service
from backend.portfolio.scope import AssetScope, resolve_asset_scope


class ScopeConn:
    def __init__(self, *, member_exists: bool = True) -> None:
        self.member_exists = member_exists
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args):
        self.calls.append((sql, args))
        return self.member_exists


class QueryConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.calls.append((" ".join(sql.split()), args))
        return []


@pytest.mark.asyncio
async def test_mine_scope_is_bound_to_the_authenticated_user() -> None:
    conn = ScopeConn()

    scope = await resolve_asset_scope(
        conn, tenant_id="tenant-1", actor_user_id="user-1", kind="MINE"
    )

    assert scope == AssetScope(kind="MINE", owner_user_id="user-1")
    assert conn.calls == []


@pytest.mark.asyncio
async def test_member_scope_requires_one_active_member_of_the_current_tenant() -> None:
    conn = ScopeConn(member_exists=True)

    scope = await resolve_asset_scope(
        conn,
        tenant_id="tenant-1",
        actor_user_id="user-1",
        kind="MEMBER",
        member_id="user-2",
    )

    assert scope.owner_user_id == "user-2"
    assert conn.calls[0][1] == ("tenant-1", "user-2")

    missing = ScopeConn(member_exists=False)
    with pytest.raises(NotFoundError):
        await resolve_asset_scope(
            missing,
            tenant_id="tenant-1",
            actor_user_id="user-1",
            kind="MEMBER",
            member_id="outsider",
        )


@pytest.mark.asyncio
async def test_scope_rejects_ambiguous_member_parameters() -> None:
    with pytest.raises(ValidationError_):
        await resolve_asset_scope(
            ScopeConn(),
            tenant_id="tenant-1",
            actor_user_id="user-1",
            kind="MEMBER",
            member_id=None,
        )
    with pytest.raises(ValidationError_):
        await resolve_asset_scope(
            ScopeConn(),
            tenant_id="tenant-1",
            actor_user_id="user-1",
            kind="TENANT",
            member_id="user-2",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        lambda conn: repository.latest_nav_per_account(
            conn, "tenant-1", owner_user_id="user-2"
        ),
        lambda conn: repository.latest_positions(
            conn, "tenant-1", owner_user_id="user-2"
        ),
        lambda conn: repository.nav_history(
            conn, "tenant-1", owner_user_id="user-2"
        ),
    ],
)
async def test_scoped_queries_filter_accounts_by_beneficial_ownership(query) -> None:
    """Removing the ownership EXISTS branch must expose collaborator/unassigned data."""
    conn = QueryConn()

    await query(conn)

    sql, args = conn.calls[0]
    assert "identity.account_members" in sql
    assert "BENEFICIAL_OWNER" in sql
    assert "JOINT_OWNER" in sql
    assert "effective_to IS NULL" in sql
    assert args[-1] == "user-2"


@pytest.mark.asyncio
async def test_tenant_nav_starts_from_snapshots_not_owner_join_rows() -> None:
    """A shared account must appear once in tenant totals, not once per owner."""
    conn = QueryConn()

    await repository.nav_history(conn, "tenant-1", owner_user_id=None)

    sql, _args = conn.calls[0]
    assert "FROM ledger.account_nav_snapshots nav" in sql
    assert "JOIN identity.account_members" not in sql
    assert "$2::uuid IS NULL OR EXISTS" in sql


@pytest.mark.asyncio
async def test_position_service_applies_scope_and_returns_account_owners(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    async def positions(_conn, _tenant, *, owner_user_id=None):
        calls.append(("positions", owner_user_id))
        return [
            {
                "instrument_id": "instrument-1",
                "account_id": "account-1",
                "ticker": "510300",
                "canonical_name": "沪深300ETF",
                "asset_type": "EXCHANGE_FUND",
                "quantity": Decimal("1"),
                "avg_cost": Decimal("4.68"),
                "market_price": Decimal("4.70"),
                "market_value": Decimal("4.70"),
                "unrealized_pnl": Decimal("0.02"),
                "data_quality_status": "FRESH",
                "account_name": "Jelly 证券账户",
                "ownership_scope": "PERSONAL",
                "owner_user_ids": ["user-1"],
                "owner_names": ["Jelly"],
            }
        ]

    async def nav(_conn, _tenant, *, owner_user_id=None):
        calls.append(("nav", owner_user_id))
        return [{"total_nav": Decimal("4.70"), "as_of": date(2026, 8, 30)}]

    monkeypatch.setattr(repository, "latest_positions", positions)
    monkeypatch.setattr(repository, "latest_nav_per_account", nav)

    result = await service.get_positions(
        object(),
        "tenant-1",
        scope=AssetScope(kind="MINE", owner_user_id="user-1"),
    )

    assert calls == [("positions", "user-1"), ("nav", "user-1")]
    assert result[0]["account"] == {
        "id": "account-1",
        "name": "Jelly 证券账户",
        "ownershipScope": "PERSONAL",
        "owners": [{"userId": "user-1", "name": "Jelly"}],
    }


def test_summary_api_defaults_to_the_authenticated_users_assets(monkeypatch) -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def transaction(_pool, _user_id, _tenant_id):
        yield object()

    async def resolve(_conn, **kwargs):
        captured.update(kwargs)
        return AssetScope(kind="MINE", owner_user_id="user-1")

    async def summary(_conn, _tenant_id, _as_of, *, scope):
        captured["resolved"] = scope
        return {
            "totalAsset": None,
            "knownValue": "0.00",
            "valuationCoveragePct": "0.00",
            "unpricedPositionCount": 0,
            "currency": "CNY",
            "cashBalance": "0.00",
            "positionsMarketValue": None,
            "dailyPnl": None,
            "dailyPnlRate": None,
            "holdingPnl": None,
            "holdingPnlRate": None,
            "cumulativePnl": None,
            "cumulativePnlRate": None,
            "allocation": [],
            "qualityStatus": "MISSING",
            "asOf": None,
            "positionCount": 0,
        }

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    monkeypatch.setattr(api, "resolve_asset_scope", resolve, raising=False)
    monkeypatch.setattr(api.service, "get_summary", summary)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_pool] = lambda: object()
    app.dependency_overrides[api.get_tenant_context] = lambda: SimpleNamespace(
        user_id="user-1", tenant_id="tenant-1"
    )
    app.dependency_overrides[api.get_request_id] = lambda: "req-scope"

    response = TestClient(app).get("/portfolio/summary")

    assert response.status_code == 200
    assert captured["kind"] == "MINE"
    assert captured["actor_user_id"] == "user-1"
    assert captured["resolved"] == AssetScope(
        kind="MINE", owner_user_id="user-1"
    )
    assert response.json()["meta"]["scope"] == "MINE"
    assert response.json()["meta"]["memberId"] is None
    assert response.json()["meta"]["tenantId"] == "tenant-1"
