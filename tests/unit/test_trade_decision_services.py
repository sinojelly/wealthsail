from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from modules.errors import ConflictError
from modules.ledger.repository import LedgerRepository
from modules.research.service import DecisionService


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
