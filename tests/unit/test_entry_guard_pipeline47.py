"""Entry restrictions survive the real report validation and scan projection."""

from copy import deepcopy

import pytest

from backend.macro import daily_research
from test_daily_entry_guards import fixtures
from test_daily_research import NOW, report


def test_validated_report_preserves_delayed_nav_and_subscription_warnings():
    surfaces, packet = fixtures()
    item = surfaces["quantitativeScan"]["items"][0]
    item["signals"][0]["candidate"] = True
    packet.update(
        directions=[{"id": "a-share:broad"}],
        fundSubscriptionStatus={
            "161129": {"purchaseStatus": "LIMITED", "dailyLimitCny": "10"}
        },
        quantitativeScan={
            "methodVersion": "fund-quant-scan-v2",
            "asOf": NOW.isoformat(),
            "items": [item],
        },
    )
    raw = report()
    raw["valueDirections"] = []
    before = deepcopy(packet)
    result = daily_research.validate_report(raw, packet, {}, completed_at=NOW)
    actual = result["quantitativeScan"]["items"][0]
    assert actual["entryStatus"] == "⚠️ 净值滞后1天·当晚外盘急跌暂缓入场"
    assert actual["subscriptionLabel"] == "⛔ 单日限购10元"
    assert actual["signals"][0]["entryReady"] is False
    assert actual["signals"][0]["inEntryZone"] is True
    assert packet == before


def guarded_scan():
    surfaces, packet = fixtures()
    item = surfaces["quantitativeScan"]["items"][0]
    item["signals"][0]["candidate"] = True
    surfaces["quantitativeScan"].update(
        methodVersion="fund-quant-scan-v2", asOf=NOW.isoformat()
    )
    return daily_research.apply_entry_guards(surfaces, packet, NOW)["quantitativeScan"]


def test_guarded_v2_scan_can_be_revalidated_for_tracking_without_losing_guard():
    from backend.investment.quantitative_scan import public_quantitative_scan

    original = guarded_scan()
    result = public_quantitative_scan(original, NOW)
    actual = result["items"][0]
    assert actual["signals"][0]["entryReady"] is False
    assert actual["signals"][0]["inEntryZone"] is True
    assert actual["tracks"][0]["entryBlocked"] is True
    assert actual["currentPrice"] == original["items"][0]["currentPrice"]


def test_guarded_v2_scan_cannot_forge_entry_ready_back_to_true():
    from backend.investment.quantitative_scan import public_quantitative_scan

    value = guarded_scan()
    value["items"][0]["signals"][0]["entryReady"] = True
    with pytest.raises(ValueError, match="entry readiness"):
        public_quantitative_scan(value, NOW)
