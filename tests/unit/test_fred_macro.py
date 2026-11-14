from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.fred_macro import fred_uri, parse_fred_macro
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


def test_vix_close_keeps_two_years_of_daily_points_and_frozen_stress_peaks():
    from backend.macro.cycle_insights import macro_snapshot
    from backend.macro.daily_research import make_research_packet

    raw = "DATE,VIXCLS\n2024-11-12,11\n2024-11-13,15\n2025-07-07,40\n2025-07-08,90\n2025-07-09,25\n2025-07-10,21\n2025-07-11,20\n2026-11-12,.\n2026-11-13,18.70\n"
    parsed = parse_fred_macro(raw, "VIXCLS", NOW)
    assert "cosd=2024-11-13" in fred_uri("VIXCLS", NOW)
    assert [r["referenceDate"] for r in parsed][0] == "2024-11-13"
    assert [r["referenceDate"] for r in parsed][-1] == "2026-11-13"
    assert all(
        r["sourceName"] == "Cboe · FRED" and r["publishedAt"] is None for r in parsed
    )
    records = [normalize_series(row, NOW) for row in parsed]
    series = series_context(records, NOW)
    live = next(row for row in series if row["code"] == "VIX")
    assert len(live["history"]) == 7
    assert live["value"] == Decimal("18.70") and live["changes"]["changeUnit"] == "点"
    packet = make_research_packet({}, series, [], [], now=NOW)
    snapshot = macro_snapshot(packet, NOW)
    archived = next(row for row in snapshot["series"] if row["code"] == "VIX")
    assert [(row["date"], row["value"]) for row in archived["history"]] == [
        ("2024-11-13", 15),
        ("2025-07-08", 90),
        ("2025-07-11", 20),
        ("2026-11-13", 18.7),
    ]
    assert macro_snapshot({"series": [archived]}, NOW - timedelta(seconds=1)) is None
