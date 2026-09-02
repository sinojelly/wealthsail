from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.common.tenancy import TenantContext
from backend.errors import NotFoundError, ValidationError_
from backend.watchlist import service


USER_ID = "00000000-0000-0000-0000-000000000001"
TENANT_ID = "00000000-0000-0000-0000-000000000002"


class WatchlistConn:
    """Small in-memory asyncpg boundary double for watchlist behavior."""

    def __init__(self, *, instrument: dict | None, created: dict | None = None) -> None:
        self.instrument = instrument
        self.created = created
        self.items: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple[str, tuple]] = []
        self.insert_args: tuple | None = None
        self.audit_rows: list[tuple] = []
        self.register_args: tuple | None = None
        self.register_error: Exception | None = None

    async def fetchrow(self, sql: str, *args):
        self.calls.append((sql, args))
        if "FROM reference.instrument_aliases" in sql:
            return self.instrument
        if "FROM reference.ensure_watchlist_instrument" in sql:
            self.register_args = args
            if self.register_error is not None:
                raise self.register_error
            return self.created
        if "INSERT INTO identity.watchlist" in sql:
            self.insert_args = args
            key = (args[0], args[1])
            if key in self.items:
                return None
            row = {
                "symbol": args[1],
                "asset_name": args[2],
                "asset_type": args[3],
                "added_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            }
            self.items[key] = row
            return row
        if (
            "FROM identity.watchlist" in sql
            and "WHERE user_id = $1 AND symbol = $2" in sql
        ):
            return self.items.get((args[0], args[1]))
        if "DELETE FROM identity.watchlist" in sql:
            return self.items.pop((args[0], args[1]), None)
        raise AssertionError(f"unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql: str, *args):
        self.calls.append((sql, args))
        assert "FROM identity.watchlist" in sql
        assert args == (USER_ID,)
        return list(self.items.values())

    async def execute(self, sql: str, *args):
        self.calls.append((sql, args))
        if "INSERT INTO audit.audit_log" in sql:
            self.audit_rows.append(args)
        return "INSERT 0 1"


def viewer_context() -> TenantContext:
    return TenantContext(USER_ID, TENANT_ID, tenant_role="VIEWER")


@pytest.mark.asyncio
async def test_add_resolves_server_canonical_metadata_and_is_idempotent() -> None:
    conn = WatchlistConn(
        instrument={"symbol": "600036", "asset_name": "招商银行", "asset_type": "STOCK"}
    )
    ctx = viewer_context()

    first = await service.add_watchlist_item(conn, ctx, " 600036 ")
    second = await service.add_watchlist_item(conn, ctx, "600036")

    assert first == second
    assert first["assetName"] == "招商银行"
    assert conn.insert_args is not None
    assert conn.insert_args[0] == USER_ID
    assert conn.insert_args[1:] == ("600036", "招商银行", "STOCK")
    assert len(conn.audit_rows) == 1
    audit = conn.audit_rows[0]
    assert audit[3] == "WATCHLIST_ITEM_ADDED"
    assert audit[-1] == '{"symbol": "600036", "assetType": "STOCK"}'


@pytest.mark.asyncio
async def test_add_rejects_symbol_missing_from_authoritative_master() -> None:
    with pytest.raises(ValidationError_, match="未通过主数据验证"):
        await service.add_watchlist_item(
            WatchlistConn(instrument=None), viewer_context(), "UNKNOWN"
        )


@pytest.mark.asyncio
async def test_add_auto_registers_fund_missing_from_master() -> None:
    """基金不在主数据时，携带搜索结果元数据即可自动登记并关注。"""
    conn = WatchlistConn(
        instrument=None,
        created={
            "symbol": "110022",
            "asset_name": "易方达消费行业",
            "asset_type": "OFF_EXCHANGE_FUND",
            "primary_market": "FUND_CN",
            "instrument_id": "00000000-0000-0000-0000-0000000000aa",
        },
    )
    result = await service.add_watchlist_item(
        conn,
        viewer_context(),
        "110022",
        name="易方达消费行业",
        asset_type="FUND",
        market="FUND_CN",
    )

    assert conn.register_args == (
        "110022",
        "易方达消费行业",
        "OFF_EXCHANGE_FUND",
        "FUND_CN",
        "CNY",
    )
    assert result["assetName"] == "易方达消费行业"
    assert result["assetType"] == "FUND"
    assert conn.insert_args[1:] == ("110022", "易方达消费行业", "FUND")


@pytest.mark.asyncio
async def test_add_auto_registers_etf_and_infers_exchange_from_code() -> None:
    conn = WatchlistConn(
        instrument=None,
        created={
            "symbol": "510300",
            "asset_name": "沪深300ETF",
            "asset_type": "EXCHANGE_FUND",
            "primary_market": "SSE",
            "instrument_id": "00000000-0000-0000-0000-0000000000bb",
        },
    )
    await service.add_watchlist_item(
        conn,
        viewer_context(),
        "510300",
        name="沪深300ETF",
        asset_type="ETF",
    )
    assert conn.register_args == ("510300", "沪深300ETF", "EXCHANGE_FUND", "SSE", "CNY")


@pytest.mark.asyncio
async def test_add_reports_explicit_error_when_market_cannot_be_resolved() -> None:
    with pytest.raises(ValidationError_, match="无法确定其市场"):
        await service.add_watchlist_item(
            WatchlistConn(instrument=None),
            viewer_context(),
            "999999",
            name="未知ETF",
            asset_type="ETF",
            market="NYSE",  # 非合法市场且代码前缀无法推断
        )


@pytest.mark.asyncio
async def test_add_surfaces_registration_failure_without_silent_swallow() -> None:
    conn = WatchlistConn(instrument=None, created=None)
    conn.register_error = RuntimeError("constraint violation")
    with pytest.raises(ValidationError_, match="自动登记主数据失败"):
        await service.add_watchlist_item(
            conn,
            viewer_context(),
            "110022",
            name="易方达消费行业",
            asset_type="FUND",
            market="FUND_CN",
        )
    # 登记失败时不得写入关注记录。
    assert conn.insert_args is None


@pytest.mark.asyncio
async def test_add_rejects_blank_or_too_long_symbols_before_lookup() -> None:
    conn = WatchlistConn(instrument=None)
    with pytest.raises(ValidationError_):
        await service.add_watchlist_item(conn, viewer_context(), "   ")
    with pytest.raises(ValidationError_):
        await service.add_watchlist_item(conn, viewer_context(), "A" * 33)
    assert conn.calls == []


@pytest.mark.asyncio
async def test_list_is_scoped_to_the_context_user_and_maps_wire_fields() -> None:
    conn = WatchlistConn(instrument=None)
    conn.items[(USER_ID, "510300")] = {
        "symbol": "510300",
        "asset_name": "沪深300ETF",
        "asset_type": "ETF",
        "added_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }

    result = await service.list_watchlist(conn, USER_ID)

    assert result == [
        {
            "symbol": "510300",
            "assetName": "沪深300ETF",
            "assetType": "ETF",
            "addedAt": "2026-09-01T00:00:00+00:00",
        }
    ]


@pytest.mark.asyncio
async def test_remove_deletes_only_the_context_user_record_and_audits_normalized_data() -> (
    None
):
    conn = WatchlistConn(instrument=None)
    conn.items[(USER_ID, "600036")] = {
        "symbol": "600036",
        "asset_name": "招商银行",
        "asset_type": "STOCK",
        "added_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }

    deleted = await service.remove_watchlist_item(conn, viewer_context(), " 600036 ")

    assert deleted["symbol"] == "600036"
    delete_sql, delete_args = next(
        (call for call in conn.calls if "DELETE FROM identity.watchlist" in call[0])
    )
    assert "WHERE user_id = $1 AND symbol = $2" in delete_sql
    assert delete_args == (USER_ID, "600036")
    assert len(conn.audit_rows) == 1
    audit = conn.audit_rows[0]
    assert audit[3] == "WATCHLIST_ITEM_REMOVED"
    assert audit[-1] == '{"symbol": "600036", "assetType": "STOCK"}'


@pytest.mark.asyncio
async def test_remove_missing_symbol_is_not_found_and_does_not_audit() -> None:
    conn = WatchlistConn(instrument=None)

    with pytest.raises(NotFoundError):
        await service.remove_watchlist_item(conn, viewer_context(), "MISSING")

    assert conn.audit_rows == []
