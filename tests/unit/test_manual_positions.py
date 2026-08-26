"""Unit tests: 手动添加持仓（搜索 + 录入 + 投影重算编排）。

行情抓取与租户事务全部 mock，聚焦 service 编排逻辑与校验规则。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.errors import IdempotencyConflictError, NotFoundError, ValidationError_
from backend.integrations import market_data as md
from backend.portfolio import service

_AS_OF = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


def _observation(symbol: str = "600519", value: str = "1500.00") -> md.Observation:
    return md.Observation(
        symbol=symbol,
        subject_type="INSTRUMENT",
        field_code=md.F_CLOSE,
        value=Decimal(value),
        as_of=_AS_OF,
        quality_status=md.Q_FRESH,
        unit="元",
    )


def _payload(**overrides) -> SimpleNamespace:
    base = dict(
        symbol="600519",
        name="贵州茅台",
        assetType="STOCK",
        quantity="100",
        avgCost="1500.00",
        buyDate=None,
        accountId=None,
        idempotencyKey="idem-1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_normalize_symbol_accepts_suffixes_and_rejects_junk() -> None:
    assert service.normalize_symbol("600519") == "600519"
    assert service.normalize_symbol("600519.SH") == "600519"
    with pytest.raises(ValidationError_):
        service.normalize_symbol("茅台")
    with pytest.raises(ValidationError_):
        service.normalize_symbol("12345")


@asynccontextmanager
async def _fake_tx(_pool, _user_id, _tenant_id):
    yield MagicMock()


@pytest.mark.asyncio
async def test_add_position_success_appends_ledger_and_rebuilds_projections() -> None:
    pool, ctx = MagicMock(), SimpleNamespace(user_id="u1", tenant_id="t1")

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_fake_tx),
        patch(
            "backend.portfolio.service.repository.find_instrument_by_ticker",
            AsyncMock(return_value=None),
        ),
        patch(
            "backend.portfolio.service.repository.upsert_instrument",
            AsyncMock(return_value="inst-9"),
        ),
        patch(
            "backend.portfolio.service.repository.pick_or_create_account",
            AsyncMock(return_value="acct-1"),
        ),
        patch(
            "backend.portfolio.service.repository.insert_buy_event",
            AsyncMock(return_value="evt-1"),
        ),
        patch(
            "backend.portfolio.service.repository.insert_cash_in_event",
            AsyncMock(return_value="cashin-1"),
        ),
        patch(
            "backend.portfolio.service.repository.holding_instrument_ids",
            AsyncMock(return_value=["inst-9"]),
        ),
        patch(
            "backend.portfolio.service.repository.latest_observation_quotes",
            AsyncMock(return_value={}),
        ),
        patch("backend.portfolio.service.write_audit", AsyncMock()),
        patch(
            "backend.portfolio.service.market_repository.refresh_positions",
            AsyncMock(return_value=1),
        ),
        patch(
            "backend.portfolio.service.market_repository.refresh_account_nav",
            AsyncMock(return_value=1),
        ),
        patch(
            "backend.portfolio.service._fetch_quote",
            AsyncMock(return_value=_observation()),
        ),
    ):
        result = await service.add_position(pool, ctx, _payload())

    pos = result["position"]
    assert pos["symbol"] == "600519"
    assert pos["type"] == "STOCK"
    assert pos["quantity"] == "100"
    assert pos["avgCost"] == "1500.00"
    assert pos["price"] == "1500.00"
    assert pos["marketValue"] == "150000.00"
    assert pos["pnl"] == "0.00"
    assert pos["quality"] == "FRESH"
    assert pos["accountId"] == "acct-1"
    assert pos["eventId"] == "evt-1"


@pytest.mark.asyncio
async def test_add_position_duplicate_idempotency_key_conflicts() -> None:
    pool, ctx = MagicMock(), SimpleNamespace(user_id="u1", tenant_id="t1")

    class _UniqueViolation(Exception):
        pass

    import asyncpg

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_fake_tx),
        patch(
            "backend.portfolio.service.repository.find_instrument_by_ticker",
            AsyncMock(return_value={"id": "inst-1", "asset_type": "CN_STOCK"}),
        ),
        patch(
            "backend.portfolio.service.repository.pick_or_create_account",
            AsyncMock(return_value="acct-1"),
        ),
        patch(
            "backend.portfolio.service.repository.insert_cash_in_event",
            AsyncMock(return_value="cashin-1"),
        ),
        patch(
            "backend.portfolio.service.repository.insert_buy_event",
            AsyncMock(side_effect=asyncpg.exceptions.UniqueViolationError),
        ),
        patch(
            "backend.portfolio.service._fetch_quote",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(IdempotencyConflictError):
            await service.add_position(pool, ctx, _payload())


@pytest.mark.asyncio
async def test_add_position_rejects_asset_type_mismatch() -> None:
    pool, ctx = MagicMock(), SimpleNamespace(user_id="u1", tenant_id="t1")

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_fake_tx),
        patch(
            "backend.portfolio.service.repository.find_instrument_by_ticker",
            AsyncMock(return_value={"id": "inst-1", "asset_type": "EXCHANGE_FUND"}),
        ),
        patch(
            "backend.portfolio.service._fetch_quote",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(ValidationError_):
            await service.add_position(pool, ctx, _payload())


@pytest.mark.asyncio
async def test_add_position_validates_quantity_and_type() -> None:
    pool, ctx = MagicMock(), SimpleNamespace(user_id="u1", tenant_id="t1")
    with pytest.raises(ValidationError_):
        await service.add_position(pool, ctx, _payload(quantity="0"))
    with pytest.raises(ValidationError_):
        await service.add_position(pool, ctx, _payload(assetType="BOND"))
    with pytest.raises(ValidationError_):
        await service.add_position(pool, ctx, _payload(symbol="abc"))


@pytest.mark.asyncio
async def test_search_merges_local_and_live_results() -> None:
    pool, ctx = MagicMock(), SimpleNamespace(user_id="u1", tenant_id="t1")

    local_row = {
        "ticker": "510300",
        "canonical_name": "沪深300ETF",
        "primary_market": "SSE",
        "asset_type": "EXCHANGE_FUND",
    }
    with (
        patch("backend.portfolio.service.tenant_transaction", new=_fake_tx),
        patch(
            "backend.portfolio.service.repository.search_instruments_local",
            AsyncMock(return_value=[local_row]),
        ),
        patch(
            "backend.portfolio.service._ak_search",
            AsyncMock(
                return_value=[
                    {
                        "symbol": "510300",
                        "name": "沪深300ETF",
                        "market": "SSE",
                        "type": "ETF",
                    },
                    {
                        "symbol": "000300",
                        "name": "沪深300",
                        "market": "SZSE",
                        "type": "STOCK",
                    },
                ]
            ),
        ),
    ):
        data = await service.search_instruments(pool, ctx, "沪深300")

    assert data["total"] == 2
    by_symbol = {item["symbol"]: item for item in data["items"]}
    # 本地结果优先（live 同 symbol 不覆盖）
    assert by_symbol["510300"]["name"] == "沪深300ETF"
    assert by_symbol["000300"]["type"] == "STOCK"


@pytest.mark.asyncio
async def test_quote_failure_degrades_to_unavailable() -> None:
    pool, ctx = MagicMock(), SimpleNamespace(user_id="u1", tenant_id="t1")

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_fake_tx),
        patch(
            "backend.portfolio.service.repository.find_instrument_by_ticker",
            AsyncMock(return_value=None),
        ),
        patch(
            "backend.portfolio.service.repository.upsert_instrument",
            AsyncMock(return_value="inst-9"),
        ),
        patch(
            "backend.portfolio.service.repository.pick_or_create_account",
            AsyncMock(return_value="acct-1"),
        ),
        patch(
            "backend.portfolio.service.repository.insert_buy_event",
            AsyncMock(return_value="evt-1"),
        ),
        patch(
            "backend.portfolio.service.repository.insert_cash_in_event",
            AsyncMock(return_value="cashin-1"),
        ),
        patch(
            "backend.portfolio.service.repository.holding_instrument_ids",
            AsyncMock(return_value=["inst-9"]),
        ),
        patch(
            "backend.portfolio.service.repository.latest_observation_quotes",
            AsyncMock(return_value={}),
        ),
        patch("backend.portfolio.service.write_audit", AsyncMock()),
        patch(
            "backend.portfolio.service.market_repository.refresh_positions",
            AsyncMock(return_value=1),
        ),
        patch(
            "backend.portfolio.service.market_repository.refresh_account_nav",
            AsyncMock(return_value=1),
        ),
        patch(
            "backend.portfolio.service._fetch_quote",
            AsyncMock(return_value=None),  # 行情源不可用
        ),
    ):
        result = await service.add_position(pool, ctx, _payload())

    pos = result["position"]
    assert pos["price"] is None
    assert pos["marketValue"] is None
    assert pos["pnl"] is None
    assert pos["quality"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 编辑 / 删除持仓
# ---------------------------------------------------------------------------
_CURRENT_600519 = {
    "account_id": "acct-1",
    "instrument_id": "inst-600519",
    "quantity": Decimal("100"),
    "avg_cost": Decimal("1500.00"),
}


@asynccontextmanager
async def _fake_tx_full(_pool, _user_id, _tenant_id):
    conn = MagicMock()
    conn.fetchval = AsyncMock(
        side_effect=lambda *args, **kwargs: {
            "SELECT canonical_name FROM reference.instruments WHERE id = $1": "贵州茅台",
            "SELECT asset_type FROM reference.instruments WHERE id = $1": "CN_STOCK",
        }.get(
            args[0],
            MagicMock(),
        )
    )
    yield conn


@pytest.mark.asyncio
async def test_update_position_clears_then_rebuilds_ledger() -> None:
    pool, ctx = MagicMock(), SimpleNamespace(user_id="u1", tenant_id="t1")

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_fake_tx_full),
        patch(
            "backend.portfolio.service.repository.current_position",
            AsyncMock(return_value=_CURRENT_600519),
        ),
        patch(
            "backend.portfolio.service.repository.insert_adjustment_event",
            AsyncMock(return_value="adj-1"),
        ),
        patch(
            "backend.portfolio.service.repository.insert_cash_out_event",
            AsyncMock(return_value="out-1"),
        ),
        patch(
            "backend.portfolio.service.repository.insert_cash_in_event",
            AsyncMock(return_value="in-1"),
        ),
        patch("backend.portfolio.service.write_audit", AsyncMock()),
        patch("backend.portfolio.service._recompute_projections", AsyncMock()),
        patch(
            "backend.portfolio.service.repository.latest_observation_quotes",
            AsyncMock(return_value={"inst-600519": {"price": Decimal("1500.00"), "quality": "FRESH"}}),
        ),
    ):
        result = await service.update_position(
            pool, ctx, "600519", _payload(quantity="200", avgCost="1400.00")
        )

    pos = result["position"]
    assert pos["quantity"] == "200"
    assert pos["avgCost"] == "1400.00"
    assert pos["marketValue"] == "300000.00"  # 200 × 实时价 1500
    assert pos["pnl"] == "20000.00"  # 300000 − 200×1400
    assert pos["quality"] == "FRESH"


@pytest.mark.asyncio
async def test_update_position_not_found_raises() -> None:
    pool, ctx = MagicMock(), SimpleNamespace(user_id="u1", tenant_id="t1")

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_fake_tx_full),
        patch(
            "backend.portfolio.service.repository.current_position",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(NotFoundError):
            await service.update_position(
                pool, ctx, "600519", _payload(quantity="100", avgCost="1500.00")
            )


@pytest.mark.asyncio
async def test_delete_position_clears_ledger_and_returns_removed() -> None:
    pool, ctx = MagicMock(), SimpleNamespace(user_id="u1", tenant_id="t1")

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_fake_tx_full),
        patch(
            "backend.portfolio.service.repository.current_position",
            AsyncMock(return_value=_CURRENT_600519),
        ),
        patch(
            "backend.portfolio.service.repository.insert_adjustment_event",
            AsyncMock(return_value="adj-1"),
        ),
        patch(
            "backend.portfolio.service.repository.insert_cash_out_event",
            AsyncMock(return_value="out-1"),
        ),
        patch("backend.portfolio.service.write_audit", AsyncMock()),
        patch("backend.portfolio.service._recompute_projections", AsyncMock()),
    ):
        result = await service.delete_position(pool, ctx, "600519")

    assert result["removed"]["symbol"] == "600519"
    assert result["removed"]["name"] == "贵州茅台"
    assert result["removed"]["quantity"] == "100"
