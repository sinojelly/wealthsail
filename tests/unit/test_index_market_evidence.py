"""Official PE remains useful without claiming full valuation or earnings."""

from datetime import date, timedelta
import pytest

from backend.investment.opportunity_service import market_evidence


def test_partial_official_pe_is_preserved_without_unlocking_full_valuation():
    data = market_evidence(
        {"id": "hs300", "name": "沪深300"},
        {
            "state": "STRONG",
            "dataQuality": "READY",
            "points": [{"date": "2026-10-14", "close": 100}],
            "indicators": {"ma200": 90, "momentum63": 0.1, "volatility20": 0.2},
            "valuation": {
                "state": "PARTIAL",
                "pe": {
                    "state": "READY",
                    "metric": "PE_TTM",
                    "points": [{"date": "2026-10-14", "value": 12.99}],
                    "latest": 12.99,
                },
                "pb": {"state": "UNAVAILABLE", "points": []},
            },
        },
    )
    assert data["trend"]["state"] == "READY"
    assert data["valuation"]["state"] == "PARTIAL"
    assert data["valuation"]["pe"]["latest"] == 12.99
    assert data["earnings"]["state"] == "UNKNOWN"
    assert "当前指数估值" in data["missingEvidence"]


def test_index_collection_records_are_exposed_as_evidence_not_synthesized():
    record = {
        "status": "FAILED",
        "collectedAt": "2026-10-15T12:00:00+08:00",
        "errorClass": "HTTPStatusError",
    }
    data = market_evidence(
        {"id": "hs300"}, {"state": "NOT_COLLECTED", "indexCollection": record}
    )
    assert data["collection"] == record


def test_current_pe_with_short_history_remains_partial_without_a_percentile():
    data = market_evidence(
        {"id": "a500"},
        {
            "state": "READY",
            "dataQuality": "READY",
            "valuation": {
                "state": "PARTIAL",
                "pe": {
                    "state": "INSUFFICIENT",
                    "latest": 13,
                    "percentile": None,
                    "points": [{"date": "2026-10-14", "value": 13}],
                },
                "pb": {"state": "UNAVAILABLE"},
            },
        },
    )
    assert data["valuation"]["state"] == "PARTIAL"
    assert data["valuation"]["pe"]["percentile"] is None


def partial_history():
    end = date(2026, 10, 14)
    points = [
        {"date": str(end - timedelta(days=199 - i)), "close": 100 + i}
        for i in range(200)
    ]
    return {
        "state": "CONFLICT",
        "dataQuality": "CONFLICT",
        "trendQuality": "READY",
        "trendWindow": {
            "start": points[0]["date"],
            "end": str(end),
            "sampleCount": 200,
        },
        "asOf": str(end),
        "points": points,
        "indicators": {"ma200": 199.5, "momentum63": 0.1, "volatility20": 0.2},
    }


def test_complete_current_trend_window_is_independent_of_old_history_gaps():
    data = market_evidence({"id": "a500"}, partial_history())
    assert data["trend"]["state"] == "READY"
    assert data["historyQuality"] == "CONFLICT"
    assert data["trendWindow"]["sampleCount"] == 200
    assert data["risk"]["state"] == "PARTIAL" and data["risk"]["maxDrawdown"] is None
    assert (
        data["valuation"]["state"] != "READY" and data["earnings"]["state"] == "UNKNOWN"
    )


@pytest.mark.parametrize(
    "change", ["stale", "unverified", "short", "wrong_end", "duplicate"]
)
def test_partial_window_cannot_bypass_current_evidence_integrity(change):
    raw = partial_history()
    if change in {"stale", "unverified"}:
        raw["state"] = change.upper()
    elif change == "short":
        raw["trendWindow"]["sampleCount"] = 199
    elif change == "wrong_end":
        raw["trendWindow"]["end"] = "2026-10-13"
    else:
        raw["points"][0] = raw["points"][1]
    assert market_evidence({"id": "a500"}, raw)["trend"]["state"] != "READY"
