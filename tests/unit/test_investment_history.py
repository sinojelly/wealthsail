from datetime import date

import pytest


def test_normalize_history_rejects_conflicting_dates_and_bad_prices():
    from backend.market.research_history import normalize

    with pytest.raises(ValueError, match="duplicate"):
        normalize(
            [{"date": "2025-01-01", "close": 1}, {"date": "2025-01-01", "close": 2}]
        )
    with pytest.raises(ValueError):
        normalize([{"date": "2025-01-01", "close": float("nan")}])
    with pytest.raises(ValueError):
        normalize([{"date": "2025-01-01", "close": 1, "low": 2, "high": 3}])


def test_normalize_sorts_without_filling_missing_bars():
    from backend.market.research_history import normalize

    rows = normalize(
        [{"date": "2025-01-03", "close": 3}, {"date": "2025-01-01", "close": 1}]
    )
    assert len(rows) == 2
    assert rows[0]["date"] == "2025-01-01"
    assert rows[0]["high"] is None


def test_quality_uses_released_trading_day_not_calendar_today():
    from backend.market.research_history import quality

    assert (
        quality(
            latest=date(2026, 10, 9),
            expected=date(2026, 10, 9),
            verified=True,
            missing=False,
        )
        == "READY"
    )
    assert (
        quality(
            latest=date(2026, 10, 8),
            expected=date(2026, 10, 9),
            verified=True,
            missing=False,
        )
        == "STALE"
    )
    assert (
        quality(latest=date(2026, 10, 9), expected=None, verified=False, missing=False)
        == "UNVERIFIED"
    )
    assert (
        quality(
            latest=date(2026, 10, 9),
            expected=date(2026, 10, 9),
            verified=True,
            missing=True,
        )
        == "CONFLICT"
    )


def test_research_refresh_routes_are_registered():
    from backend.main import create_app

    paths = create_app().openapi()["paths"]
    assert "/api/v1/investment/refresh" in paths
    assert "/api/v1/investment/refresh/{run_id}" in paths
    assert "/api/v1/investment/indices/{index_id}/analysis" in paths
