from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.errors import (
    IdempotencyConflictError as ConflictError,
    ValidationError_,
)
from backend.research.decisions import DecisionService
from backend.trading.repository import LedgerRepository
from backend.trading import service as trading_service


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.fetchval_result = None
        self.fetchrow_results: list[dict | None] = []
        self.execute_result = "UPDATE 1"

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self.fetchval_result

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self.fetchrow_results.pop(0) if self.fetchrow_results else None

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self.execute_result

    async def executemany(self, sql, args):
        self.calls.append((sql, tuple(args)))


class FakeTradeRepository:
    def __init__(self) -> None:
        self.confirmed = False

    async def confirm_trade_fill(self, _conn, **kwargs):
        self.confirmed = True
        return {
            "fillId": kwargs["fill_id"],
            "status": "CONFIRMED",
            "eventId": "event-1",
            "replayed": False,
        }


@pytest.mark.asyncio
async def test_trade_service_rejects_oversell_before_writing() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "fill-1",
            "account_id": "account-1",
            "instrument_id": "instrument-1",
            "side": "SELL",
            "quantity": Decimal("5"),
            "unit_price": Decimal("10"),
            "fee": Decimal("0"),
            "status": "DRAFT",
        }
    ]
    values = iter(["account-1", Decimal("2")])

    async def fetchval(sql, *args):
        conn.calls.append((sql, args))
        return next(values)

    conn.fetchval = fetchval  # type: ignore[method-assign]
    repository = FakeTradeRepository()

    with pytest.raises(ValidationError_, match="持仓不足"):
        await trading_service.confirm_trade_fill(
            conn,
            tenant_id="tenant-1",
            fill_id="fill-1",
            idempotency_key="confirm-1",
            confirmed_by="user-1",
            repository=repository,
        )
    assert repository.confirmed is False
    holding_sql = next(sql for sql, _ in conn.calls if "SUM(leg.quantity)" in sql)
    assert "event.event_type <> 'REVERSAL'" in holding_sql


@pytest.mark.asyncio
async def test_trade_service_requires_exit_rule_for_buy() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "fill-1",
            "account_id": "account-1",
            "instrument_id": "instrument-1",
            "side": "BUY",
            "quantity": Decimal("5"),
            "unit_price": Decimal("10"),
            "fee": Decimal("1"),
            "status": "DRAFT",
        }
    ]
    values = iter(["account-1", False])

    async def fetchval(sql, *args):
        conn.calls.append((sql, args))
        return next(values)

    conn.fetchval = fetchval  # type: ignore[method-assign]
    repository = FakeTradeRepository()

    with pytest.raises(ValidationError_, match="出局条件"):
        await trading_service.confirm_trade_fill(
            conn,
            tenant_id="tenant-1",
            fill_id="fill-1",
            idempotency_key="confirm-1",
            confirmed_by="user-1",
            repository=repository,
        )
    assert repository.confirmed is False


@pytest.mark.asyncio
async def test_trade_service_rejects_insufficient_cash() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "fill-1",
            "account_id": "account-1",
            "instrument_id": "instrument-1",
            "side": "BUY",
            "quantity": Decimal("5"),
            "unit_price": Decimal("10"),
            "fee": Decimal("1"),
            "status": "DRAFT",
        }
    ]
    values = iter(["account-1", True, Decimal("50")])

    async def fetchval(sql, *args):
        conn.calls.append((sql, args))
        return next(values)

    conn.fetchval = fetchval  # type: ignore[method-assign]
    repository = FakeTradeRepository()

    with pytest.raises(ValidationError_, match="现金不足"):
        await trading_service.confirm_trade_fill(
            conn,
            tenant_id="tenant-1",
            fill_id="fill-1",
            idempotency_key="confirm-1",
            confirmed_by="user-1",
            repository=repository,
        )
    assert repository.confirmed is False
    cash_sql = next(sql for sql, _ in conn.calls if "SUM(leg.amount)" in sql)
    assert "event.event_type <> 'REVERSAL'" in cash_sql


@pytest.mark.asyncio
async def test_trade_service_rebuilds_projection_after_confirmation(monkeypatch) -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "fill-1",
            "account_id": "account-1",
            "instrument_id": "instrument-1",
            "side": "SELL",
            "quantity": Decimal("2"),
            "unit_price": Decimal("10"),
            "fee": Decimal("0"),
            "status": "DRAFT",
        }
    ]
    values = iter(["account-1", Decimal("3")])

    async def fetchval(sql, *args):
        conn.calls.append((sql, args))
        return next(values)

    conn.fetchval = fetchval  # type: ignore[method-assign]
    recompute = AsyncMock()
    monkeypatch.setattr(
        trading_service.portfolio_service,
        "recompute_projections",
        recompute,
    )
    repository = FakeTradeRepository()

    result = await trading_service.confirm_trade_fill(
        conn,
        tenant_id="tenant-1",
        fill_id="fill-1",
        idempotency_key="confirm-1",
        confirmed_by="user-1",
        repository=repository,
    )

    assert result["status"] == "CONFIRMED"
    recompute.assert_awaited_once_with(conn, tenant_id="tenant-1")


@pytest.mark.asyncio
async def test_decision_write_is_persisted_and_audited() -> None:
    conn = FakeConn()
    conn.fetchval_result = "decision-1"

    result = await DecisionService().record_decision(
        conn,
        tenant_id="tenant-1",
        user_id="user-1",
        decision_action="ACCEPTED",
        idempotency_key="decision-key",
        recommendation_id="recommendation-1",
    )

    assert result == {"decisionId": "decision-1", "replayed": False}
    assert any("INSERT INTO reporting.decisions" in sql for sql, _ in conn.calls)
    assert any("RESEARCH_DECISION_RECORDED" in sql for sql, _ in conn.calls)
    assert not any("ledger.trade_fills" in sql for sql, _ in conn.calls)
    audit = next(args for sql, args in conn.calls if "RESEARCH_DECISION_RECORDED" in sql)
    assert isinstance(audit[-1], str)


@pytest.mark.asyncio
async def test_decision_correction_supersedes_the_previous_record() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "decision-old",
            "user_id": "user-1",
            "report_id": "report-1",
            "recommendation_id": "recommendation-1",
            "status": "RECORDED",
        }
    ]
    conn.fetchval_result = "decision-new"

    result = await DecisionService().record_decision(
        conn,
        tenant_id="tenant-1",
        user_id="user-1",
        decision_action="ACCEPTED",
        idempotency_key="decision-correction",
        report_id="report-1",
        recommendation_id="recommendation-1",
        previous_decision_id="decision-old",
        correction="补充新的证据",
    )

    assert result["decisionId"] == "decision-new"
    assert any(
        "status = 'SUPERSEDED'" in sql and args[0] == "decision-old"
        for sql, args in conn.calls
    )


@pytest.mark.asyncio
async def test_decision_idempotency_key_rejects_a_different_command() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "decision-1",
            "report_id": None,
            "recommendation_id": "recommendation-1",
            "user_id": "user-1",
            "decision_action": "IGNORED",
            "status": "RECORDED",
            "reason": None,
            "correction": None,
            "previous_decision_id": None,
        }
    ]

    with pytest.raises(ConflictError):
        await DecisionService().record_decision(
            conn,
            tenant_id="tenant-1",
            user_id="user-1",
            decision_action="ACCEPTED",
            idempotency_key="reused-key",
            recommendation_id="recommendation-1",
        )


@pytest.mark.asyncio
async def test_confirmed_trade_retry_returns_the_original_event() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "fill-1",
            "account_id": "account-1",
            "instrument_id": "instrument-1",
            "side": "BUY",
            "quantity": Decimal("2"),
            "unit_price": Decimal("10"),
            "fee": Decimal("0"),
            "trade_date": date(2026, 8, 28),
            "decision_id": None,
            "status": "CONFIRMED",
            "ledger_event_id": "event-1",
            "event_key": "confirm-key",
        }
    ]

    result = await LedgerRepository().confirm_trade_fill(
        conn,
        tenant_id="tenant-1",
        fill_id="fill-1",
        idempotency_key="confirm-key",
        confirmed_by="user-1",
    )

    assert result == {
        "fillId": "fill-1",
        "status": "CONFIRMED",
        "eventId": "event-1",
        "replayed": True,
    }
    assert not any(sql.lstrip().startswith("UPDATE") for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_trade_draft_idempotency_key_rejects_changed_payload() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "fill-1",
            "account_id": "account-1",
            "instrument_id": "instrument-1",
            "decision_id": None,
            "side": "SELL",
            "quantity": Decimal("1"),
            "unit_price": Decimal("10"),
            "fee": Decimal("0"),
            "trade_date": date(2026, 8, 28),
            "created_by": "user-1",
            "status": "DRAFT",
        }
    ]

    with pytest.raises(ConflictError):
        await LedgerRepository().create_trade_fill_draft(
            conn,
            tenant_id="tenant-1",
            account_id="account-1",
            instrument_id="instrument-1",
            side="BUY",
            quantity=Decimal("1"),
            unit_price=Decimal("10"),
            trade_date=date(2026, 8, 28),
            idempotency_key="draft-key",
            created_by="user-1",
        )


@pytest.mark.asyncio
async def test_event_idempotency_rejects_different_legs() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "id": "event-1",
            "account_id": "account-1",
            "event_type": "BUY",
            "status": "CONFIRMED",
            "trade_date": date(2026, 8, 28),
            "description": "确认成交草稿 fill-1",
            "reverses_event_id": None,
        }
    ]
    # Existing event legs differ from the command being replayed.
    async def fetch(sql, *args):
        conn.calls.append((sql, args))
        return [
            {
                "leg_type": "INSTRUMENT",
                "instrument_id": "instrument-1",
                "quantity": Decimal("1"),
                "amount": None,
                "unit_price": Decimal("10"),
            },
            {
                "leg_type": "CASH",
                "instrument_id": None,
                "quantity": None,
                "amount": Decimal("-10"),
                "unit_price": None,
            },
        ]

    conn.fetch = fetch  # type: ignore[method-assign]

    with pytest.raises(ConflictError):
        await LedgerRepository().persist_event(
            conn,
            tenant_id="tenant-1",
            account_id="account-1",
            event_type="BUY",
            trade_date=date(2026, 8, 28),
            idempotency_key="confirm-key",
            description="确认成交草稿 fill-2",
            legs=[
                (
                    "INSTRUMENT",
                    "instrument-2",
                    Decimal("2"),
                    None,
                    Decimal("12"),
                ),
                ("CASH", None, None, Decimal("-24"), None),
            ],
        )
