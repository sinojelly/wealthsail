from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.fred_macro import parse_fred_macro
from backend.macro.market_repository import normalize_series, series_context

NOW = datetime(2026, 11, 13, 12, tzinfo=ZoneInfo("America/New_York"))


@pytest.mark.parametrize(
    "feed,code",
    (("CPIAUCSL", "US_CPI"), ("CPILFESL", "US_CORE_CPI"), ("PPIACO", "US_PPI")),
)
def test_monthly_changes_use_exact_comparison_month_and_known_retrieval_clock(
    feed, code
):
    raw = f"DATE,{feed}\n2025-10-01,100\n2026-08-01,102\n2026-09-01,.\n2026-10-01,104\n"
    rows = parse_fred_macro(raw, feed, NOW)
    latest = [row for row in rows if row["referenceDate"] == "2026-10-01"]
    assert [row["code"] for row in latest] == [code + "_YOY"]
    row = normalize_series(latest[0], NOW)
    assert row["value"] == Decimal("4") and row["published_at"] is None
    assert row["available_at"] == NOW


def test_daily_prices_retain_five_observation_change_and_skip_missing_holiday():
    raw = "observation_date,DGS2\n2026-11-03,4.12\n2026-11-04,4.14\n2026-11-05,4.18\n2026-11-06,4.20\n2026-11-09,4.26\n2026-11-11,.\n2026-11-12,4.43\n"
    row = next(
        row
        for row in series_context(
            [
                normalize_series(item, NOW)
                for item in parse_fred_macro(raw, "DGS2", NOW)
            ],
            NOW,
        )
        if row["code"] == "US_T2_NOMINAL"
    )
    assert row["changes"]["change5Samples"] == Decimal("31")
    assert row["changes"]["changeUnit"] == "bp"
    assert len(row["history"]) == 6


@pytest.mark.parametrize(
    "payload", ("2026-11-14,1", "2026-11-13,NaN", "2026-11-12,2\n2026-11-12,3")
)
def test_fred_rejects_invalid_or_conflicting_observations(payload):
    with pytest.raises(ValueError):
        parse_fred_macro("DATE,DGS2\n" + payload, "DGS2", NOW)
