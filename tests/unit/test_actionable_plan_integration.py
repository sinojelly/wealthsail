"""Existing analysis reads expose only current, same-asset research plans."""

from datetime import datetime, timedelta
from importlib import import_module
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 17, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def snapshot(*, count=30, basis="INDEX_PRICE", quality="READY"):
    return {
        "state": "READY",
        "dataQuality": quality,
        "seriesKind": basis,
        "source": "https://quotes.test/current",
        "retrievedAt": NOW.isoformat(),
        "asOf": NOW.date().isoformat(),
        "points": [
            {
                "date": (NOW.date() - timedelta(days=count - i - 1)).isoformat(),
                "close": 100 + i % 2,
                "high": 102,
                "low": 99,
            }
            for i in range(count)
        ],
        "valuation": {
            "state": "PARTIAL",
            "requestedEnd": NOW.date().isoformat(),
            "sourceUri": "https://quotes.test/valuation",
            "pe": {
                "state": "READY",
                "percentile": 35,
                "sourceUri": "https://quotes.test/valuation",
            },
        },
        "fundamentals": {
            "valuationConfig": {
                "verified": True,
                "sourceUri": "https://quotes.test/valuation",
            }
        },
    }


def plan(facts, **kwargs):
    module = import_module("backend.investment.service")
    return module.actionable_plan_for_analysis(
        facts, "index", "a500", now=NOW, **kwargs
    )


def test_partial_valuation_and_short_ma200_still_form_current_left_plan():
    result = plan(snapshot())
    assert result["tracks"][0]["state"] == "CANDIDATE"
    assert result["indicators"]["ma200"] is None
    assert result["priceBasis"] == "INDEX_POINTS"
    assert result["subjectId"] == "a500"
    assert result["indicators"]["pePercentile"] == 35


@pytest.mark.parametrize("quality", ["STALE", "UNVERIFIED", "CONFLICT"])
def test_unusable_current_evidence_does_not_expose_active_coordinates(quality):
    result = plan(snapshot(quality=quality))
    assert all(row["state"] == "INSUFFICIENT" for row in result["tracks"])
    assert all(row["entryZoneMin"] is None for row in result["tracks"])
    assert all(row.get("technicalTriggered") is False for row in result["tracks"])
    assert all(row.get("entryReady") is False for row in result["tracks"])
    assert all(row.get("inBuyZone") is False for row in result["tracks"])
    assert result["orderPriceAvailable"] is False


def test_calendar_verified_short_tail_can_survive_old_history_gap():
    facts = snapshot(quality="CONFLICT")
    facts["trendQuality"] = "INSUFFICIENT"
    facts["trendWindow"] = {
        "start": facts["points"][0]["date"],
        "end": facts["asOf"],
        "sampleCount": 30,
    }
    result = plan(facts)
    assert result["tracks"][0]["state"] == "CANDIDATE"
    assert result["inputWindow"]["sampleCount"] == 30


def test_future_retrieval_cannot_be_used_even_when_observation_date_is_current():
    facts = snapshot()
    facts["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    assert all(row["state"] == "INSUFFICIENT" for row in plan(facts)["tracks"])


@pytest.mark.asyncio
async def test_analysis_read_attaches_own_fund_plan_without_another_instrument_quote(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    module = import_module("backend.investment.service")
    monkeypatch.setattr(module, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(
        module, "expected_trading_day", AsyncMock(return_value=NOW.date())
    )
    facts = snapshot(basis="ETF_HFQ")
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "result": {
            key: value
            for key, value in facts.items()
            if key not in {"points", "source", "seriesKind"}
        },
        "points": facts["points"],
        "as_of": NOW.date(),
        "retrieved_at": NOW,
        "source_uri": facts["source"],
        "input_hash": "prices",
        "series_kind": "ETF_HFQ",
    }
    result = await module.analysis(conn, "fund", "own-etf")
    assert result["actionableZones"]["subjectId"] == "own-etf"
    assert result["actionableZones"]["priceBasis"] == "ETF_HFQ"
    assert result["actionableZones"]["orderPriceAvailable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("current", [False, True])
async def test_unscored_close_only_snapshot_still_checks_calendar_and_source(
    monkeypatch, current
):
    from unittest.mock import AsyncMock

    module = import_module("backend.investment.service")
    monkeypatch.setattr(module, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(
        module, "expected_trading_day", AsyncMock(return_value=NOW.date())
    )
    facts = snapshot(basis="UNIT_NAV")
    day = NOW.date() if current else NOW.date() - timedelta(days=1)
    facts["points"][-1]["date"] = day.isoformat()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "result": {
            key: value
            for key, value in facts.items()
            if key not in {"points", "source", "seriesKind"}
        },
        "points": facts["points"],
        "as_of": day,
        "retrieved_at": NOW,
        "source_uri": facts["source"],
        "input_hash": "prices",
        "series_kind": "UNIT_NAV",
    }
    result = await module.analysis(conn, "fund", "own-fund")
    assert result["dataQuality"] == ("UNVERIFIED" if current else "STALE")
    assert all(
        row["state"] == "INSUFFICIENT" for row in result["actionableZones"]["tracks"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "old_request",
        "wrong_source",
        "missing_mapping",
        "pb_only_stale",
        "pb_and_old_request",
        "stale_pe",
        None,
    ],
)
@pytest.mark.parametrize("oversold", [False, True])
async def test_analysis_valuation_validity_only_controls_its_own_left_branch(
    monkeypatch, problem, oversold
):
    from unittest.mock import AsyncMock

    module = import_module("backend.investment.service")
    monkeypatch.setattr(module, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(
        module, "expected_trading_day", AsyncMock(return_value=NOW.date())
    )
    facts = snapshot(basis="UNIT_NAV")
    for index, point in enumerate(facts["points"]):
        close = (
            300 - 2 * index if oversold else 100 + 2 * index + (3 if index % 2 else 0)
        )
        point.update(close=close, high=close + 1, low=close - 1)
    end = (
        NOW.date() - timedelta(days=1)
        if problem in {"old_request", "pb_and_old_request"}
        else NOW.date()
    )
    source = (
        "https://borrowed.test/valuation"
        if problem == "wrong_source"
        else "https://quotes.test/valuation"
    )
    facts["fundamentals"].update(
        indexId="hs300",
        valuationConfig={
            "verified": True,
            "sourceUri": "https://quotes.test/valuation",
            "publishHour": 18,
        },
        valuation={
            "state": "STALE"
            if problem in {"pb_only_stale", "pb_and_old_request", "stale_pe"}
            else "READY",
            "requestedEnd": end.isoformat(),
            "sourceUri": source,
            "pe": {
                "state": "STALE" if problem == "stale_pe" else "READY",
                "metric": "PE_TTM",
                "sourceUri": source,
                "points": [
                    {
                        "date": (
                            end - timedelta(days=1200 - (1200 * i // 756))
                        ).isoformat(),
                        "value": 10 if i == 756 else 12,
                    }
                    for i in range(757)
                ],
            },
            "pb": {
                "state": "STALE"
                if problem in {"pb_only_stale", "pb_and_old_request"}
                else "UNAVAILABLE"
            },
        },
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {
            "result": {
                key: value
                for key, value in facts.items()
                if key not in {"points", "source", "seriesKind"}
            },
            "points": facts["points"],
            "as_of": NOW.date(),
            "retrieved_at": NOW,
            "source_uri": facts["source"],
            "input_hash": "prices",
            "series_kind": "UNIT_NAV",
        },
        {
            "metadata": {
                "research": {
                    "sourceVerified": True,
                    "sourceUri": facts["source"],
                    "publishHour": None,
                }
            },
            "mapping_valid": problem != "missing_mapping",
        },
    ]
    result = await module.analysis(conn, "fund", "own-fund")
    assert result["dataQuality"] == "READY"
    assert result["valueEvidence"]["evidence"]["pe"]["state"] == (
        "STALE" if problem == "stale_pe" else "READY"
    )
    if problem in {"old_request", "pb_only_stale", "pb_and_old_request", "stale_pe"}:
        assert result["valuation"]["state"] == "STALE"
    plan = result["actionableZones"]
    pe_usable = problem in {None, "pb_only_stale"}
    assert (plan["indicators"]["pePercentile"] is not None) is pe_usable
    left = plan["tracks"][0]
    # A usable cheap-PE branch remains evidence, but v5 gives an exit veto
    # priority when this rising-price fixture has already crossed its sell band.
    assert left["state"] == (
        "CANDIDATE" if oversold else "WATCH" if pe_usable else "INSUFFICIENT"
    )
    if not oversold and pe_usable:
        assert any(reason == "PE历史分位不高于40%：满足" for reason in left["reasons"])
        assert left["inSellZone"] is True
        assert left["entryBlocked"] is True
        assert left["entryReady"] is False
