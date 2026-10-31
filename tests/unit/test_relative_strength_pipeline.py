"""Only a visible, sourced CSI300 series can become a fund benchmark."""

from copy import deepcopy
from datetime import timedelta

import pytest

from test_daily_research import NOW


def benchmark_analysis():
    points = [
        {
            "date": (NOW.date() - timedelta(days=90 - offset)).isoformat(),
            "close": str(4000 + offset),
        }
        for offset in range(90)
    ]
    return {
        "dataQuality": "READY",
        "asOf": points[-1]["date"],
        "retrievedAt": NOW.isoformat(),
        "source": "https://www.csindex.com.cn/000300",
        "points": points,
    }


def test_market_benchmark_preserves_actual_closes_and_provenance():
    from backend.jobs.opportunity_market import relative_strength_benchmark

    analysis = benchmark_analysis()
    before = deepcopy(analysis)
    benchmark = relative_strength_benchmark(analysis, NOW)
    assert benchmark["state"] == "READY"
    assert benchmark["indexId"] == "hs300"
    assert benchmark["points"] == analysis["points"]
    assert benchmark["sourceUri"] == analysis["source"]
    assert analysis == before


@pytest.mark.parametrize(
    "problem",
    [
        "future-retrieval",
        "future-close",
        "duplicate-date",
        "invalid-close",
        "stale",
        "no-source",
        "unverified",
    ],
)
def test_bad_benchmark_cannot_be_used_to_claim_excess_returns(problem):
    from backend.jobs.opportunity_market import relative_strength_benchmark

    analysis = benchmark_analysis()
    if problem == "future-retrieval":
        analysis["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif problem == "future-close":
        analysis["points"][-1]["date"] = (NOW.date() + timedelta(days=1)).isoformat()
    elif problem == "duplicate-date":
        analysis["points"][-1]["date"] = analysis["points"][-2]["date"]
    elif problem == "invalid-close":
        analysis["points"][-1]["close"] = "NaN"
    elif problem == "stale":
        analysis["asOf"] = (NOW.date() - timedelta(days=11)).isoformat()
    elif problem == "no-source":
        analysis.pop("source")
    elif problem == "unverified":
        analysis["dataQuality"] = "UNVERIFIED"
    result = relative_strength_benchmark(analysis, NOW)
    assert result["state"] == "UNAVAILABLE"
    assert result["points"] == []


def test_discovery_rank_and_public_scan_preserve_benchmark_and_discounted_coordinates():
    from backend.indicators.actionable_zones import compute_actionable_price_plan
    from backend.investment.discovery import discovery_packet
    from backend.investment.opportunity_scoring import rank_report
    from backend.investment.quantitative_scan import public_quantitative_scan

    closes = [1 + index / 500 for index in range(140)] + [1.12] * 80
    points = [
        {
            "date": (NOW.date() - timedelta(days=len(closes) - index)).isoformat(),
            "close": value,
        }
        for index, value in enumerate(closes)
    ]
    benchmark = [{**point, "close": 4000} for point in points]
    anchor = compute_actionable_price_plan(
        points,
        subject_kind="nav",
        subject_id="000001",
        price_basis="UNIT_NAV",
        unit="CNY",
        market="FUND_CN",
        label="测试机器人联接",
        benchmark_points=benchmark,
        source_uri="https://fund.eastmoney.com/000001.html",
    )
    anchor.update(
        fundCode="000001",
        historyQuality="READY",
        historySampleCount=220,
        retrievedAt=NOW.isoformat(),
        availableAt=NOW.isoformat(),
    )
    universe = [
        {
            "subjectKind": "nav",
            "subjectId": "000001",
            "fundCode": "000001",
            "market": "FUND_CN",
            "label": anchor["label"],
            "discoverySector": "ROBOT",
        }
    ]
    packet = discovery_packet(universe, [anchor], {}, NOW)
    scan = packet["quantitativeScan"]
    assert scan["items"][0]["relativeStrength"]["state"] == "READY"
    assert isinstance(scan["items"][0]["industryAdjustmentBase"], dict)
    report = rank_report(
        {
            "asOf": NOW.isoformat(),
            "quantitativeScan": scan,
            "industryCycleReviews": [
                {
                    "industry": "ROBOT",
                    "stage": "UNKNOWN",
                    "riskFlags": ["FINANCING_COOLING"],
                }
            ],
        },
        {"asOf": NOW.isoformat()},
    )
    guarded = public_quantitative_scan(report["quantitativeScan"], NOW)["items"][0]
    left = guarded["tracks"][0]
    assert (
        guarded["relativeStrength"]["excessReturn60d"]
        == anchor["relativeStrength"]["excessReturn60d"]
    )
    assert guarded["industryRiskAdjustment"]["state"] == "APPLIED"
    assert float(left["entryZoneMax"]) < float(anchor["tracks"][0]["entryZoneMax"])
    signal = next(row for row in guarded["signals"] if row["track"] == "LEFT_VALUE")
    assert signal["inEntryZone"] == (
        float(left["entryZoneMin"])
        <= float(guarded["currentPrice"])
        <= float(left["entryZoneMax"])
    )
