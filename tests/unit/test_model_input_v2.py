"""Model reading views never replace the frozen evidence or price authority."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.macro.model_projection import model_packet
from test_quantitative_scan import scan
from test_research_frozen_contract_projection import frozen


VERSION = "daily-model-input-v2"


def compact(packet):
    return model_packet(packet, version=VERSION)


def test_current_funds_keep_decisions_and_identity_without_server_price_geometry():
    raw = scan(1)
    row = raw["items"][0]
    row.update(
        priceTimeBasis="CONFIRMED_UNIT_NAV",
        entryBlocked=True,
        entryBlockReason="DELAYED_NAV_MARKET_DROP",
        subscriptionRestricted=True,
        subscriptionStatus={"purchaseStatus": "LIMITED", "dailyLimitCny": "10"},
        industryRiskWarning="未解除的砍单风险",
        relativeStrength={"state": "WEAK", "weakening": True, "reasons": ["风险"]},
    )
    row["indicators"].update(p30="0.9", ma60="1.01", navPercentile1y=95)
    row["tracks"][0].update(entryReady=False, inSellZone=True)
    before = deepcopy(raw)
    projected = compact({"quantitativeScan": raw})["quantitativeScan"]["items"][0]
    for field in (
        "subjectKind",
        "subjectId",
        "fundCode",
        "priceBasis",
        "priceTimeBasis",
        "asOf",
        "unit",
        "currentPrice",
        "historyQuality",
        "historySampleCount",
        "entryBlocked",
        "entryBlockReason",
        "subscriptionRestricted",
        "subscriptionStatus",
        "industryRiskWarning",
    ):
        assert projected[field] == row[field]
    assert projected["tracks"][0]["track"] == "RIGHT_TREND"
    assert projected["tracks"][0]["priceState"] == "READY"
    assert projected["tracks"][0]["entryReady"] is False
    assert projected["tracks"][0]["inSellZone"] is True
    assert "entryZoneMin" not in projected["tracks"][0]
    assert "p30" not in projected["indicators"]
    assert projected["indicators"]["navPercentile1y"] == 95
    assert projected["relativeStrength"]["weakening"] is True
    assert raw == before


def test_previous_plan_is_a_comparison_identity_without_another_quantitative_dump():
    old = scan(1)["items"][0]
    old.update(selectedTrack="RIGHT_TREND", industryRiskWarning="风险未解除")
    result = compact({"previousActionablePlans": [old]})["previousFundPlans"][0]
    assert result["fundCode"] == old["fundCode"]
    assert result["selectedTrack"] == "RIGHT_TREND"
    assert result["asOf"] == old["asOf"]
    assert result["priceBasis"] == old["priceBasis"]
    assert result["industryRiskWarning"] == "风险未解除"
    assert "indicators" not in result and "signals" not in result
    assert result["tracks"][0]["entryZoneMin"] == old["tracks"][0]["entryZoneMin"]


def test_frozen_queries_and_risk_states_remain_exact_and_independently_identified():
    packet = frozen()
    packet["previousIndustryRiskStates"] = [
        {"industry": "ROBOT", "riskFlags": ["ORDER_CUTS"]}
    ]
    before = deepcopy(packet)
    result = compact(packet)
    queries = [
        q
        for row in result["industryCycleResearch"]["candidates"]
        for q in row["queries"]
    ]
    manifest = packet["researchDiscovery"]["auditManifest"]
    assert {(q["taskId"], q["industryId"], q["kind"], q["query"]) for q in queries} == {
        (q["taskId"], q["industryId"], q["kind"], q["query"]) for q in manifest["tasks"]
    }
    assert result["previousIndustryRiskStates"] == packet["previousIndustryRiskStates"]
    assert result["industryCycleResearch"]["window"] == manifest["window"]
    assert packet == before


def test_exact_repeated_news_text_is_shared_but_same_url_distinct_facts_survive():
    text = "公司披露真实利润与回款变化，但仍需核验报告期和风险。" * 8
    packet = {
        "documents": [{"id": "doc", "url": "https://example.com/a", "quote": text}],
        "alphaPublic": {
            "catalysts": [
                {
                    "id": "n1",
                    "sourceUri": "https://example.com/a",
                    "content": text,
                    "sectorBuckets": ["AI"],
                },
                {
                    "id": "n2",
                    "sourceUri": "https://example.com/a",
                    "content": "不同经济事实与负面风险",
                    "sectorBuckets": ["AI"],
                },
            ]
        },
        "institutionalSignals": {
            "researchViews": [{"mediaEvidenceId": "n1", "summary": text}]
        },
    }
    before = deepcopy(packet)
    result = compact(packet)
    refs = [
        result["documents"][0]["quote"],
        result["alphaPublic"]["catalysts"][0]["content"],
        result["institutionalSignals"]["researchViews"][0]["summary"],
    ]
    assert refs[0] == refs[1] == refs[2]
    assert result["sharedText"][refs[0]["textRef"]] == text
    assert result["alphaPublic"]["catalysts"][1]["content"] == "不同经济事实与负面风险"
    assert len(result["alphaPublic"]["catalysts"]) == 2
    assert packet == before


def test_observation_metadata_references_do_not_merge_definition_breaks_or_points():
    rows = [
        {
            "code": "M1",
            "referenceDate": f"2026-{month:02}-01",
            "value": month,
            "sourceUri": "https://example.com/m1",
            "timeBasis": "RETRIEVAL_ONLY",
            "metadata": {
                "definitionVersion": "old" if month == 1 else "new",
                "definitionNote": "真实口径边界",
            },
        }
        for month in (1, 2, 3)
    ]
    result = compact({"alphaPublic": {"observations": rows}})["alphaPublic"]
    groups = result["observations"]
    assert len(groups) == 2
    assert [p["value"] for g in groups for p in g["points"]] == [1, 2, 3]
    assert (
        result["observationMetadata"][groups[0]["metadataRef"]]["definitionVersion"]
        == "old"
    )
    assert (
        result["observationMetadata"][groups[1]["metadataRef"]]["definitionVersion"]
        == "new"
    )


def test_prompt_uses_versioned_compact_view_and_five_sections():
    from backend.macro.daily_research import build_prompt

    prompt = build_prompt({"quantitativeScan": scan(1)})
    public = json.loads(prompt.split("\n")[-1])
    assert public["modelProjectionVersion"] == VERSION
    for heading in (
        "一、事实与来源",
        "二、经济研究",
        "三、基金机会",
        "四、行业审核与风险",
        "五、结构化输出",
    ):
        assert heading in prompt
    assert "topicCatalog每批最多64" in prompt
    assert "有足够真实线索时形成6至10个valueDirections" in prompt
    assert "绝不是洞察或基金关联白名单" in prompt
    assert "上限与目标不作凑数要求" in prompt
    assert "原始Packet" in prompt and "服务器" in prompt


def test_unsupported_reading_projection_version_is_not_silently_accepted():
    with pytest.raises(ValueError):
        model_packet({}, version="daily-model-input-future")


def test_reference_expansion_preserves_exact_news_facts_and_source_dates():
    from scripts.audit_daily_research_input import expand_text_references

    text = "同一公司同一报告期的真实事实，不能被另一条新闻替代。" * 8
    rows = [
        {
            "id": f"news-{i}",
            "content": text,
            "title": "真实原文",
            "sectorBuckets": ["AI"],
            "publishedAt": f"2026-11-0{i + 1}T10:00:00Z",
            "sourceUri": "https://example.com/shared",
            "sourceKey": "PUBLIC",
            "retrievedAt": "2026-11-03T00:00:00Z",
            "factVerification": "UNVERIFIED",
        }
        for i in range(2)
    ]
    result = compact({"alphaPublic": {"catalysts": rows}})
    expanded = expand_text_references(result)["alphaPublic"]
    restored = [
        {**expanded["catalystDefaults"], **row} for row in expanded["catalysts"]
    ]
    assert restored == rows
    assert rows[0]["publishedAt"] != rows[1]["publishedAt"]


@pytest.mark.skipif(
    not Path("artifacts/research-recovery/ecbfb663-frozen.json").is_file(),
    reason="private original export is local only; no production access",
)
def test_real_frozen_input_reduction_is_at_least_thirty_percent_without_mutation():
    from copy import deepcopy
    from scripts.audit_daily_research_input import audit, load_frozen_packet, legacy_prompt
    from backend.macro.daily_research import REPORT_SCHEMA

    path = Path("artifacts/research-recovery/ecbfb663-frozen.json")
    original = path.read_bytes()
    packet = load_frozen_packet(path)
    summary = audit(packet, "ecf3f90")
    assert summary["old"]["promptUtf8Bytes"] == 592170
    assert summary["old"]["projectionUtf8Bytes"] == 571818
    assert summary["oldDirectModelPacketUtf8Bytes"] == 570924
    assert summary["promptReductionPct"] >= 30
    # The cycle dashboard intentionally adds one optional, permissive field.
    # Keep the original no-unrelated-schema-drift protection for every other
    # property and constraint, rather than accepting arbitrary schema changes.
    schema = deepcopy(REPORT_SCHEMA)
    assert "cycleInsights" not in schema["required"]
    schema["properties"].pop("cycleInsights")
    assert schema == legacy_prompt(packet, "ecf3f90")[2]
    assert summary["originalPacketUnchanged"] is True
    assert path.read_bytes() == original
