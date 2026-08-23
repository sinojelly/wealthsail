"""Unit tests for WealthSail immutable portfolio-event ledger service."""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
import uuid
import pytest

from modules.ledger.service import LedgerService, EventType


def test_cash_in_and_rebuild_projection() -> None:
    """Tests capital deposit and projection rebuilding."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    ledger = LedgerService(tenant_id=tenant_id, account_id=account_id)

    event = ledger.record_cash_in(
        amount=Decimal("100000.00"),
        trade_date=date(2026, 8, 23),
        idempotency_key="TEST_CASH_IN_01",
    )

    assert event.event_type == EventType.CASH_IN
    projection = ledger.rebuild_projection()
    assert projection.cash_balance == Decimal("100000.00")
    assert len(projection.positions) == 0


def test_buy_security_and_cost_basis() -> None:
    """Tests security purchase, cash deduction, and unit cost calculation."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    ledger = LedgerService(tenant_id=tenant_id, account_id=account_id)

    # Initial deposit
    ledger.record_cash_in(
        amount=Decimal("50000.00"),
        trade_date=date(2026, 8, 23),
        idempotency_key="DEPOSIT_01",
    )

    # Buy 300 shares @ 100.00 with 10.00 fee
    ledger.record_buy(
        instrument_id=inst_id,
        quantity=Decimal("300.0000"),
        unit_price=Decimal("100.00"),
        fee=Decimal("10.00"),
        trade_date=date(2026, 8, 23),
        idempotency_key="BUY_01",
    )

    proj = ledger.rebuild_projection()
    # Cash: 50,000 - 30,000 - 10 = 19,990.00
    assert proj.cash_balance == Decimal("19990.00")
    assert inst_id in proj.positions
    pos = proj.positions[inst_id]
    assert pos.quantity == Decimal("300.0000")
    # Cost basis: 30,010.00 / 300 = 100.0333
    assert pos.avg_cost == Decimal("100.0333")


def test_fund_dividend_cash_flow() -> None:
    """Tests cash dividend: increases cash balance, preserves position shares."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    fund_id = uuid.uuid4()
    ledger = LedgerService(tenant_id=tenant_id, account_id=account_id)

    ledger.record_cash_in(
        amount=Decimal("10000.00"),
        trade_date=date(2026, 8, 23),
        idempotency_key="DEP_01",
    )
    ledger.record_buy(
        instrument_id=fund_id,
        quantity=Decimal("1000.0000"),
        unit_price=Decimal("5.00"),
        fee=Decimal("0.00"),
        trade_date=date(2026, 8, 23),
        idempotency_key="BUY_FUND_01",
    )

    # Cash dividend: 0.20 per share on 1000 shares = 200.00 cash
    ledger.record_fund_dividend_cash(
        instrument_id=fund_id,
        cash_per_share=Decimal("0.20"),
        trade_date=date(2026, 8, 24),
        idempotency_key="DIV_CASH_01",
    )

    proj = ledger.rebuild_projection()
    # Cash: 10,000 - 5,000 + 200 = 5,200.00
    assert proj.cash_balance == Decimal("5200.00")
    pos = proj.positions[fund_id]
    assert pos.quantity == Decimal("1000.0000")
    assert pos.avg_cost == Decimal("5.0000")


def test_fund_dividend_reinvest_dilution() -> None:
    """Tests dividend reinvestment: increases shares, strictly dilutes unit cost."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    fund_id = uuid.uuid4()
    ledger = LedgerService(tenant_id=tenant_id, account_id=account_id)

    ledger.record_cash_in(
        amount=Decimal("10000.00"),
        trade_date=date(2026, 8, 23),
        idempotency_key="DEP_01",
    )
    # Buy 1000 shares @ 6.00 = 6000.00 total cost basis
    ledger.record_buy(
        instrument_id=fund_id,
        quantity=Decimal("1000.0000"),
        unit_price=Decimal("6.00"),
        fee=Decimal("0.00"),
        trade_date=date(2026, 8, 23),
        idempotency_key="BUY_FUND_01",
    )

    # Reinvestment: 0.20 / share = 200.00 total dividend.
    # Ex-dividend NAV = 5.00 -> New shares = 200 / 5 = 40.0000 shares.
    # Total shares = 1040.0000.
    # Diluted unit cost = 6000.00 / 1040.0000 = 5.7692.
    ledger.record_fund_dividend_reinvest(
        instrument_id=fund_id,
        cash_per_share=Decimal("0.20"),
        reinvest_nav=Decimal("5.00"),
        trade_date=date(2026, 8, 24),
        idempotency_key="DIV_REINVEST_01",
    )

    proj = ledger.rebuild_projection()
    # Cash unchanged
    assert proj.cash_balance == Decimal("4000.00")
    pos = proj.positions[fund_id]
    assert pos.quantity == Decimal("1040.0000")
    assert pos.avg_cost == Decimal("5.7692")


def test_idempotency_key_duplicate_prevention() -> None:
    """Tests that duplicate idempotency keys are strictly rejected."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    ledger = LedgerService(tenant_id=tenant_id, account_id=account_id)

    ledger.record_cash_in(
        amount=Decimal("1000.00"),
        trade_date=date(2026, 8, 23),
        idempotency_key="KEY_001",
    )

    with pytest.raises(ValueError, match="重复提交"):
        ledger.record_cash_in(
            amount=Decimal("1000.00"),
            trade_date=date(2026, 8, 23),
            idempotency_key="KEY_001",
        )


def test_insufficient_cash_rejection() -> None:
    """Tests that buying beyond available cash balance is rejected."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    ledger = LedgerService(tenant_id=tenant_id, account_id=account_id)

    ledger.record_cash_in(
        amount=Decimal("1000.00"),
        trade_date=date(2026, 8, 23),
        idempotency_key="DEP_01",
    )

    with pytest.raises(ValueError, match="可用现金不足"):
        ledger.record_buy(
            instrument_id=inst_id,
            quantity=Decimal("100.0000"),
            unit_price=Decimal("50.00"),  # Requires 5000 > 1000
            fee=Decimal("0.00"),
            trade_date=date(2026, 8, 23),
            idempotency_key="BUY_FAIL_01",
        )


def test_buy_rejects_negative_fee() -> None:
    """A negative fee must not reduce the cash sufficiency requirement."""
    ledger = LedgerService(tenant_id=uuid.uuid4(), account_id=uuid.uuid4())
    ledger.record_cash_in(Decimal("1000.00"), date(2026, 8, 24), "DEP_NEG_FEE")

    with pytest.raises(ValueError, match="手续费"):
        ledger.record_buy(
            instrument_id=uuid.uuid4(),
            quantity=Decimal("10.0000"),
            unit_price=Decimal("100.00"),
            fee=Decimal("-10.00"),
            trade_date=date(2026, 8, 24),
            idempotency_key="BUY_NEG_FEE",
        )


def test_sell_reduces_position_and_preserves_remaining_unit_cost() -> None:
    """A partial sale realizes cash without rewriting the remaining unit cost."""
    ledger = LedgerService(tenant_id=uuid.uuid4(), account_id=uuid.uuid4())
    instrument_id = uuid.uuid4()
    ledger.record_cash_in(Decimal("2000.00"), date(2026, 8, 24), "DEP_SELL")
    ledger.record_buy(
        instrument_id,
        Decimal("10.0000"),
        Decimal("100.00"),
        Decimal("0.00"),
        date(2026, 8, 24),
        "BUY_FOR_SELL",
    )

    ledger.record_sell(
        instrument_id,
        Decimal("4.0000"),
        Decimal("120.00"),
        Decimal("2.00"),
        date(2026, 8, 25),
        "SELL_PARTIAL",
    )

    projection = ledger.rebuild_projection()
    assert projection.cash_balance == Decimal("1478.00")
    assert projection.positions[instrument_id].quantity == Decimal("6.0000")
    assert projection.positions[instrument_id].total_cost_basis == Decimal("600.00")
    assert projection.positions[instrument_id].avg_cost == Decimal("100.0000")


def test_sell_rejects_quantity_above_holding() -> None:
    """The portfolio cannot sell more units than the immutable event stream owns."""
    ledger = LedgerService(tenant_id=uuid.uuid4(), account_id=uuid.uuid4())
    instrument_id = uuid.uuid4()
    ledger.record_cash_in(Decimal("1000.00"), date(2026, 8, 24), "DEP_OVERSELL")
    ledger.record_buy(
        instrument_id,
        Decimal("5.0000"),
        Decimal("10.00"),
        Decimal("0.00"),
        date(2026, 8, 24),
        "BUY_OVERSELL",
    )

    with pytest.raises(ValueError, match="持仓不足"):
        ledger.record_sell(
            instrument_id,
            Decimal("6.0000"),
            Decimal("10.00"),
            Decimal("0.00"),
            date(2026, 8, 25),
            "SELL_TOO_MUCH",
        )


def test_recorded_event_values_are_immutable_and_utc() -> None:
    """Callers must not mutate an already recorded economic event in memory."""
    ledger = LedgerService(tenant_id=uuid.uuid4(), account_id=uuid.uuid4())
    event = ledger.record_cash_in(Decimal("100.00"), date(2026, 8, 24), "DEP_FROZEN")

    assert event.created_at.tzinfo is not None
    with pytest.raises(FrozenInstanceError):
        event.description = "rewritten"


def test_cash_out_fee_and_tax_reduce_available_cash() -> None:
    """Standalone cash movements must participate in a rebuilt projection."""
    ledger = LedgerService(tenant_id=uuid.uuid4(), account_id=uuid.uuid4())
    ledger.record_cash_in(Decimal("1000.00"), date(2026, 8, 24), "DEP_CHARGES")

    ledger.record_cash_out(Decimal("200.00"), date(2026, 8, 25), "WITHDRAW_01")
    ledger.record_fee(Decimal("5.00"), date(2026, 8, 25), "FEE_01")
    ledger.record_tax(Decimal("2.00"), date(2026, 8, 25), "TAX_01")

    assert ledger.rebuild_projection().cash_balance == Decimal("793.00")


def test_reversal_of_latest_event_restores_prior_projection() -> None:
    """A reversal records lineage and restores the pre-event projection."""
    ledger = LedgerService(tenant_id=uuid.uuid4(), account_id=uuid.uuid4())
    instrument_id = uuid.uuid4()
    ledger.record_cash_in(Decimal("1000.00"), date(2026, 8, 24), "DEP_REVERSAL")
    buy = ledger.record_buy(
        instrument_id,
        Decimal("2.0000"),
        Decimal("100.00"),
        Decimal("1.00"),
        date(2026, 8, 25),
        "BUY_TO_REVERSE",
    )

    reversal = ledger.record_reversal(buy.id, date(2026, 8, 25), "REVERSE_BUY")
    projection = ledger.rebuild_projection()

    assert reversal.event_type is EventType.REVERSAL
    assert reversal.reverses_event_id == buy.id
    assert projection.cash_balance == Decimal("1000.00")
    assert instrument_id not in projection.positions

    with pytest.raises(ValueError, match="最新经济事件"):
        ledger.record_reversal(buy.id, date(2026, 8, 25), "REVERSE_BUY_AGAIN")


def test_reversal_rejects_non_latest_economic_event() -> None:
    """The prototype avoids invalid downstream history by reversing only the latest event."""
    ledger = LedgerService(tenant_id=uuid.uuid4(), account_id=uuid.uuid4())
    first = ledger.record_cash_in(Decimal("1000.00"), date(2026, 8, 24), "DEP_OLD")
    ledger.record_cash_out(Decimal("100.00"), date(2026, 8, 25), "WITHDRAW_NEW")

    with pytest.raises(ValueError, match="最新经济事件"):
        ledger.record_reversal(first.id, date(2026, 8, 25), "REVERSE_OLD")
