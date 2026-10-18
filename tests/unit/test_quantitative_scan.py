"""Public scans retain the exact report's prices, independently of model selection."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.macro import daily_repository, daily_research
from test_daily_research import NOW, report


def scan_plan(code="008281", **updates):
    return {
        "methodVersion": "actionable-zones-v3",
        "subjectKind": "nav",
        "subjectId": code,
        "fundCode": code,
        "market": "FUND_CN",
        "label": "实际基金" + code,
        "priceBasis": "UNIT_NAV",
        "unit": "CNY/份",
        "asOf": (NOW - timedelta(days=1)).date().isoformat(),
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "sourceUri": f"https://fund.eastmoney.com/{code}.html",
        "currentPrice": "1.0000",
        "historyQuality": "READY",
        "historySampleCount": 245,
        "indicators": {"rsi14": 39.5, "navVolatility14": 0.012},
        "tracks": [
            {
                "track": "RIGHT_TREND",
                "state": "WATCH",
                "priceState": "READY",
                "entryZoneMin": "0.9900",
                "entryZoneMax": "1.0100",
                "stopLossPrice": "0.9600",
                "takeProfitT1": "1.0500",
                "takeProfitT2": "1.0800",
                "reasons": ["技术观察条件尚未满足"],
            }
        ],
        "signals": [
            {
                "track": "RIGHT_TREND",
                "triggerState": "WATCH",
                "inEntryZone": True,
                "distanceToEntryPct": 0,
            }
        ],
        **updates,
    }


def scan(count=30):
    return {
        "methodVersion": "fund-quant-scan-v1",
        "asOf": NOW.isoformat(),
        "minimumHistorySamples": 220,
        "coverage": {"pricedFundCount": count, "targetHistoryFundCount": count},
        "items": [scan_plan(f"{100000 + i:06}") for i in range(count)],
    }


@pytest.mark.parametrize("count", [1, 21, 30, 32])
def test_validated_report_copies_every_actual_server_scan_item(count):
    public = scan(count)
    payload = daily_research.validate_report(
        report(),
        {
            "asOf": NOW.isoformat(),
            "directions": [{"id": "a-share:broad"}],
            "quantitativeScan": public,
        },
        {},
        completed_at=NOW,
    )
    assert len(payload["quantitativeScan"]["items"]) == count
    first = payload["quantitativeScan"]["items"][0]
    assert first["tracks"] == public["items"][0]["tracks"]
    assert first["signals"] == public["items"][0]["signals"]
    public["items"][0]["tracks"][0]["entryZoneMin"] = "99"
    assert first["tracks"][0]["entryZoneMin"] == "0.9900"


def test_model_cannot_replace_server_quantitative_scan():
    raw = {**report(), "quantitativeScan": scan()}
    with pytest.raises(ValueError):
        daily_research.validate_report(raw, {}, {}, completed_at=NOW)


def test_public_scan_omits_private_fields_and_unusable_prices():
    from backend.investment.quantitative_scan import public_quantitative_scan

    raw = scan(3)
    raw["private"] = "never publish"
    raw["items"][0]["private"] = "never publish"
    raw["items"][1]["tracks"][0]["stopLossPrice"] = None
    raw["items"][2]["availableAt"] = (NOW + timedelta(seconds=1)).isoformat()
    clean = public_quantitative_scan(raw, NOW)
    assert len(clean["items"]) == 1
    assert clean["coverage"] == {"pricedFundCount": 1, "targetHistoryFundCount": 1}
    assert "never publish" not in str(clean)


def test_public_scan_does_not_trust_forged_signal_or_duplicate_identity():
    from backend.investment.quantitative_scan import public_quantitative_scan

    raw = scan(2)
    raw["items"][0]["signals"][0]["inEntryZone"] = False
    with pytest.raises(ValueError):
        public_quantitative_scan(raw, NOW)


@pytest.mark.parametrize("distance", [float("nan"), float("inf"), True, "0"])
def test_public_scan_rejects_nonfinite_or_untyped_signal_distance(distance):
    from backend.investment.quantitative_scan import public_quantitative_scan

    raw = scan(1)
    raw["items"][0]["signals"][0]["distanceToEntryPct"] = distance
    with pytest.raises(ValueError):
        public_quantitative_scan(raw, NOW)
    raw = scan(2)
    raw["items"][1] = deepcopy(raw["items"][0])
    with pytest.raises(ValueError):
        public_quantitative_scan(raw, NOW)


@pytest.mark.asyncio
async def test_old_report_scan_fallback_uses_exact_ready_run_not_latest_attempt():
    run = uuid4()
    old = {
        "run_id": run,
        "slot_date": NOW.date(),
        "scheduled_for": NOW,
        "dispatched_at": NOW,
        "recorded_at": NOW,
        "status": "READY",
        "payload": {"asOf": NOW.isoformat(), "summary": "earlier valid report"},
        "public_packet": {"quantitativeScan": scan(30)},
    }
    latest = {
        **old,
        "run_id": uuid4(),
        "status": "FAILED",
        "payload": None,
        "public_packet": {"quantitativeScan": scan(1)},
    }
    conn = AsyncMock()
    conn.fetchrow.side_effect = [latest, old]
    result = await daily_repository.read_daily_research(conn, NOW)
    assert result["reportRunId"] == str(run)
    assert len(result["report"]["quantitativeScan"]["items"]) == 30
    assert "quantitativeScan" not in old["payload"]


@pytest.mark.asyncio
async def test_embedded_report_scan_is_not_replaced_by_packet_or_later_clock():
    frozen = scan(1)
    row = {
        "run_id": uuid4(),
        "slot_date": NOW.date(),
        "scheduled_for": NOW,
        "dispatched_at": NOW,
        "recorded_at": NOW,
        "status": "READY",
        "payload": {"asOf": NOW.isoformat(), "quantitativeScan": frozen},
        "public_packet": {"quantitativeScan": scan(30)},
    }
    conn = AsyncMock()
    conn.fetchrow.return_value = row
    result = await daily_repository.read_daily_research(conn, NOW + timedelta(days=30))
    assert len(result["report"]["quantitativeScan"]["items"]) == 1
