"""Bounded audit candidates cover fund plans and stay identical across stages."""

import json
from copy import deepcopy

import pytest

from backend.integrations.daily_sources import industry_cycle_research_tasks
from backend.macro.daily_research import (
    ResearchValidationError,
    build_prompt,
    validate_report,
)
from test_daily_research import NOW, report
from test_industry_cycle import checks
from test_industry_plan_contract import nav


def prompt_packet(packet):
    return json.JSONDecoder().raw_decode(build_prompt(packet).split("\n")[-1])[0]


def test_anchor_only_industry_gets_real_chinese_queries_and_plan_obligation():
    packet = {
        "asOf": NOW.isoformat(),
        "discoveryActionableZones": [nav(discoverySector="NONFERROUS")],
    }
    before = deepcopy(packet)
    tasks = industry_cycle_research_tasks(packet, NOW)
    assert [row["industry"] for row in tasks["candidates"]] == ["NONFERROUS"]
    assert all(
        "有色金属" in query["query"] for query in tasks["candidates"][0]["queries"]
    )
    assert tasks["planReviewEligibility"] == [
        {
            "subjectKind": "nav",
            "subjectId": "004432",
            "requiredReviewIndustryIds": ["NONFERROUS"],
            "industryReviewEligible": True,
        }
    ]
    assert packet == before


def test_composite_news_adds_related_fund_candidates_without_alias_substitution():
    packet = {"alphaPublic": {"sectorCatalysts": {"RARE_EARTH_METALS": ["n1"]}}}
    tasks = industry_cycle_research_tasks(packet, NOW)
    assert {row["industry"] for row in tasks["candidates"]} == {
        "RARE_EARTH_METALS",
        "RARE_EARTH",
        "NONFERROUS",
    }
    queries = {
        row["industry"]: row["queries"][0]["query"] for row in tasks["candidates"]
    }
    assert "有色金属" in queries["NONFERROUS"]
    assert "有色金属" not in queries["RARE_EARTH"]


def test_historical_priority_keeps_budget_but_exposes_ineligible_omitted_plan():
    industries = [
        "ROBOT", "BANK", "BROKER", "AI", "RARE_EARTH", "POWER",
        "GOLD", "DRUG", "COAL", "PCB", "DEFENCE", "COMMUNICATION",
    ]
    packet = {
        "discoveryActionableZones": [nav(discoverySector="NONFERROUS")],
        "previousIndustryRiskStates": [
            {"industry": industry, "industryCycle": {"riskFlags": ["ORDER_CUTS"]}}
            for industry in industries
        ],
    }
    tasks = industry_cycle_research_tasks(packet, NOW)
    assert len(tasks["candidates"]) == 10
    assert sum(len(row["queries"]) for row in tasks["candidates"]) == 50
    assert tasks["omittedPlanIndustryIds"] == ["NONFERROUS"]
    assert tasks["planReviewEligibility"][0]["industryReviewEligible"] is False
    assert "NONFERROUS" in tasks["omittedIndustries"]


def test_prompt_and_validator_share_guarded_candidate_order_and_packet_clock():
    industries = ["BANK", "BROKER", "AI", "RARE_EARTH", "POWER", "GOLD", "DRUG", "COAL", "PCB", "DEFENCE", "COMMUNICATION"]
    packet = {
        "asOf": NOW.isoformat(),
        "directions": [],
        "industryCycleResearch": {"required": True},
        "quantitativeScan": {
            "methodVersion": "fund-quant-scan-v1",
            "asOf": NOW.isoformat(),
            "items": [
                {
                    "discoverySector": industry,
                    "entryReady": True,
                    "fundCode": f"{i:06}",
                    "signals": [],
                }
                for i, industry in enumerate(industries)
            ],
        },
        "fundSubscriptionStatus": {"000000": {"purchaseStatus": "SUSPENDED"}},
    }
    model_input = prompt_packet(packet)
    expected = model_input["industryCycleResearch"]["candidates"]
    raw = {
        **report(),
        "sources": [],
        "events": [],
        "industryCycleReviews": [
            {"industry": row["industry"], "sourceIds": [], "riskChecks": checks()}
            for row in expected
        ],
    }
    result = validate_report(raw, packet, {}, completed_at=NOW)
    assert result["industryResearchCoverage"]["candidateCount"] == 10


def test_model_cannot_expand_ten_review_fifty_query_budget():
    packet = {"asOf": NOW.isoformat(), "industryCycleResearch": {"required": True}}
    raw = {
        **report(),
        "sources": [],
        "events": [],
        "industryCycleReviews": [
            {"industry": f"EXTRA_{i}", "sourceIds": [], "riskChecks": checks()}
            for i in range(11)
        ],
    }
    with pytest.raises(ResearchValidationError) as failure:
        validate_report(raw, packet, {}, completed_at=NOW)
    assert failure.value.rule_id == "INDUSTRY_REVIEW_BUDGET_EXCEEDED"


def test_exact_plan_industry_precedes_news_and_is_projected_for_model_selection():
    packet = {
        "asOf": NOW.isoformat(),
        "discoveryActionableZones": [nav(discoverySector="NONFERROUS")],
        "quantitativeScan": {"items": [nav()]},
        "alphaPublic": {"sectorCatalysts": {f"NEWS_{i}": ["n1"] for i in range(11)}},
    }
    public = prompt_packet(packet)
    assert public["industryCycleResearch"]["candidates"][0]["industry"] == "NONFERROUS"
    plan = public["quantitativeScan"]["items"][0]
    assert plan["requiredReviewIndustryIds"] == ["NONFERROUS"]
    assert plan["industryReviewEligible"] is True


def test_outbound_review_schema_budget_keeps_legacy_python_report_compatible():
    from backend.macro.daily_research import REPORT_SCHEMA

    assert REPORT_SCHEMA["properties"]["industryCycleReviews"]["maxItems"] == 10


def test_selected_plan_missing_review_precedes_global_candidate_coverage_error():
    from test_industry_plan_binding import metals_direction, metals_packet

    packet = metals_packet("NONFERROUS")
    packet.update(
        asOf=NOW.isoformat(), directions=[], industryCycleResearch={"required": True}
    )
    raw = {
        **report(),
        "events": [],
        "valueDirections": [metals_direction(packet, reviewIndustryIds=["RARE_EARTH"])],
        "industryCycleReviews": [
            {"industry": "RARE_EARTH", "sourceIds": [], "riskChecks": checks()}
        ],
    }
    with pytest.raises(ResearchValidationError) as failure:
        validate_report(raw, packet, {}, completed_at=NOW)
    assert failure.value.rule_id == "DIRECTION_PLAN_SECTOR_UNREVIEWED"
    assert failure.value.item_index == 0
    assert failure.value.missing_industry_codes == ["NONFERROUS"]


def test_unselected_missing_candidate_still_reports_safe_industry_coverage_gap():
    packet = {
        "asOf": NOW.isoformat(),
        "directions": [],
        "industryCycleResearch": {"required": True},
        "discoveryActionableZones": [nav(discoverySector="NONFERROUS")],
    }
    raw = {**report(), "events": [], "valueDirections": [], "industryCycleReviews": []}
    with pytest.raises(ResearchValidationError) as failure:
        validate_report(raw, packet, {}, completed_at=NOW)
    assert failure.value.rule_id == "INDUSTRY_REVIEW_COVERAGE_MISSING"
    assert failure.value.item_index is None
    assert failure.value.missing_industry_codes == ["NONFERROUS"]


def test_replacing_a_required_candidate_with_an_outside_plan_does_not_evade_budget():
    from test_industry_cycle import cycle
    from test_industry_plan_binding import metals_direction, metals_packet
    from test_value_directions import NOW as PLAN_NOW

    packet = metals_packet("NONFERROUS")
    sectors = [
        "ROBOT",
        "BANK",
        "BROKER",
        "AI",
        "RARE_EARTH",
        "POWER",
        "GOLD",
        "DRUG",
        "COAL",
        "PCB",
    ]
    packet.update(
        directions=[],
        industryCycleResearch={"required": True},
        previousIndustryRiskStates=[
            {"industry": sector, "industryCycle": {"riskFlags": ["ORDER_CUTS"]}}
            for sector in sectors
        ],
    )
    raw = {
        **report(),
        "events": [],
        "valueDirections": [
            metals_direction(
                packet,
                reviewIndustryIds=["NONFERROUS"],
                industryCycle=cycle(stage="UNKNOWN", evidence=[]),
            )
        ],
        "industryCycleReviews": [
            {"industry": sector, "sourceIds": [], "riskChecks": checks()}
            for sector in [*sectors[:-1], "NONFERROUS"]
        ],
    }
    with pytest.raises(ResearchValidationError) as failure:
        validate_report(raw, packet, {}, completed_at=PLAN_NOW)
    assert failure.value.rule_id == "INDUSTRY_REVIEW_COVERAGE_MISSING"
    assert failure.value.missing_industry_codes == ["PCB"]


def test_required_false_legacy_report_does_not_acquire_new_review_obligations():
    from test_industry_plan_binding import metals_direction, metals_packet
    from test_value_directions import NOW as PLAN_NOW

    packet = metals_packet("NONFERROUS")
    packet.update(directions=[], industryCycleResearch={"required": False})
    raw = {**report(), "events": [], "valueDirections": [metals_direction(packet)]}
    actual = validate_report(raw, packet, {}, completed_at=PLAN_NOW)
    assert actual["valueDirections"][0]["plans"][0]["fundCode"] == "004432"


def test_scan_without_exact_anchor_is_researchable_but_cannot_claim_plan_eligibility():
    packet = {
        "asOf": NOW.isoformat(),
        "quantitativeScan": {
            "items": [nav(discoverySector="NONFERROUS")],
        },
    }
    public = prompt_packet(packet)
    assert public["industryCycleResearch"]["candidates"][0]["industry"] == "NONFERROUS"
    item = public["quantitativeScan"]["items"][0]
    assert item["requiredReviewIndustryIds"] == []
    assert item["industryReviewEligible"] is False


def legacy_top_level_plan_report(required):
    from test_phase2_research import NOW as PLAN_NOW, anchor, proposal

    plan = {
        **anchor(),
        "fundCode": "008281",
        "market": "FUND_CN",
        "discoverySector": "AI",
    }
    sectors = [
        "ROBOT",
        "BANK",
        "BROKER",
        "RARE_EARTH",
        "POWER",
        "GOLD",
        "DRUG",
        "COAL",
        "PCB",
        "NONFERROUS",
    ]
    packet = {
        "asOf": PLAN_NOW.isoformat(),
        "directions": [],
        "discoveryUniverse": [plan],
        "discoveryActionableZones": [plan],
        "themeActionableZones": [plan],
        "previousIndustryRiskStates": [
            {"industry": sector, "industryCycle": {"riskFlags": ["ORDER_CUTS"]}}
            for sector in sectors
        ],
    }
    if required is not None:
        packet["industryCycleResearch"] = {"required": required}
    raw = {
        **report(),
        "macroCycle": "Unverified public cycle.",
        "sources": [],
        "events": [],
        "valueDirections": [],
        "actionablePlans": [proposal()],
        "industryCycleReviews": [
            {"industry": sector, "sourceIds": [], "riskChecks": checks()}
            for sector in sectors
        ],
    }
    return packet, raw, PLAN_NOW


def test_new_contract_cannot_bypass_omitted_sector_via_legacy_top_level_plans():
    packet, raw, now = legacy_top_level_plan_report(True)
    assert industry_cycle_research_tasks(packet, now)["omittedPlanIndustryIds"] == [
        "AI"
    ]
    with pytest.raises(ResearchValidationError) as failure:
        validate_report(raw, packet, {}, completed_at=now)
    assert failure.value.reason_code == "FUND_PLAN_INVALID"
    assert failure.value.rule_id is None


@pytest.mark.parametrize("required", [False, None])
def test_legacy_top_level_plan_remains_readable_without_new_contract(required):
    packet, raw, now = legacy_top_level_plan_report(required)
    actual = validate_report(raw, packet, {}, completed_at=now)
    assert actual["actionablePlans"][0]["fundCode"] == "008281"


def test_outbound_schema_closes_legacy_plan_entry_without_changing_python_storage_schema():
    from backend.macro.daily_research import REPORT_SCHEMA, _Report

    assert REPORT_SCHEMA["properties"]["actionablePlans"]["maxItems"] == 0
    assert (
        _Report.model_json_schema()["properties"]["actionablePlans"]["maxItems"] == 24
    )
