"""The model selects identity; full server snapshots remain the price authority."""

from copy import deepcopy
import json

import pytest

from backend.macro import daily_research as research
from backend.investment.quantitative_scan import public_quantitative_scan
from test_daily_research import NOW
from test_quantitative_scan import scan


def test_source_timeout_remains_explicit_in_the_full_and_compact_packet():
    full = research.make_research_packet(
        {},
        [],
        [],
        [],
        now=NOW,
        external={
            "sourceResults": {
                "SSE_ETF_SHARES": {"status": "TIMEOUT", "errorClass": "TimeoutError"}
            }
        },
    )
    assert full["sourceResults"]["SSE_ETF_SHARES"]["status"] == "TIMEOUT"
    assert research.model_packet(full)["sourceResults"] == full["sourceResults"]


def test_dynamic_nav_collection_results_survive_the_public_packet_without_raw_errors():
    statuses = {
        "000051": {
            "status": "SUCCESS",
            "records": 500,
            "cache": "REUSED_CONFIRMED",
            "retrievedAt": NOW.isoformat(),
        },
        "028777": {"status": "SUCCESS", "records": 5, "retrievedAt": NOW.isoformat()},
        "011035": {
            "status": "FAILED",
            "errorClass": "TimeoutError",
            "error": "PRIVATE",
        },
        "bad-code": {"status": "FAILED", "error": "PRIVATE"},
        "012733": {"status": "SUCCESS", "records": -1, "retrievedAt": "not-a-date"},
    }
    full = research.make_research_packet(
        {},
        [],
        [],
        [],
        now=NOW,
        external={"dynamicNavSourceResults": statuses},
    )
    assert full["dynamicNavSourceResults"] == {
        "000051": statuses["000051"],
        "028777": statuses["028777"],
        "011035": {"status": "FAILED", "errorClass": "TimeoutError"},
        "012733": {"status": "SUCCESS"},
    }
    assert (
        research.model_packet(full)["dynamicNavSourceResults"]
        == full["dynamicNavSourceResults"]
    )
    assert "PRIVATE" not in str(full)


def test_new_scan_keeps_all_40_funds_and_separates_geometry_from_a_buy_signal():
    raw = scan(40)
    raw["methodVersion"] = "fund-quant-scan-v2"
    for row in raw["items"]:
        row["signals"][0].update(candidate=False, entryReady=False)
    result = public_quantitative_scan(raw, NOW)
    assert len(result["items"]) == 40
    assert result["items"][0]["signals"][0] == {
        "track": "RIGHT_TREND",
        "triggerState": "WATCH",
        "inEntryZone": True,
        "distanceToEntryPct": 0,
        "candidate": False,
        "entryReady": False,
    }


@pytest.mark.parametrize("field", ["candidate", "entryReady"])
def test_new_scan_rejects_a_forged_ready_signal(field):
    raw = scan(1)
    raw["methodVersion"] = "fund-quant-scan-v2"
    raw["items"][0]["signals"][0].update(candidate=False, entryReady=False)
    raw["items"][0]["signals"][0][field] = True
    with pytest.raises(ValueError):
        public_quantitative_scan(raw, NOW)


def test_compact_model_packet_uses_one_price_table_without_mutating_the_audit_packet():
    full = {
        "asOf": NOW.isoformat(),
        "directions": [{"id": "a-share:broad"}],
        "quantitativeScan": scan(40),
        "documents": [{"id": "official", "quote": "公开政策"}],
        "alphaPublic": {
            "catalysts": [
                {
                    "id": "cls-parent-13",
                    "parentSourceId": "cls-parent",
                    "content": "大摩算力预测，机构观点待验证",
                    "sourceUri": "https://www.cls.cn/detail/123",
                    "sectorBuckets": ["AI_SEMICONDUCTOR"],
                }
            ]
        },
        "institutionalSignals": {
            "researchViews": [
                {
                    "mediaEvidenceId": "cls-parent-13",
                    "summary": "大摩算力预测",
                    "sourceUri": "https://www.cls.cn/detail/123",
                }
            ]
        },
    }
    for row in full["quantitativeScan"]["items"]:
        row["indicators"]["rawTechnicalHistory"] = "public historical details " * 300
    full["discoveryActionableZones"] = deepcopy(full["quantitativeScan"]["items"])
    full["themeActionableZones"] = deepcopy(full["quantitativeScan"]["items"])
    original = json.dumps(full, sort_keys=True)
    compact = research.model_packet(full)
    assert len(compact["quantitativeScan"]["items"]) == 40
    assert compact["quantitativeScan"]["items"][0]["fundCode"] == "100000"
    assert (
        compact["quantitativeScan"]["items"][0]["tracks"][0]["takeProfitT1"] == "1.0500"
    )
    assert "discoveryActionableZones" not in compact
    assert "themeActionableZones" not in compact
    assert "rawTechnicalHistory" not in json.dumps(compact)
    assert "大摩算力预测" in json.dumps(compact, ensure_ascii=False)
    assert "cls-parent-13" in json.dumps(compact)
    assert len(json.dumps(compact)) < len(original) / 2
    assert json.dumps(full, sort_keys=True) == original


def test_compact_model_input_keeps_entry_guards_and_subscription_limits():
    raw = scan(1)
    row = raw["items"][0]
    row.update(
        subscriptionRestricted=True,
        subscriptionLabel="⛔ 单日限购10元",
        subscriptionStatus={"purchaseStatus": "LIMITED", "dailyLimitCny": "10"},
        preferredEntryEligible=False,
        isDelayedNav=True,
        entryBlocked=True,
        entryStatus="⚠️ 净值滞后1天·当晚外盘急跌暂缓入场",
        entryStatusTone="amber",
    )
    row["indicators"]["navPercentile1y"] = 95
    row["tracks"][0].update(entryReady=False, inBuyZone=False, entryBlocked=True)
    compact = research.model_packet({"quantitativeScan": raw})
    result = compact["quantitativeScan"]["items"][0]
    assert result.get("subscriptionRestricted") is True
    assert result.get("subscriptionStatus", {}).get("dailyLimitCny") == "10"
    assert result.get("subscriptionLabel") == "⛔ 单日限购10元"
    assert result.get("preferredEntryEligible") is False
    assert result.get("entryBlocked") is True
    assert result.get("entryStatus") == "⚠️ 净值滞后1天·当晚外盘急跌暂缓入场"
    assert result["indicators"].get("navPercentile1y") == 95
    assert result["tracks"][0].get("entryReady") is False


def test_repair_only_separates_two_known_fund_codes_and_preserves_identifiers_and_amounts():
    codes = {"018344", "004854", "000051", "000962"}
    assert (
        research.separate_fund_codes(
            "机器人基金0183445004854与宽基0000515000962可研究。", codes
        )
        == "机器人基金018344与004854与宽基000051与000962可研究。"
    )
    assert (
        research.separate_fund_codes("基金018344004854并列。", codes)
        == "基金018344与004854并列。"
    )
    text = "订单号0183445004854，金额0000515000962元，日期20261019。"
    assert research.separate_fund_codes(text, codes) == text
    assert (
        research.separate_fund_codes("未知基金0183445999999", codes)
        == "未知基金0183445999999"
    )


def test_code_repair_never_modifies_source_ids_or_identity_lists():
    from backend.macro.model_projection import readable_fund_codes

    raw = {
        "sourceIds": ["0183445004854"],
        "summary": "基金0183445004854",
        "events": [{"watchFor": ["基金0183445004854"]}],
    }
    result = readable_fund_codes(raw, {"018344", "004854"})
    assert result["sourceIds"] == ["0183445004854"]
    assert result["summary"] == "基金018344与004854"
    assert result["events"][0]["watchFor"] == ["基金018344与004854"]


def test_compact_projection_keeps_real_technical_fields_and_institutional_source_closure():
    raw = scan(1)
    raw["items"][0]["indicators"].update(
        bollLower="0.95",
        bollMiddle="1",
        bollUpper="1.05",
        ma60="1.02",
        momentum20="0.01",
    )
    packet = {
        "quantitativeScan": raw,
        "candidateIndex": [
            {
                "id": "fed",
                "title": "Federal Reserve policy calendar",
                "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            }
        ],
        "alphaPublic": {
            "catalysts": [
                {
                    "id": "view",
                    "content": "大摩研报预计经济增长",
                    "sectorBuckets": ["OTHER"],
                },
                {
                    "id": "noise",
                    "content": "无行业映射一般线索",
                    "sectorBuckets": ["OTHER"],
                },
            ],
            "sectorCatalysts": {"OTHER": ["view", "noise"]},
        },
        "institutionalSignals": {
            "researchViews": [
                {"mediaEvidenceId": "view", "summary": "大摩研报预计经济增长"}
            ]
        },
    }
    compact = research.model_packet(packet)
    assert compact["quantitativeScan"]["items"][0]["indicators"]["bollLower"] == "0.95"
    assert compact["quantitativeScan"]["items"][0]["indicators"]["ma60"] == "1.02"
    assert compact["candidateIndex"][0]["id"] == "fed"
    assert [row["id"] for row in compact["alphaPublic"]["catalysts"]] == ["view"]
    assert compact["alphaPublic"]["sectorCatalysts"] == {"OTHER": ["view"]}


def test_v2_quantitative_scan_can_support_a_direction_without_model_supplied_prices():
    from test_value_directions import quantitative_packet, direction, validate

    packet = quantitative_packet()
    packet["quantitativeScan"]["methodVersion"] = "fund-quant-scan-v2"
    for row in packet["quantitativeScan"]["items"]:
        for signal in row["signals"]:
            signal.update(
                candidate=signal["triggerState"] == "CANDIDATE",
                entryReady=signal["triggerState"] == "CANDIDATE"
                and signal["inEntryZone"],
            )
    result = validate(
        [direction(macroCodes=[], quantitativeFundCodes=["005112"])], packet
    )
    assert result[0]["plans"][0]["currentPrice"] == "1.2500"


def test_compact_macro_history_groups_provenance_without_losing_dates_values_or_definition_changes():
    common = {
        "code": "M1_YOY",
        "unit": "%",
        "frequency": "MONTHLY",
        "sourceKey": "PUBLIC",
        "sourceUri": "https://www.gov.cn/report",
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "publishedAt": None,
        "timeBasis": "RETRIEVAL_ONLY",
    }
    rows = [
        {
            **common,
            "referenceDate": "2025-01-01",
            "value": 3.0,
            "metadata": {"definitionVersion": "old"},
        },
        {
            **common,
            "referenceDate": "2025-02-01",
            "value": 3.1,
            "metadata": {"definitionVersion": "new"},
        },
        {
            **common,
            "referenceDate": "2025-03-01",
            "value": 3.2,
            "metadata": {"definitionVersion": "new"},
        },
    ]
    result = research.model_packet({"alphaPublic": {"observations": rows}})
    groups = result["alphaPublic"]["observations"]
    assert len(groups) == 2
    assert [point["value"] for group in groups for point in group["points"]] == [
        3.0,
        3.1,
        3.2,
    ]
    assert groups[0]["metadata"]["definitionVersion"] == "old"
    assert groups[1]["metadata"]["definitionVersion"] == "new"
    assert groups[1]["points"][-1]["referenceDate"] == "2025-03-01"
    assert groups[1]["retrievedAt"] == NOW.isoformat()
