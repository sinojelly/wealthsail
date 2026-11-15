from datetime import date, timedelta

from backend.indicators.actionable_zones import buy_zone_backtest
from backend.macro.live_industry_positions import enrich_live_industry_positions


def test_buy_zone_counts_entries_and_complete_forward_windows():
    closes = ["1.20", "1.00", "1.00", "1.06", *(["1.20"] * 60), "1.00"]
    result = buy_zone_backtest(closes, "0.95", "1.05")
    assert result["touchCount"] == 2
    assert result["evaluatedCount"] == 1
    assert result["winRatePct"] == 100
    assert result["averageMaxReboundPct"] == 20


def test_buy_zone_ignores_left_censored_entry_and_requires_more_than_five_percent():
    closes = ["1.00", "1.01", "1.06", "1.00", *(["1.05"] * 60)]
    result = buy_zone_backtest(closes, "0.95", "1.02")
    assert result["touchCount"] == result["evaluatedCount"] == 1
    assert result["winRatePct"] == 0
    assert result["averageMaxReboundPct"] == 5


def test_current_positions_replace_frozen_price_claims_and_add_backtest():
    start = date(2026, 7, 3)
    closes = ["1.20", "1.02", *(["1.20"] * 63), "1.01"]
    points = [
        {"date": (start + timedelta(days=i)).isoformat(), "value": value}
        for i, value in enumerate(closes)
    ]
    plan = {
        "subjectKind": "nav",
        "fundCode": "024194",
        "label": "商业卫星",
        "priceBasis": "UNIT_NAV",
        "asOf": points[-1]["date"],
        "currentPrice": "1.01",
        "tracks": [
            {
                "track": "LEFT_VALUE",
                "entryZoneMin": "0.95",
                "entryZoneMax": "1.05",
                "stopLossPrice": "0.90",
                "takeProfitT1": "1.20",
            }
        ],
        "series": {"state": "READY", "asOf": points[-1]["date"], "points": points},
    }
    industry = {
        "label": "商业卫星",
        "stage": "继续观察",
        "driver": "024194仍未入区。卫星发射排期仍需跟踪。",
        "fundCode": "024194",
        "origin": "原报告基金价格位置",
    }
    report = {
        "valueDirections": [{"title": "商业卫星", "plans": [plan]}],
        "cycleInsights": {"industries": [industry]},
    }
    enrich_live_industry_positions(report)
    assert industry["stage"] == industry["fundPositions"][0]["status"] == "已入买区"
    assert industry["driver"] == "卫星发射排期仍需跟踪。"
    assert plan["tracks"][0]["buyZoneBacktest"]["evaluatedCount"] == 1
    # Same dates and sample count, revised values. Cache identity follows NAV data.
    for point in points[2:-1]:
        point["value"] = "1.06"
    enrich_live_industry_positions(report)
    assert plan["tracks"][0]["buyZoneBacktest"]["winRatePct"] == 0
