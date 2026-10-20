"""Entry restrictions survive the real report validation and scan projection."""

from copy import deepcopy

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
