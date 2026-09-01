"""Watchlist context for audience-specific reports."""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock

import pytest

from backend.reports import repository, service


class _ReportConnection:
    async def fetchrow(self, _sql: str, *_args: object):
        return None

    async def fetch(self, _sql: str, *_args: object):
        return []


@pytest.mark.asyncio
async def test_report_context_reads_watchlist_for_explicit_audience(monkeypatch) -> None:
    """An audience-specific report must contain only that user's watchlist."""
    watched = [{"symbol": "600036", "name": "招商银行", "type": "STOCK"}]
    monkeypatch.setattr(
        repository, "get_user_watchlist_context", AsyncMock(return_value=watched)
    )

    context = await service._build_context(
        _ReportConnection(), "tenant-1", "PRE_MARKET", audience_user_id="user-1"
    )

    assert context["watchlist"] == watched
    repository.get_user_watchlist_context.assert_awaited_once_with(ANY, "user-1")


@pytest.mark.asyncio
async def test_legacy_report_context_has_empty_watchlist_without_audience(monkeypatch) -> None:
    """Legacy tenant-wide reports must not query a user's watchlist."""
    get_watchlist = AsyncMock()
    monkeypatch.setattr(repository, "get_user_watchlist_context", get_watchlist)

    context = await service._build_context(_ReportConnection(), "tenant-1", "POST_MARKET")

    assert context["watchlist"] == []
    get_watchlist.assert_not_awaited()


class _WatchlistConnection:
    def __init__(self) -> None:
        self.args: tuple[object, ...] | None = None

    async def fetch(self, _sql: str, *args: object):
        self.args = args
        return [
            {
                "symbol": "600036",
                "name": "招商银行",
                "type": "STOCK",
                "added_at": None,
                "instrument_id": "11111111-1111-1111-1111-111111111111",
                "price": None,
                "change_pct": None,
                "quality": None,
                "as_of": None,
            }
        ]


@pytest.mark.asyncio
async def test_watchlist_context_keeps_missing_market_fields_null() -> None:
    """A missing quote must stay missing rather than becoming a synthetic value."""
    conn = _WatchlistConnection()

    watchlist = await repository.get_user_watchlist_context(conn, "user-1")

    assert conn.args == ("user-1",)
    assert watchlist == [
        {
            "symbol": "600036",
            "name": "招商银行",
            "type": "STOCK",
            "addedAt": None,
            "instrumentId": "11111111-1111-1111-1111-111111111111",
            "price": None,
            "changePct": None,
            "quality": None,
            "asOf": None,
        }
    ]
