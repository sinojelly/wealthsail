"""Confirmed disclosed NAV is useful research without a fabricated calendar."""

from copy import deepcopy
from datetime import timedelta

import pytest

from backend.investment import discovery, theme_graph
from backend.integrations.nav_history_proof import nav_points_digest
from test_theme_graph import NOW, nav_history


def disclosed_history():
    history = nav_history(fundCode="162411", overseasCalendarRequired=True)
    history["sourceUri"] = "https://fund.eastmoney.com/162411.html"
    history["providerHistoryProof"] = {
        "methodVersion": "eastmoney-nav-history-v1",
        "fundCode": "162411",
        "sourceUri": "https://api.fund.eastmoney.com/f10/lsjz",
        "queryStart": "2026-09-01",
        "queryEnd": "2026-10-18",
        "totalCount": len(history["points"]),
        "receivedRowCount": len(history["points"]),
        "uniquePointCount": len(history["points"]),
        "pointCount": len(history["points"]),
        "pageCount": 1,
        "requiredPages": 1,
        "complete": True,
        "firstDate": history["points"][0]["date"],
        "lastDate": history["asOf"],
        "pointsDigest": nav_points_digest(history["points"]),
        "retrievedAt": NOW.isoformat(),
    }
    return history


def test_qdii_complete_disclosed_series_has_full_research_prices_without_claiming_current_estimate():
    history = disclosed_history()
    before = deepcopy(history)
    result = theme_graph.nav_plan(history, now=NOW)
    assert result["historyQuality"] == "READY"
    assert result["priceTimeBasis"] == "LATEST_DISCLOSED_NAV"
    assert result["orderPriceAvailable"] is False
    assert result["asOf"] == history["asOf"]
    assert result["historySampleCount"] == len(history["points"])
    assert result["historyTargetSamples"] == 500
    assert result["historyCoverage"] == "SHORT_HISTORY"
    for track in result["tracks"]:
        assert all(
            track[field] is not None
            for field in (
                "entryZoneMin",
                "entryZoneMax",
                "stopLossPrice",
                "takeProfitT1",
                "takeProfitT2",
            )
        )
        assert "境外" in "".join(track["reasons"])
    assert history == before


@pytest.mark.parametrize(
    "mutation",
    ["digest", "code", "truncated", "future", "source", "old", "missing", "cutoff"],
)
def test_disclosure_proof_does_not_admit_conflicting_future_stale_or_wrong_identity(
    mutation,
):
    history = disclosed_history()
    proof = history["providerHistoryProof"]
    if mutation == "digest":
        history["points"][0]["close"] = "99.99"
    elif mutation == "code":
        proof["fundCode"] = "000051"
    elif mutation == "truncated":
        proof["pageCount"] = 0
    elif mutation == "future":
        proof["retrievedAt"] = (NOW + timedelta(days=1)).isoformat()
    elif mutation == "source":
        proof["sourceUri"] = "https://example.org/unproven"
    elif mutation == "old":
        history["asOf"] = "2026-09-25"
        history["points"] = [
            p for p in history["points"] if p["date"] <= history["asOf"]
        ]
        proof.update(
            lastDate=history["asOf"],
            pointCount=len(history["points"]),
            pointsDigest=nav_points_digest(history["points"]),
        )
    elif mutation == "cutoff":
        proof["queryEnd"] = "2026-10-17"
    else:
        history.pop("providerHistoryProof")
    result = theme_graph.nav_plan(history, now=NOW)
    assert result["historyQuality"] != "READY"
    assert all(t["entryZoneMin"] is None for t in result["tracks"])
    assert all(t.get("technicalTriggered") is False for t in result["tracks"])
    assert all(t.get("entryReady") is False for t in result["tracks"])
    assert all(t.get("inBuyZone") is False for t in result["tracks"])


def test_quantitative_scan_keeps_technical_trigger_distinct_from_inside_entry_zone():
    history = disclosed_history()
    plan = theme_graph.nav_plan(history, now=NOW)
    asset = {
        "subjectKind": "nav",
        "subjectId": "162411",
        "fundCode": "162411",
        "market": "FUND_CN",
        "label": "真实油气研究",
    }
    public = discovery.discovery_packet([asset], [plan], {}, NOW)
    scan = public["quantitativeScan"]
    assert scan["methodVersion"] == "fund-quant-scan-v2"
    assert scan["minimumHistorySamples"] == 120
    row = scan["items"][0]
    assert (
        row["fundCode"] == "162411" and row["priceTimeBasis"] == "LATEST_DISCLOSED_NAV"
    )
    for signal in row["signals"]:
        track = next(t for t in plan["tracks"] if t["track"] == signal["track"])
        assert signal["triggerState"] == track["state"]
        assert signal["inEntryZone"] == (
            float(track["entryZoneMin"])
            <= float(plan["currentPrice"])
            <= float(track["entryZoneMax"])
        )


def test_quantitative_scan_excludes_stale_or_missing_prices():
    from test_phase2_research import anchor

    plan = anchor()
    plan.update(historyQuality="STALE", fundCode="008281")
    asset = {
        "subjectKind": "nav",
        "subjectId": "008281",
        "fundCode": "008281",
        "market": "FUND_CN",
        "label": "芯片",
    }
    assert (
        discovery.discovery_packet([asset], [plan], {}, NOW)["quantitativeScan"][
            "items"
        ]
        == []
    )
