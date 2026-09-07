"""Deterministic projection tests over confirmed immutable ledger events."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.market.repository import (
    derive_account_nav,
    rebuild_confirmed_holdings,
    refresh_account_nav,
    refresh_positions,
)


def test_rebuild_uses_confirmed_event_legs_and_preserves_account_dimension() -> None:
    events = [
        {
            "account_id": "account-a",
            "event_type": "BUY",
            "legs": [
                {"leg_type": "INSTRUMENT", "instrument_id": "fund-1", "quantity": "10", "unit_price": "2"},
                {"leg_type": "CASH", "amount": "-20"},
                {"leg_type": "FEE", "amount": "-1"},
            ],
        },
        {
            "account_id": "account-b",
            "event_type": "BUY",
            "legs": [
                {"leg_type": "INSTRUMENT", "instrument_id": "fund-1", "quantity": "5", "unit_price": "3"},
                {"leg_type": "CASH", "amount": "-15"},
            ],
        },
    ]

    holdings = rebuild_confirmed_holdings(events)

    assert holdings[("account-a", "fund-1")].quantity == Decimal("10")
    assert holdings[("account-a", "fund-1")].avg_cost == Decimal("2.1000")
    assert holdings[("account-b", "fund-1")].quantity == Decimal("5")
    assert holdings[("account-b", "fund-1")].avg_cost == Decimal("3.0000")


class _ProjectionConnection:
    def __init__(self) -> None:
        self.fetch_sql = ""
        self.writes: list[tuple] = []
        self.deleted = False

    async def fetch(self, sql: str, *_args):
        self.fetch_sql = " ".join(sql.split())
        return [
            {
                "account_id": "account-a",
                "event_type": "BUY",
                "legs": [
                    {
                        "leg_type": "INSTRUMENT",
                        "instrument_id": "priced",
                        "quantity": "10",
                        "unit_price": "2",
                    },
                    {"leg_type": "CASH", "amount": "-20"},
                ],
            },
            {
                "account_id": "account-a",
                "event_type": "BUY",
                "legs": [
                    {
                        "leg_type": "INSTRUMENT",
                        "instrument_id": "unpriced",
                        "quantity": "5",
                        "unit_price": "4",
                    },
                    {"leg_type": "CASH", "amount": "-20"},
                ],
            },
        ]

    async def execute(self, _sql: str, *args):
        if _sql.lstrip().startswith("DELETE"):
            self.deleted = True
        else:
            self.writes.append(args)


@pytest.mark.asyncio
async def test_refresh_keeps_unpriced_holding_as_unavailable_row() -> None:
    conn = _ProjectionConnection()

    count = await refresh_positions(
        conn,
        tenant_id="tenant-a",
        quotes={"priced": {"price": Decimal("3"), "quality": "FRESH"}},
        as_of_date=date(2026, 8, 26),
    )

    assert count == 2
    assert conn.deleted is True
    assert "ev.status = 'CONFIRMED'" in conn.fetch_sql
    assert "ev.event_type <> 'REVERSAL'" in conn.fetch_sql
    assert "reversal.reverses_event_id = ev.id" in conn.fetch_sql
    assert "reversal.trade_date <= $2" in conn.fetch_sql
    by_instrument = {str(args[2]): args for args in conn.writes}
    assert by_instrument["priced"][6:10] == (
        Decimal("3"),
        Decimal("30.00"),
        Decimal("10.00"),
        "FRESH",
    )
    assert by_instrument["unpriced"][6:10] == (
        None,
        None,
        None,
        "UNAVAILABLE",
    )


def test_replacement_adjustment_uses_authoritative_snapshot_quantity_and_cost() -> None:
    holdings = rebuild_confirmed_holdings(
        [{
            "account_id": "account-a",
            "event_type": "ADJUSTMENT",
            "legs": [{
                "leg_type": "INSTRUMENT",
                "instrument_id": "fund-1",
                "quantity": "125.5",
                "unit_price": "1.2345",
            }],
        }]
    )

    assert holdings[("account-a", "fund-1")].quantity == Decimal("125.5")
    assert holdings[("account-a", "fund-1")].avg_cost == Decimal("1.2345")


def test_account_nav_abstains_when_any_open_position_is_unpriced() -> None:
    nav = derive_account_nav(
        cash_balance=Decimal("100"),
        principal=Decimal("120"),
        priced_positions_value=Decimal("50"),
        total_position_count=2,
        priced_position_count=1,
    )

    assert nav.total_nav is None
    assert nav.known_value == Decimal("150.00")
    assert nav.coverage_pct == Decimal("50.00")
    assert nav.unpriced_position_count == 1
    assert nav.quality == "UNAVAILABLE"


def test_cash_only_account_has_complete_fresh_nav() -> None:
    nav = derive_account_nav(
        cash_balance=Decimal("100"),
        principal=Decimal("100"),
        priced_positions_value=Decimal("0"),
        total_position_count=0,
        priced_position_count=0,
    )

    assert nav.total_nav == Decimal("100.00")
    assert nav.coverage_pct == Decimal("100.00")
    assert nav.quality == "FRESH"


def test_account_nav_preserves_the_worst_position_valuation_quality() -> None:
    nav = derive_account_nav(
        cash_balance=Decimal("100"),
        principal=Decimal("100"),
        priced_positions_value=Decimal("50"),
        total_position_count=2,
        priced_position_count=2,
        position_quality_statuses=("FRESH", "ESTIMATED"),
    )

    assert nav.total_nav == Decimal("150.00")
    assert nav.quality == "ESTIMATED"


class _NavConnection:
    def __init__(
        self,
        *,
        cash_balance: Decimal = Decimal("100"),
        positions_value: Decimal = Decimal("0"),
        previous_total: Decimal | None = None,
        daily_external_flow: Decimal = Decimal("0"),
    ) -> None:
        self.cash_sql = ""
        self.previous_nav_sql = ""
        self.nav_write_args: tuple[object, ...] | None = None
        self.cash_balance = cash_balance
        self.positions_value = positions_value
        self.previous_total = previous_total
        self.daily_external_flow = daily_external_flow

    async def fetch(self, _sql: str, *_args):
        return [{"account_id": "account-a"}]

    async def fetchrow(self, sql: str, *_args):
        normalized = " ".join(sql.split())
        if "ledger.ledger_legs" in normalized:
            self.cash_sql = normalized
            return {
                "cash_balance": self.cash_balance,
                "principal": Decimal("100"),
                "daily_external_flow": self.daily_external_flow,
            }
        if "ledger.position_snapshots" in normalized:
            return {
                "positions_value": self.positions_value,
                "total": 0,
                "priced_count": 0,
            }
        if "SELECT total_nav" in normalized and self.previous_total is not None:
            self.previous_nav_sql = normalized
            return {"total_nav": self.previous_total}
        if "SELECT total_nav" in normalized:
            self.previous_nav_sql = normalized
        return None

    async def execute(self, sql: str, *args):
        if "ledger.account_nav_snapshots" in sql:
            self.nav_write_args = args
        return "INSERT 0 1"


@pytest.mark.asyncio
async def test_account_nav_excludes_reversal_legs_from_cash() -> None:
    conn = _NavConnection()

    assert await refresh_account_nav(
        conn,
        tenant_id="tenant-a",
        as_of_date=date(2026, 8, 28),
    ) == 1
    assert "ev.event_type <> 'REVERSAL'" in conn.cash_sql
    assert "reversal.reverses_event_id = ev.id" in conn.cash_sql
    assert "reversal.trade_date <= $3" in conn.cash_sql
    assert "ev.trade_date <= $3" in conn.cash_sql


@pytest.mark.asyncio
async def test_account_nav_does_not_fabricate_cumulative_pnl_from_opening_import() -> None:
    """An opening-balance import is not a lifetime performance baseline."""
    conn = _NavConnection()

    assert await refresh_account_nav(
        conn,
        tenant_id="tenant-a",
        as_of_date=date(2026, 8, 28),
    ) == 1

    assert conn.nav_write_args is not None
    # INSERT argument $8 is cumulative_pnl. It must remain unknown until a
    # cash-flow-aware performance ledger is available.
    assert conn.nav_write_args[7] is None


@pytest.mark.asyncio
async def test_first_account_nav_snapshot_abstains_from_daily_pnl() -> None:
    """Without a previous close, a zero daily return would be fabricated."""
    conn = _NavConnection()

    await refresh_account_nav(
        conn,
        tenant_id="tenant-a",
        as_of_date=date(2026, 8, 28),
    )

    assert conn.nav_write_args is not None
    assert conn.nav_write_args[6] is None


@pytest.mark.asyncio
async def test_account_nav_daily_pnl_excludes_same_day_external_cash_flow() -> None:
    """Deposits and withdrawals are not investment performance."""
    conn = _NavConnection(
        cash_balance=Decimal("200"),
        positions_value=Decimal("950"),
        previous_total=Decimal("1000"),
        daily_external_flow=Decimal("100"),
    )

    await refresh_account_nav(
        conn,
        tenant_id="tenant-a",
        as_of_date=date(2026, 8, 28),
    )

    assert conn.nav_write_args is not None
    assert conn.nav_write_args[6] == Decimal("50.00")
    assert "ev.trade_date = $3" in conn.cash_sql
    assert "reference.trading_calendars" in conn.previous_nav_sql
    assert "calendar.calendar_date < $3" in conn.previous_nav_sql
