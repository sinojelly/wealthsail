"""New public positive clues and actual holdings precede historical reviews."""

from copy import deepcopy
from datetime import timedelta

import pytest

from backend.integrations.daily_sources import industry_cycle_research_tasks
from backend.macro.research_topics import freeze_discovery_packet, frozen_manifest
from tests.unit.test_industry_allocation_policy_v2 import NOW


HISTORICAL = ["ROBOT", "BANK", "BROKER", "AI", "RARE_EARTH", "POWER", "GOLD", "DRUG", "COAL", "PCB"]


def packet():
    return {
        "asOf": NOW.isoformat(),
        "previousIndustryRiskStates": [
            {"industry": industry, "industryCycle": {"riskFlags": ["ORDER_CUTS"]}}
            for industry in HISTORICAL
        ],
        "holdingIndustryIds": ["DEFENCE"],
        "alphaPublic": {
            "sectorCatalysts": {"NONFERROUS": ["today-positive"]},
            "catalysts": [{
                "id": "today-positive", "title": "有色金属企业已披露净利润增长50%",
                "publishedAt": NOW.isoformat(), "retrievedAt": NOW.isoformat(),
                "availableAt": NOW.isoformat(), "sourceUri": "https://www.cls.cn/detail/12345",
            }],
        },
    }


def allocate(value):
    return industry_cycle_research_tasks(value, NOW, allocation_version="industry-allocation-v2")


def test_ten_historical_risks_cannot_crowd_out_today_opportunity_then_holdings():
    value = packet()
    before = deepcopy(value)
    result = allocate(value)
    selected = [row["industry"] for row in result["candidates"]]
    assert selected == ["NONFERROUS", "DEFENCE", *HISTORICAL[:8]]
    assert len([query for row in result["candidates"] for query in row["queries"]]) == 50
    assert result["omittedIndustries"] == ["COAL", "PCB"]
    assert result["deferredPages"] == [["COAL", "PCB"]]
    assert result["omittedRiskCandidateCount"] == 2
    reasons = {row["industryId"]: row for row in result["selectionReasons"]}
    assert reasons["NONFERROUS"]["reasonCode"] == "CURRENT_POSITIVE_OPPORTUNITY_PRIORITY"
    assert reasons["DEFENCE"]["reasonCode"] == "HOLDING_INDUSTRY_PRIORITY"
    assert reasons["PCB"]["selected"] is False
    assert value == before


@pytest.mark.parametrize("change", ["negative", "yesterday", "future", "dangling", "no_public_source"])
def test_stale_negative_or_unavailable_clues_do_not_claim_today_positive_priority(change):
    value = packet()
    value.pop("holdingIndustryIds")
    clue = value["alphaPublic"]["catalysts"][0]
    if change == "negative":
        clue["title"] = "有色金属订单下降且企业亏损"
    elif change == "yesterday":
        clue["publishedAt"] = (NOW - timedelta(days=1)).isoformat()
    elif change == "future":
        clue["availableAt"] = (NOW + timedelta(minutes=1)).isoformat()
    elif change == "dangling":
        clue["id"] = "different-source"
    else:
        clue.pop("sourceUri")
    assert [row["industry"] for row in allocate(value)["candidates"]] == HISTORICAL


def test_positive_and_holding_priority_deduplicate_without_clearing_historical_risk():
    value = packet()
    value["holdingIndustryIds"] = ["NONFERROUS", "ROBOT", "DEFENCE", "DEFENCE"]
    result = allocate(value)
    selected = [row["industry"] for row in result["candidates"]]
    assert selected[:4] == ["NONFERROUS", "ROBOT", "DEFENCE", "BANK"]
    assert len(selected) == len(set(selected)) == 10
    assert value["previousIndustryRiskStates"][0]["industryCycle"]["riskFlags"] == ["ORDER_CUTS"]


def test_more_than_ten_today_opportunities_keep_overflow_and_holdings_as_deferred():
    value = packet()
    industries = [*HISTORICAL, "NONFERROUS", "COMMUNICATION"]
    value["alphaPublic"]["sectorCatalysts"] = {
        industry: ["today-positive"] for industry in industries
    }
    result = allocate(value)
    selected = [row["industry"] for row in result["candidates"]]
    assert len(selected) == 10
    assert "DEFENCE" not in selected
    assert set(result["allIndustries"]) == {*industries, "DEFENCE"}
    assert set(result["omittedIndustries"]) == set(result["allIndustries"]) - set(selected)
    assert [industry for page in result["deferredPages"] for industry in page] == result["omittedIndustries"]


def test_frozen_manifest_keeps_its_original_selection_after_input_priority_changes():
    frozen = freeze_discovery_packet(packet(), NOW)
    original = deepcopy(frozen_manifest(frozen))
    assert original["selectedScopes"][:2] == ["NONFERROUS", "DEFENCE"]
    frozen["holdingIndustryIds"] = ["COMMUNICATION"]
    frozen["alphaPublic"] = {}
    result = allocate(frozen)
    assert result == original["candidateContract"]
