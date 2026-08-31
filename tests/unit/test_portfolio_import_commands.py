"""Portfolio initialization commands preserve the difference between facts and input."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.common.tenancy import TenantContext
from backend.errors import ForbiddenError, ValidationError_
from backend.portfolio import repository, service
from backend.portfolio.imports import repository as import_repository


def _payload() -> SimpleNamespace:
    return SimpleNamespace(
        symbol="600519",
        name="untrusted display name",
        assetType="STOCK",
        quantity=Decimal("100"),
        avgCost=Decimal("1500.00"),
        buyDate=date(2026, 8, 26),
        accountId=None,
    )


@asynccontextmanager
async def _tx(_pool, _user_id, _tenant_id):
    yield MagicMock()


@pytest.mark.asyncio
async def test_create_position_import_returns_draft_without_publishing_a_position() -> None:
    """A user's unreviewed input must remain a DRAFT and must not alter projections."""
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    create_draft = AsyncMock(
        return_value={"batch_id": "batch-1", "item_id": "item-1"}
    )
    recompute = AsyncMock()

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_tx),
        patch(
            "backend.portfolio.service._resolve_verified_instrument",
            AsyncMock(
                return_value={
                    "id": None,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "asset_type": "CN_STOCK",
                    "market": "SSE",
                }
            ),
        ),
        patch(
            "backend.portfolio.service.repository.find_instrument_by_ticker",
            AsyncMock(return_value=None),
        ),
        patch(
            "backend.portfolio.service.repository.find_position_import_by_idempotency_key",
            AsyncMock(return_value=None),
        ),
        patch(
            "backend.portfolio.service.repository.upsert_instrument",
            AsyncMock(return_value="instrument-1"),
        ),
        patch(
            "backend.portfolio.service.repository.pick_or_create_account",
            AsyncMock(return_value="account-1"),
        ),
        patch(
            "backend.portfolio.service.repository.create_import_draft",
            create_draft,
        ),
        patch("backend.portfolio.service.write_audit", AsyncMock()),
        patch("backend.portfolio.service.recompute_projections", recompute),
    ):
        result = await service.create_position_import(
            MagicMock(), ctx, _payload(), idempotency_key="request-1"
        )

    assert result == {
        "batchId": "batch-1",
        "status": "DRAFT",
        "version": 1,
        "item": {
            "itemId": "item-1",
            "symbol": "600519",
            "name": "贵州茅台",
            "assetType": "STOCK",
            "quantity": "100",
            "unitCost": "1500.00",
            "tradeDate": "2026-08-26",
            "accountId": "account-1",
            "validationStatus": "VALID",
        },
    }
    recompute.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_position_import_replays_same_draft_without_provider_or_write() -> None:
    """A network retry with the same payload/key returns the original command result."""
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    prior = {
        "batch_id": "batch-1",
        "source_content_hash": service._position_import_content_hash(
            symbol="600519",
            asset_type="STOCK",
            quantity=Decimal("100"),
            unit_cost=Decimal("1500.00"),
            trade_date=date(2026, 8, 26),
            account_id=None,
        ),
        "item_id": "item-1",
        "ticker": "600519",
        "canonical_name": "贵州茅台",
        "asset_type": "CN_STOCK",
        "quantity": Decimal("100"),
        "unit_cost": Decimal("1500.00"),
        "trade_date": date(2026, 8, 26),
        "account_id": "account-1",
        "validation_status": "VALID",
    }

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_tx),
        patch(
            "backend.portfolio.service.repository.find_position_import_by_idempotency_key",
            AsyncMock(return_value=prior),
        ),
        patch(
            "backend.portfolio.service._resolve_verified_instrument",
            AsyncMock(),
        ) as resolve,
        patch(
            "backend.portfolio.service.repository.create_import_draft",
            AsyncMock(),
        ) as create,
    ):
        result = await service.create_position_import(
            MagicMock(), ctx, _payload(), idempotency_key="request-1"
        )

    assert result["batchId"] == "batch-1"
    assert result["item"]["name"] == "贵州茅台"
    resolve.assert_not_awaited()
    create.assert_not_awaited()


class _InstrumentConnection:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.fetchval_calls = 0

    async def execute(self, sql: str, *_args):
        self.executed.append(sql)
        return "OK"

    async def fetchrow(self, _sql: str, *_args):
        self.fetchval_calls += 1
        if self.fetchval_calls == 1:
            return None

    async def fetchval(self, _sql: str, *_args):
        self.fetchval_calls += 1
        return "instrument-1"


@pytest.mark.asyncio
async def test_instrument_creation_serializes_on_market_and_ticker_identity() -> None:
    conn = _InstrumentConnection()

    result = await repository.upsert_instrument(
        conn,
        ticker="600519",
        name="贵州茅台",
        asset_type="CN_STOCK",
        primary_market="SSE",
    )

    assert result == "instrument-1"
    assert "pg_advisory_xact_lock" in conn.executed[0]
    assert conn.fetchval_calls == 2


class _ConflictingInstrumentConnection:
    async def execute(self, _sql: str, *_args):
        return "OK"

    async def fetchrow(self, _sql: str, *_args):
        return {"id": "instrument-1", "asset_type": "CN_STOCK"}


@pytest.mark.asyncio
async def test_instrument_upsert_rejects_existing_type_conflict() -> None:
    with pytest.raises(ValidationError_):
        await repository.upsert_instrument(
            _ConflictingInstrumentConnection(),
            ticker="123001",
            name="错误 ETF",
            asset_type="EXCHANGE_FUND",
            primary_market="SZSE",
        )


class _AccountConnection:
    def __init__(self) -> None:
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.executed: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args):
        self.fetchval_calls.append((sql, args))
        if len(self.fetchval_calls) == 1:
            return None
        return "account-personal-1"

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))
        return "INSERT 0 1"


@pytest.mark.asyncio
async def test_new_automatic_account_is_owned_by_the_importing_user() -> None:
    """A newly imported position must be visible in the actor's MINE scope."""
    conn = _AccountConnection()

    account_id = await repository.pick_or_create_account(
        conn,
        tenant_id="tenant-1",
        asset_type="CN_STOCK",
        owner_user_id="user-1",
    )

    lookup_sql, lookup_args = conn.fetchval_calls[0]
    insert_sql, insert_args = conn.fetchval_calls[1]
    member_sql, member_args = conn.executed[0]
    assert account_id == "account-personal-1"
    assert "identity.account_members" in lookup_sql
    assert lookup_args == ("tenant-1", "BROKERAGE", "user-1")
    assert "ownership_scope" in insert_sql
    assert "'PERSONAL'" in insert_sql
    assert insert_args == ("tenant-1", "BROKERAGE", "证券账户")
    assert "INSERT INTO identity.account_members" in member_sql
    assert "'BENEFICIAL_OWNER', 'OWNER'" in member_sql
    assert member_args == (
        "tenant-1",
        "account-personal-1",
        "user-1",
    )


@pytest.mark.asyncio
async def test_existing_account_write_access_requires_actor_membership() -> None:
    conn = _AccountConnection()
    conn.fetchval_calls.clear()

    await repository.find_writable_account(
        conn,
        tenant_id="tenant-1",
        account_id="account-1",
        user_id="user-1",
    )

    sql, args = conn.fetchval_calls[0]
    assert "identity.account_members" in sql
    assert "permission_role IN ('OWNER', 'EDITOR')" in sql
    assert "effective_to IS NULL" in sql
    assert args == ("tenant-1", "account-1", "user-1")


class _TiantianAccountConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, _sql: str, *_args):
        return None

    async def fetchval(self, sql: str, *args):
        assert "INSERT INTO identity.accounts" in sql
        assert "ownership_scope" in sql
        assert "'PERSONAL'" in sql
        assert args == ("tenant-1", "天天基金账户")
        return "fund-account-1"

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))
        return "INSERT 0 1"


@pytest.mark.asyncio
async def test_new_tiantian_account_is_owned_by_the_importing_user() -> None:
    conn = _TiantianAccountConnection()

    target = await import_repository.resolve_target_account(
        conn,
        tenant_id="tenant-1",
        external_account_key="00000000-0000-0000-0000-000000000001",
        account_id=None,
        account_name="天天基金账户",
        owner_user_id="user-1",
    )

    member_sql, member_args = conn.executed[0]
    assert target.account_id == "fund-account-1"
    assert "INSERT INTO identity.account_members" in member_sql
    assert member_args == ("tenant-1", "fund-account-1", "user-1")


class _UnauthorizedBoundAccountConnection:
    async def fetchrow(self, _sql: str, *_args):
        return {
            "binding_id": "binding-1",
            "binding_account_id": "account-other",
            "binding_status": "ACTIVE",
            "display_name": "另一位成员的天天基金",
            "account_name": "另一位成员的天天基金",
            "actor_can_write": False,
        }


@pytest.mark.asyncio
async def test_tiantian_binding_cannot_be_reused_by_another_tenant_member() -> None:
    with pytest.raises(ForbiddenError):
        await import_repository.resolve_target_account(
            _UnauthorizedBoundAccountConnection(),
            tenant_id="tenant-1",
            external_account_key="00000000-0000-0000-0000-000000000001",
            account_id=None,
            account_name=None,
            owner_user_id="user-2",
        )


class _ConfirmConnection:
    def __init__(self) -> None:
        self.event_sql = ""
        self.legs: list[tuple] = []
        self.batch_update_sql = ""
        self.execute_sqls: list[str] = []

    async def fetchrow(self, sql: str, *_args):
        assert "FOR UPDATE" in sql
        return {
            "batch_id": "batch-1",
            "account_id": "account-1",
            "status": "DRAFT",
            "version": 1,
        }

    async def fetch(self, sql: str, *_args):
        assert "portfolio_import_items" in sql
        return [{
            "item_id": "item-1",
            "item_type": "POSITION",
            "instrument_id": "instrument-1",
            "quantity": Decimal("100"),
            "unit_cost": Decimal("1500.00"),
            "cash_amount": None,
            "currency": "CNY",
            "trade_date": date(2026, 8, 26),
            "validation_status": "VALID",
        }]

    async def fetchval(self, sql: str, *_args):
        self.event_sql = sql
        return "event-1"

    async def executemany(self, _sql: str, rows: list[tuple]):
        self.legs = rows

    async def execute(self, sql: str, *_args):
        self.execute_sqls.append(sql)
        self.batch_update_sql = sql
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_confirm_import_emits_one_opening_adjustment_and_no_cash_flow() -> None:
    """Confirmation establishes a baseline; it does not invent a trade or cash transfer."""
    conn = _ConfirmConnection()

    result = await repository.confirm_import_batch(
        conn,
        tenant_id="tenant-1",
        batch_id="batch-1",
        idempotency_key="confirm-1",
    )

    assert "'ADJUSTMENT', 'CONFIRMED'" in conn.event_sql
    assert "import_batch_id" in conn.event_sql
    assert conn.legs == [
        (
            "tenant-1",
            "event-1",
            "INSTRUMENT",
            "instrument-1",
            Decimal("100"),
            Decimal("1500.00"),
        )
    ]
    assert "status = 'CONFIRMED'" in conn.batch_update_sql
    assert any("pg_advisory_xact_lock" in sql for sql in conn.execute_sqls)
    assert result["event_id"] == "event-1"
    assert result["version"] == 2
