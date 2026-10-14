"""Cross-layer audit regressions: plotted facts must agree with statistics."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.investment import service
from test_investment_macro_context import NOW, _context_sources, _row
from test_macro_research import enrich, item, history


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict_month", [8, 9])
async def test_history_official_conflict_breaks_chart_and_current_change(
    monkeypatch, conflict_month
):
    _context_sources(
        monkeypatch,
        [_row(date(2026, month, 1), Decimal(month) / 10) for month in (7, 8, 9)],
    )
    monkeypatch.setattr(
        service,
        "read_macro_evidence",
        AsyncMock(
            return_value={
                "releases": [
                    {
                        "code": "CPI_YOY",
                        "reference_period": date(2026, conflict_month, 1),
                        "value": Decimal(9),
                        "unit": "%",
                        "source_uri": "https://www.stats.gov.cn/test",
                        "publisher": "国家统计局",
                        "retrieved_at": NOW,
                        "published_at": None,
                        "revision": 1,
                    }
                ],
                "schedules": [],
                "events": [],
            }
        ),
    )
    result = (await service.market_context(object()))["items"][0]
    assert result["history"][conflict_month - 7]["state"] == "CONFLICT"
    assert result["trendState"] == "GAP" and result["periodChange"] is None
    assert f"2026-{conflict_month:02d}" in result["statistics"]["missingPeriods"]


def test_unknown_event_publication_does_not_outrank_confirmed_recent_fact():
    event = {
        "event_key": "known",
        "title": "公告",
        "category": "DATA_RELEASE",
        "source_type": "OFFICIAL_FACT",
        "source_uri": "https://www.stats.gov.cn/test",
        "publisher": "国家统计局",
        "published_at": NOW - timedelta(hours=2),
        "retrieved_at": NOW - timedelta(hours=1),
        "direction_ids": [],
    }
    rows = [
        event,
        {**event, "event_key": "unknown", "published_at": None, "retrieved_at": NOW},
    ]
    assert [e["id"] for e in service._macro_events(rows, NOW)] == ["known", "unknown"]


def test_unknown_units_cannot_create_a_level_or_comparable_change():
    result = enrich(item(unit=None), history(unit=None))
    assert result["value"] is None
    assert result["interpretation"]["level"] == "UNAVAILABLE"
    assert result["statistics"]["previousValue"] is None
