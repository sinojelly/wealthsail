"""Only a confirmed, unused installation may remove historical approximations."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import importlib

import pytest


class _Transaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.before = deepcopy(self.conn.holdings)
        self.conn.in_transaction = True
        return self.conn

    async def __aexit__(self, exc_type, *_args):
        if exc_type is not None:
            self.conn.holdings = self.before
        self.conn.in_transaction = False
        return False


class FakeConn:
    def __init__(self, *, counts=None, holdings=None, fail_delete=False):
        self.counts = {
            "profile_count": 0, "tenant_count": 0, "account_count": 0,
            "event_count": 0, "position_count": 0, "nav_count": 0,
            **(counts or {}),
        }
        self.holdings = deepcopy(holdings or [])
        self.fail_delete = fail_delete
        self.in_transaction = False
        self.locked_tables = set()
        self.delete_attempts = 0

    def transaction(self):
        return _Transaction(self)

    async def execute(self, sql, *_args):
        assert self.in_transaction
        if sql.lstrip().upper().startswith("LOCK TABLE"):
            tables = sql.split("TABLE", 1)[1].split("IN SHARE", 1)[0]
            self.locked_tables.update(table.strip() for table in tables.split(","))
            return "LOCK TABLE"
        raise AssertionError(f"unexpected statement: {sql}")

    async def fetchrow(self, _sql, *_args):
        self._assert_locked()
        return self.counts

    async def fetch(self, _sql, *_args):
        self._assert_locked()
        return deepcopy(self.holdings)

    async def fetchval(self, sql, *_args):
        self._assert_locked()
        assert "DELETE FROM market.fund_lookthrough" in sql
        self.delete_attempts += 1
        removed = len(self.holdings)
        self.holdings.clear()
        if self.fail_delete:
            raise RuntimeError("database delete failed")
        return removed

    def _assert_locked(self):
        assert self.in_transaction
        assert self.locked_tables == {
            "identity.profiles", "identity.tenants", "identity.accounts",
            "ledger.ledger_events", "ledger.position_snapshots",
            "ledger.account_nav_snapshots", "market.fund_lookthrough",
        }


def _approximate_holding(**overrides):
    return {
        "report_period": date(2026, 6, 30), "source_id": None,
        "source": "index-disclosure",
        "warning": "基于公开指数机制的估值近似，真季报披露后请用 scripts/ingest_fund_holdings.py 覆盖",
        **overrides,
    }


async def _clean(conn):
    module = importlib.import_module("scripts.fresh_install_data")
    return await module.clean_fresh_install_data(conn)


@pytest.mark.asyncio
async def test_unused_database_removes_all_legacy_approximations_and_returns_count():
    conn = FakeConn(holdings=[_approximate_holding(), _approximate_holding()])

    assert await _clean(conn) == {"removedApproximateHoldings": 2}
    assert conn.holdings == []


@pytest.mark.asyncio
async def test_already_clean_unused_database_is_repeatable():
    conn = FakeConn()

    assert await _clean(conn) == {"removedApproximateHoldings": 0}
    assert await _clean(conn) == {"removedApproximateHoldings": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("table_count", [
    "profile_count", "tenant_count", "account_count", "event_count",
    "position_count", "nav_count",
])
async def test_any_existing_identity_or_personal_business_data_prevents_cleanup(table_count):
    original = [_approximate_holding()]
    conn = FakeConn(counts={table_count: 1}, holdings=original)

    with pytest.raises(RuntimeError, match="empty|existing|unused"):
        await _clean(conn)

    assert conn.holdings == original
    assert conn.delete_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"report_period": date(2026, 9, 30)}, {"report_period": None},
    {"source_id": "e377d172-5b3d-4fbc-8681-250b7bd145e9"},
    {"source": "official-disclosure"}, {"source": None},
    {"warning": "reviewed official holdings"}, {"warning": None},
])
async def test_one_unrecognized_holding_aborts_all_cleanup(overrides):
    original = [_approximate_holding(), _approximate_holding(**overrides)]
    conn = FakeConn(holdings=original)

    with pytest.raises(RuntimeError, match="unexpected|unrecognized|source"):
        await _clean(conn)

    assert conn.holdings == original
    assert conn.delete_attempts == 0


@pytest.mark.asyncio
async def test_delete_failure_rolls_back_all_removals():
    original = [_approximate_holding()]
    conn = FakeConn(holdings=original, fail_delete=True)

    with pytest.raises(RuntimeError, match="database delete failed"):
        await _clean(conn)

    assert conn.holdings == original
