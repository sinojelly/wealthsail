"""modules/portfolio 领域服务单元测试（假连接断言 SQL 与业务规则）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from modules.errors import ForbiddenError, NotFoundError, ValidationError
from modules.identity.service import TenantContext
from modules.portfolio.service import (
    PortfolioService,
    derive_account_nav,
    fmt_money,
    fmt_qty,
    normalize_symbol,
    rebuild_confirmed_holdings,
)

ACTOR = TenantContext(tenant_id="tenant-1", user_id="user-1", tenant_role="OWNER")


# ---------------------------------------------------------------------------
# 假连接 / 假池（按调用顺序返回配置好的结果，并记录全部调用）
# ---------------------------------------------------------------------------
class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.fetchrow_results: list[Any] = []
        self.fetchval_results: list[Any] = []
        self.fetch_results: list[list[Any]] = []
        self.execute_results: list[Any] = []
        self.executemany_results: list[Any] = []

    async def fetchrow(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        if not self.fetchrow_results:
            return None
        return self.fetchrow_results.pop(0)

    async def fetch(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        if not self.fetch_results:
            return []
        return self.fetch_results.pop(0)

    async def fetchval(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        if not self.fetchval_results:
            return None
        return self.fetchval_results.pop(0)

    async def execute(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        if not self.execute_results:
            return "OK"
        return self.execute_results.pop(0)

    async def executemany(self, sql, args) -> Any:
        self.calls.append((sql, tuple(args)))
        if not self.executemany_results:
            return "OK"
        return self.executemany_results.pop(0)

    def transaction(self):
        class _Tx:
            def __init__(self, conn):
                self._conn = conn

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, *_args):
                return None

        return _Tx(self)


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def acquire(self):
        class _Acquire:
            def __init__(self, conn):
                self._conn = conn

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, *_args):
                return None

        return _Acquire(self.conn)


class FakeSearchProvider:
    """可注入的标的数据源（模拟 AkShare 实时搜索）。"""

    def __init__(self, results: list[dict[str, str]] | None = None) -> None:
        self.results = results or []
        self.raise_error = False
        self.calls: list[str] = []

    async def search(self, keyword: str) -> list[dict[str, str]]:
        self.calls.append(keyword)
        if self.raise_error:
            raise RuntimeError("provider down")
        return self.results


def _service(
    conn: FakeConn | None = None,
    provider: FakeSearchProvider | None = None,
) -> PortfolioService:
    return PortfolioService(
        FakePool(conn or FakeConn()),
        search_provider=provider or FakeSearchProvider(),
    )


def _sqls(conn: FakeConn) -> list[str]:
    return [c[0] for c in conn.calls]


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def test_normalize_symbol_rules() -> None:
    assert normalize_symbol("600519") == "600519"
    assert normalize_symbol(" 510300.SH ") == "510300"
    assert normalize_symbol("110022.SZ") == "110022"
    with pytest.raises(ValidationError):
        normalize_symbol("abc")
    with pytest.raises(ValidationError):
        normalize_symbol("60051")


def test_rebuild_confirmed_holdings_replays_buys_sells() -> None:
    events = [
        {
            "account_id": "acc-1",
            "event_type": "BUY",
            "legs": [
                {"leg_type": "INSTRUMENT", "instrument_id": "inst-1", "quantity": "100", "amount": None, "unit_price": "10"},
                {"leg_type": "CASH", "instrument_id": None, "quantity": None, "amount": "-1000", "unit_price": None},
                {"leg_type": "FEE", "instrument_id": None, "quantity": None, "amount": "-5", "unit_price": None},
            ],
        },
        {
            "account_id": "acc-1",
            "event_type": "SELL",
            "legs": [
                {"leg_type": "INSTRUMENT", "instrument_id": "inst-1", "quantity": "-40", "amount": None, "unit_price": "12"},
            ],
        },
    ]
    holdings = rebuild_confirmed_holdings(events)
    holding = holdings[("acc-1", "inst-1")]
    assert holding.quantity == Decimal("60.0000")
    # 成本 = 1000 + 5 手续费；卖出 40 后按比例摊薄
    assert holding.cost_basis == Decimal("603.00")
    assert holding.avg_cost == Decimal("10.0500")


def test_rebuild_confirmed_holdings_rejects_negative_position() -> None:
    events = [
        {
            "account_id": "acc-1",
            "event_type": "SELL",
            "legs": [
                {"leg_type": "INSTRUMENT", "instrument_id": "inst-1", "quantity": "-100", "amount": None, "unit_price": "12"},
            ],
        },
    ]
    with pytest.raises(ValueError):
        rebuild_confirmed_holdings(events)


def test_derive_account_nav_fails_closed_on_unpriced_position() -> None:
    valuation = derive_account_nav(
        cash_balance=Decimal("100.00"),
        principal=Decimal("1000.00"),
        priced_positions_value=Decimal("0.00"),
        total_position_count=1,
        priced_position_count=0,
    )
    assert valuation.total_nav is None
    assert valuation.quality == "ESTIMATED"
    assert valuation.coverage_pct == Decimal("0.00")

    valuation = derive_account_nav(
        cash_balance=Decimal("100.00"),
        principal=Decimal("1000.00"),
        priced_positions_value=Decimal("900.00"),
        total_position_count=1,
        priced_position_count=1,
    )
    assert valuation.total_nav == Decimal("1000.00")
    assert valuation.quality == "FRESH"


def test_formatting_helpers() -> None:
    assert fmt_money(Decimal("1.005")) == "1.01"
    assert fmt_money(None) == "0.00"
    assert fmt_qty(Decimal("1.23456")) == "1.2346"


# ---------------------------------------------------------------------------
# 持仓管理
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_add_position_writes_buy_event_legs_and_recomputes() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        None,  # 幂等键未使用
        {
            # 本地主数据命中
            "id": "inst-1",
            "asset_type": "CN_STOCK",
            "canonical_name": "贵州茅台",
            "primary_market": "SSE",
        },
    ]
    conn.fetchval_results = ["acc-1", "evt-1"]
    service = _service(conn)

    result = await service.add_position(
        ACTOR,
        account_id="acc-1",
        symbol="600519",
        asset_type="STOCK",
        quantity=Decimal("100"),
        unit_cost=Decimal("10.00"),
        fee=Decimal("5.00"),
        trade_date=date(2026, 8, 20),
        idempotency_key="import-1",
        name="贵州茅台",
    )

    sqls = _sqls(conn)
    event_insert = next(s for s in sqls if "INSERT INTO ledger.ledger_events" in s)
    assert "'BUY'" in event_insert
    legs_call = next(c for c in conn.calls if "INSERT INTO ledger.ledger_legs" in c[0])
    leg_rows = legs_call[1]  # executemany 的参数即腿元组
    assert len(leg_rows) == 3  # INSTRUMENT + CASH + FEE
    assert leg_rows[0][2] == "INSTRUMENT" and leg_rows[0][3] == "inst-1"
    assert leg_rows[0][4] == Decimal("100")
    assert leg_rows[1][5] == Decimal("-1000.00")
    assert leg_rows[2][5] == Decimal("-5.00")
    assert any("DELETE FROM ledger.position_snapshots" in s for s in sqls)
    # 无活动账户时 NAV 快照自然跳过；确认账户枚举查询已执行
    assert any("SELECT id AS account_id" in s for s in sqls)
    assert any("PORTFOLIO_POSITION_ADDED" in c[1] for c in conn.calls if "audit.audit_log" in c[0])
    assert result["eventId"] == "evt-1"
    assert result["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_add_position_resolves_missing_instrument_via_provider() -> None:
    conn = FakeConn()
    conn.fetchval_results = ["acc-1", "new-inst-1", "evt-1"]
    conn.fetchrow_results = [None, None]  # 幂等键、本地标的主数据均未命中
    provider = FakeSearchProvider(
        results=[{"symbol": "688981", "name": "中芯国际", "market": "SSE", "type": "STOCK", "source": "live"}]
    )
    service = _service(conn, provider)

    result = await service.add_position(
        ACTOR,
        account_id="acc-1",
        symbol="688981",
        asset_type="STOCK",
        quantity=Decimal("10"),
        unit_cost=Decimal("50"),
        trade_date=date(2026, 8, 20),
        idempotency_key="import-2",
    )

    sqls = _sqls(conn)
    assert any("INSERT INTO reference.instruments" in s for s in sqls)
    assert any("INSERT INTO reference.instrument_aliases" in s for s in sqls)
    assert result["name"] == "中芯国际"


@pytest.mark.asyncio
async def test_add_position_fails_when_provider_cannot_verify() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [None, None]  # 幂等键、本地主数据均未命中
    service = _service(conn, FakeSearchProvider(results=[]))
    with pytest.raises(NotFoundError):
        await service.add_position(
            ACTOR,
            account_id="acc-1",
            symbol="999999",
            asset_type="STOCK",
            quantity=Decimal("1"),
            unit_cost=Decimal("1"),
            trade_date=date(2026, 8, 20),
            idempotency_key="import-3",
        )


@pytest.mark.asyncio
async def test_add_position_replays_same_idempotency_key() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "event_id": "evt-old",
            "event_type": "BUY",
            "account_id": "acc-1",
            "trade_date": date(2026, 8, 20),
            "description": "买入持仓 600519",
        }
    ]
    service = _service(conn)

    result = await service.add_position(
        ACTOR,
        account_id="acc-1",
        symbol="600519",
        asset_type="STOCK",
        quantity=Decimal("100"),
        unit_cost=Decimal("10"),
        trade_date=date(2026, 8, 20),
        idempotency_key="import-1",
    )

    assert result["eventId"] == "evt-old"
    assert result["replayed"] is True
    # 幂等重放不得再次写事件/腿
    assert not any("INSERT INTO ledger.ledger_events" in s for s in _sqls(conn))


@pytest.mark.asyncio
async def test_add_position_validates_inputs() -> None:
    service = _service()
    with pytest.raises(ValidationError):
        await service.add_position(
            ACTOR, account_id="a", symbol="600519", asset_type="STOCK",
            quantity=Decimal("0"), unit_cost=Decimal("1"),
            trade_date=date(2026, 8, 20), idempotency_key="k",
        )
    with pytest.raises(ValidationError):
        await service.add_position(
            ACTOR, account_id="a", symbol="600519", asset_type="OPTION",
            quantity=Decimal("1"), unit_cost=Decimal("1"),
            trade_date=date(2026, 8, 20), idempotency_key="k",
        )
    with pytest.raises(ValidationError):
        await service.add_position(
            ACTOR, account_id="a", symbol="600519", asset_type="STOCK",
            quantity=Decimal("1"), unit_cost=Decimal("1"),
            trade_date=date(2026, 8, 20), idempotency_key="",
        )


@pytest.mark.asyncio
async def test_edit_position_reverses_then_rebuys() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        None,  # 幂等键未使用
        {
            # 原 BUY 事件（可冲销）
            "id": "evt-original",
            "account_id": "acc-1",
            "trade_date": date(2026, 8, 20),
            "description": "买入持仓 600519",
            "instrument_id": "inst-1",
            "ticker": "600519",
        },
    ]
    conn.fetchval_results = ["evt-reversal", "evt-rebuy"]
    service = _service(conn)

    result = await service.edit_position(
        ACTOR,
        event_id="evt-original",
        quantity=Decimal("50"),
        unit_cost=Decimal("12.00"),
        idempotency_key="edit-1",
    )

    sqls = _sqls(conn)
    assert any("'REVERSAL'" in s and "reverses_event_id" in s for s in sqls)
    assert result["reversalEventId"] == "evt-reversal"
    assert result["eventId"] == "evt-rebuy"


@pytest.mark.asyncio
async def test_delete_position_writes_reversal() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        None,  # 幂等键未使用
        {
            "id": "evt-delete",
            "account_id": "acc-1",
            "trade_date": date(2026, 8, 20),
            "description": "买入持仓 600519",
            "instrument_id": "inst-1",
            "ticker": "600519",
        },
    ]
    conn.fetchval_results = ["evt-reversal"]
    service = _service(conn)

    result = await service.delete_position(
        ACTOR, event_id="evt-delete", idempotency_key="delete-1"
    )

    sqls = _sqls(conn)
    assert any("'REVERSAL'" in s and "reverses_event_id" in s for s in sqls)
    assert result["reversalEventId"] == "evt-reversal"


@pytest.mark.asyncio
async def test_delete_position_rejects_already_reversed() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [None, None]  # 幂等键未用；_load_buy_event 未命中
    service = _service(conn)
    with pytest.raises(NotFoundError):
        await service.delete_position(
            ACTOR, event_id="evt-gone", idempotency_key="delete-2"
        )


@pytest.mark.asyncio
async def test_list_positions_returns_market_values() -> None:
    conn = FakeConn()
    conn.fetch_results = [
        [
            {
                "account_id": "acc-1",
                "instrument_id": "inst-1",
                "as_of": date(2026, 8, 20),
                "quantity": Decimal("100"),
                "avg_cost": Decimal("10.00"),
                "market_price": Decimal("12.50"),
                "market_value": Decimal("1250.00"),
                "unrealized_pnl": Decimal("250.00"),
                "data_quality_status": "FRESH",
                "canonical_name": "贵州茅台",
                "asset_type": "CN_STOCK",
                "primary_market": "SSE",
                "ticker": "600519",
            }
        ]
    ]
    service = _service(conn)

    result = await service.list_positions(conn, ACTOR)  # type: ignore[arg-type]

    item = result["items"][0]
    assert item["symbol"] == "600519"
    assert item["marketValue"] == "1250.00"
    assert item["pnl"] == "250.00"
    assert item["pnlRate"] == "25.00"
    assert item["quality"] == "FRESH"
    assert result["totalMarketValue"] == "1250.00"


# ---------------------------------------------------------------------------
# 账户管理
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_account() -> None:
    conn = FakeConn()
    conn.fetchval_results = ["acc-new"]
    service = _service(conn)

    result = await service.create_account(
        ACTOR, name=" 华泰证券 ", account_type="BROKERAGE", base_currency="cny"
    )

    insert = next(c for c in conn.calls if "INSERT INTO identity.accounts" in c[0])
    assert insert[1][1] == "BROKERAGE"
    assert insert[1][2] == "华泰证券"
    assert insert[1][3] == "CNY"
    assert result["accountId"] == "acc-new"

    with pytest.raises(ValidationError):
        await service.create_account(ACTOR, name="", account_type="BROKERAGE")
    with pytest.raises(ValidationError):
        await service.create_account(ACTOR, name="x", account_type="CRYPTO")


@pytest.mark.asyncio
async def test_archive_account_blocks_nonzero_balance() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "acc-1",
            "name": "华泰证券",
            "cash_balance": Decimal("100.00"),
            "open_quantity": Decimal("0"),
        }
    ]
    service = _service(conn)
    with pytest.raises(ForbiddenError):
        await service.archive_account(conn, ACTOR, account_id="acc-1")  # type: ignore[arg-type]

    # 零余额零持仓 → 可归档
    conn.fetchrow_results = [
        {"id": "acc-1", "name": "华泰证券", "cash_balance": Decimal("0"), "open_quantity": Decimal("0")},
        {"id": "acc-1", "status": "CLOSED"},
    ]
    result = await service.archive_account(conn, ACTOR, account_id="acc-1")  # type: ignore[arg-type]
    assert result["status"] == "CLOSED"


# ---------------------------------------------------------------------------
# 现金流
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cash_flow_events_use_signed_legs() -> None:
    cases = [
        ("record_cash_in", "CASH_IN", "CASH", "+"),
        ("record_cash_out", "CASH_OUT", "CASH", "-"),
        ("record_fee", "FEE", "FEE", "-"),
        ("record_dividend", "DIVIDEND_CASH", "CASH", "+"),
        ("record_interest", "ADJUSTMENT", "CASH", "+"),
    ]
    for method, event_type, leg_type, sign in cases:
        conn = FakeConn()
        conn.fetchrow_results = [None]
        conn.fetchval_results = ["acc-1", "evt-x"]
        service = _service(conn)
        result = await getattr(service, method)(
            ACTOR,
            account_id="acc-1",
            amount=Decimal("100.00"),
            trade_date=date(2026, 8, 20),
            idempotency_key=f"flow-{event_type}",
        )
        assert result["eventType"] == event_type
        leg_call = next(c for c in conn.calls if "INSERT INTO ledger.ledger_legs" in c[0])
        assert leg_call[1][2] == leg_type
        assert leg_call[1][3] == Decimal(("100.00" if sign == "+" else "-100.00"))


@pytest.mark.asyncio
async def test_cash_flow_rejects_non_positive_amount() -> None:
    service = _service()
    with pytest.raises(ValidationError):
        await service.record_cash_in(
            ACTOR,
            account_id="acc-1",
            amount=Decimal("0"),
            trade_date=date(2026, 8, 20),
            idempotency_key="flow-0",
        )


# ---------------------------------------------------------------------------
# 搜索与快照重算
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_merges_local_and_external() -> None:
    conn = FakeConn()
    conn.fetch_results = [
        [
            {
                "id": "inst-1",
                "asset_type": "CN_STOCK",
                "canonical_name": "贵州茅台",
                "primary_market": "SSE",
                "ticker": "600519",
            }
        ]
    ]
    provider = FakeSearchProvider(
        results=[{"symbol": "600519", "name": "贵州茅台", "market": "SSE", "type": "STOCK", "source": "live"}]
    )
    service = _service(conn, provider)

    result = await service.search_instruments(ACTOR, keyword="600519")

    assert result["total"] >= 1
    assert any(item["symbol"] == "600519" for item in result["items"])
    local_item = next(item for item in result["items"] if item["source"] == "local")
    assert local_item["type"] == "STOCK"

    with pytest.raises(ValidationError):
        await service.search_instruments(ACTOR, keyword="  ")


@pytest.mark.asyncio
async def test_search_survives_provider_outage() -> None:
    conn = FakeConn()
    conn.fetch_results = [[]]
    provider = FakeSearchProvider()
    provider.raise_error = True
    service = _service(conn, provider)

    result = await service.search_instruments(ACTOR, keyword="茅台")
    assert result["total"] == 0  # 外部源故障不抛异常


@pytest.mark.asyncio
async def test_recompute_snapshots_refreshes_positions_and_navs() -> None:
    conn = FakeConn()
    conn.fetch_results = [[], []]  # 无事件、无账户
    service = _service(conn)

    result = await service.recompute_snapshots(ACTOR)

    sqls = _sqls(conn)
    assert any("DELETE FROM ledger.position_snapshots" in s for s in sqls)
    assert result == {"positionsUpdated": 0, "accountsUpdated": 0}
