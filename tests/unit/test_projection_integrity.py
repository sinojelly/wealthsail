"""Deterministic projection tests over confirmed immutable ledger events."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.market.repository import (
    derive_account_nav,
    rebuild_confirmed_holdings,
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
