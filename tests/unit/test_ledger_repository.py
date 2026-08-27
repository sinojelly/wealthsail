"""modules/ledger/repository 持久化仓储单元测试。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from modules.ledger.repository import LedgerRepository


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.fetchval_results: list[Any] = []
        self.fetchrow_results: list[Any] = []
        self.fetch_results: list[list[Any]] = []
        self.execute_result = "UPDATE 1"

    async def fetchval(self, query, *args) -> Any:
        self.calls.append((query, args))
        if not self.fetchval_results:
            return None
        return self.fetchval_results.pop(0)

    async def fetchrow(self, query, *args) -> Any:
        self.calls.append((query, args))
        if not self.fetchrow_results:
            return None
        return self.fetchrow_results.pop(0)

    async def fetch(self, query, *args) -> Any:
        self.calls.append((query, args))
        if not self.fetch_results:
            return []
        return self.fetch_results.pop(0)

    async def execute(self, query, *args) -> Any:
        self.calls.append((query, args))
        return self.execute_result

    async def executemany(self, query, args) -> Any:
        self.calls.append((query, tuple(args)))
        return "OK"

    def transaction(self):
        class _Tx:
            def __init__(self, conn):
                self._conn = conn

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, *_args):
                return None

        return _Tx(self)


def _sqls(conn: FakeConn) -> list[str]:
    return [c[0] for c in conn.calls]


# ---------------------------------------------------------------------------
# persist_event
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_event_writes_events_and_legs() -> None:
    conn = FakeConn()
    conn.fetchval_results = ["evt-1"]
    repo = LedgerRepository()

    result = await repo.persist_event(
        conn,
        tenant_id="tenant-1",
        account_id="acc-1",
        event_type="BUY",
        trade_date=date(2026, 8, 20),
        idempotency_key="buy-1",
        description="买入持仓",
        legs=[
            ("INSTRUMENT", "inst-1", Decimal("100"), None, Decimal("10.00")),
            ("CASH", None, None, Decimal("-1000.00"), None),
        ],
    )

    assert result == {"eventId": "evt-1", "replayed": False}
    event_insert = next(c for c in conn.calls if "INSERT INTO ledger.ledger_events" in c[0])
    assert event_insert[1][2] == "BUY"
    assert event_insert[1][5] == "buy-1"
    legs_call = next(c for c in conn.calls if "INSERT INTO ledger.ledger_legs" in c[0])
    assert len(legs_call[1]) == 2
    assert legs_call[1][0][2] == "INSTRUMENT"
    assert legs_call[1][1][5] == Decimal("-1000.00")


@pytest.mark.asyncio
async def test_persist_event_replays_same_idempotency_key() -> None:
    conn = FakeConn()
    conn.fetchval_results = [None]  # INSERT 冲突 → 无返回
    conn.fetchrow_results = [{"id": "evt-old"}]
    repo = LedgerRepository()

    result = await repo.persist_event(
        conn,
        tenant_id="tenant-1",
        account_id="acc-1",
        event_type="BUY",
        trade_date=date(2026, 8, 20),
        idempotency_key="buy-1",
        description="重复提交",
        legs=[("CASH", None, None, Decimal("-1.00"), None)],
    )

    assert result == {"eventId": "evt-old", "replayed": True}
    # 幂等重放不得再次写腿
    assert not any("INSERT INTO ledger.ledger_legs" in s for s in _sqls(conn))


@pytest.mark.asyncio
async def test_persist_event_validates_types() -> None:
    repo = LedgerRepository()
    conn = FakeConn()
    with pytest.raises(ValueError):
        await repo.persist_event(
            conn, tenant_id="t", account_id="a", event_type="MAGIC",
            trade_date=date(2026, 8, 20), idempotency_key="k",
            description="x", legs=[],
        )
    with pytest.raises(ValueError):
        await repo.persist_event(
            conn, tenant_id="t", account_id="a", event_type="BUY",
            trade_date=date(2026, 8, 20), idempotency_key="k",
            description="x", legs=[("NOPE", None, None, None, None)],
        )


# ---------------------------------------------------------------------------
# record_trade（成交 → 账本闭环）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_record_trade_writes_event_legs_and_confirmed_fill() -> None:
    conn = FakeConn()
    conn.fetchval_results = ["evt-1", "fill-1"]  # persist_event 返回 eventId；fills INSERT 返回 id
    repo = LedgerRepository()

    result = await repo.record_trade(
        conn,
        tenant_id="tenant-1",
        account_id="acc-1",
        instrument_id="inst-1",
        side="buy",
        quantity=Decimal("100"),
        unit_price=Decimal("10.00"),
        fee=Decimal("5.00"),
        trade_date=date(2026, 8, 20),
        idempotency_key="fill-buy-1",
        created_by="user-1",
        decision_id="dec-1",
    )

    assert result["eventId"] == "evt-1"
    assert result["fillId"] == "fill-1"
    sqls = _sqls(conn)
    assert any("INSERT INTO ledger.ledger_events" in s for s in sqls)
    legs_call = next(c for c in conn.calls if "INSERT INTO ledger.ledger_legs" in c[0])
    leg_rows = legs_call[1]
    assert len(leg_rows) == 3  # INSTRUMENT + CASH + FEE
    assert leg_rows[0][2] == "INSTRUMENT" and leg_rows[0][4] == Decimal("100")
    assert leg_rows[1][5] == Decimal("-1000.00")
    assert leg_rows[2][5] == Decimal("-5.00")
    fill_insert = next(c for c in conn.calls if "INSERT INTO ledger.trade_fills" in c[0])
    assert fill_insert[1][4] == "BUY"
    assert fill_insert[1][9] == "evt-1"  # ledger_event_id 关联
    assert fill_insert[1][10] == "user-1"


@pytest.mark.asyncio
async def test_record_trade_sell_uses_negative_instrument_leg() -> None:
    conn = FakeConn()
    conn.fetchval_results = ["evt-2", "fill-2"]
    repo = LedgerRepository()

    result = await repo.record_trade(
        conn,
        tenant_id="tenant-1",
        account_id="acc-1",
        instrument_id="inst-1",
        side="SELL",
        quantity=Decimal("40"),
        unit_price=Decimal("12.00"),
        trade_date=date(2026, 8, 21),
        idempotency_key="fill-sell-1",
        created_by="user-1",
    )

    assert result["eventId"] == "evt-2"
    legs_call = next(c for c in conn.calls if "INSERT INTO ledger.ledger_legs" in c[0])
    leg_rows = legs_call[1]
    assert leg_rows[0][4] == Decimal("-40")
    assert leg_rows[1][5] == Decimal("480.00")  # 卖出现金为正


@pytest.mark.asyncio
async def test_record_trade_rejects_invalid_side_and_quantity() -> None:
    repo = LedgerRepository()
    conn = FakeConn()
    with pytest.raises(ValueError):
        await repo.record_trade(
            conn, tenant_id="t", account_id="a", instrument_id="i", side="HODL",
            quantity=Decimal("1"), unit_price=Decimal("1"),
            trade_date=date(2026, 8, 20), idempotency_key="k", created_by="u",
        )
    with pytest.raises(ValueError):
        await repo.record_trade(
            conn, tenant_id="t", account_id="a", instrument_id="i", side="BUY",
            quantity=Decimal("0"), unit_price=Decimal("1"),
            trade_date=date(2026, 8, 20), idempotency_key="k", created_by="u",
        )


# ---------------------------------------------------------------------------
# 草稿 → 确认状态机
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_trade_fill_draft_then_confirm() -> None:
    conn = FakeConn()
    conn.fetchval_results = ["fill-draft-1"]
    repo = LedgerRepository()

    draft = await repo.create_trade_fill_draft(
        conn,
        tenant_id="tenant-1",
        account_id="acc-1",
        instrument_id="inst-1",
        side="BUY",
        quantity=Decimal("10"),
        unit_price=Decimal("50.00"),
        trade_date=date(2026, 8, 21),
        idempotency_key="draft-1",
        created_by="user-1",
        decision_id="dec-1",
    )
    assert draft == {"fillId": "fill-draft-1", "status": "DRAFT", "replayed": False}
    fill_insert = next(c for c in conn.calls if "INSERT INTO ledger.trade_fills" in c[0])
    assert "'DRAFT'" in fill_insert[0]  # status 为 SQL 常量
    assert fill_insert[1][10] == "draft-1"  # idempotency_key
    assert fill_insert[1][3] == "dec-1"  # decision_id 关联
    assert not any("INSERT INTO ledger.ledger_events" in s for s in _sqls(conn))  # 草稿不写账本

    # 确认：读草稿（FOR UPDATE）→ 写事件/腿 → 回填
    conn2 = FakeConn()
    conn2.fetchrow_results = [
        {
            "id": "fill-draft-1",
            "account_id": "acc-1",
            "instrument_id": "inst-1",
            "side": "BUY",
            "quantity": Decimal("10"),
            "unit_price": Decimal("50.00"),
            "fee": Decimal("0.00"),
            "trade_date": date(2026, 8, 21),
            "decision_id": "dec-1",
        }
    ]
    conn2.fetchval_results = ["evt-confirm"]
    conn2.execute_result = "UPDATE 1"
    confirmed = await repo.confirm_trade_fill(
        conn2, tenant_id="tenant-1", fill_id="fill-draft-1", idempotency_key="confirm-1"
    )
    assert confirmed == {"fillId": "fill-draft-1", "status": "CONFIRMED", "eventId": "evt-confirm", "replayed": False}
    update = next(c for c in conn2.calls if "UPDATE ledger.trade_fills" in c[0])
    assert update[1][0] == "evt-confirm"
    assert any("INSERT INTO ledger.ledger_events" in s for s in _sqls(conn2))


@pytest.mark.asyncio
async def test_confirm_missing_draft_is_noop() -> None:
    conn = FakeConn()  # fetchrow → None
    repo = LedgerRepository()
    result = await repo.confirm_trade_fill(
        conn, tenant_id="tenant-1", fill_id="ghost", idempotency_key="confirm-x"
    )
    assert result == {"fillId": "ghost", "status": "NOT_FOUND_OR_NOT_DRAFT"}
    assert not any("INSERT INTO ledger.ledger_events" in s for s in _sqls(conn))


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_trade_fills() -> None:
    conn = FakeConn()
    conn.fetch_results = [
        [
            {
                "id": "fill-1",
                "side": "BUY",
                "quantity": Decimal("100"),
                "unit_price": Decimal("10.00"),
                "fee": Decimal("0.00"),
                "trade_date": date(2026, 8, 20),
                "source_type": "USER_CONFIRMED",
                "status": "CONFIRMED",
                "ledger_event_id": "evt-1",
                "created_at": None,
                "confirmed_at": None,
                "canonical_name": "贵州茅台",
                "ticker": "600519",
            }
        ]
    ]
    repo = LedgerRepository()
    result = await repo.list_trade_fills(conn, "tenant-1")
    item = result["items"][0]
    assert item["symbol"] == "600519"
    assert item["status"] == "CONFIRMED"
    assert item["eventId"] == "evt-1"
    assert result["total"] == 1
