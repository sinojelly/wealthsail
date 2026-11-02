"""Ranking preserves the per-fund risk boundary in every public plan copy."""

from copy import deepcopy

from backend.indicators.actionable_zones import apply_industry_cycle
from backend.investment.opportunity_scoring import rank_report
from backend.macro.industry_cycle import merge_industry_cycles, normalize_industry_cycle
from test_daily_research import NOW
from test_industry_cycle_priority import real_anchor
from test_industry_plan_binding import review


def fixture():
    industrial = {**real_anchor("004432"), "discoverySector": "NONFERROUS"}
    rare = {**real_anchor("011035"), "discoverySector": "RARE_EARTH"}
    industrial_cycle = normalize_industry_cycle(None)
    rare_cycle = review("RARE_EARTH", risk=True)
    own = [
        apply_industry_cycle(industrial, industrial_cycle),
        apply_industry_cycle(rare, rare_cycle),
    ]
    packet = {
        "asOf": NOW.isoformat(),
        "discoveryActionableZones": deepcopy([industrial, rare]),
        "discoveryUniverse": deepcopy([industrial, rare]),
    }
    report = {
        "asOf": NOW.isoformat(),
        "industryCycleReviews": [review("NONFERROUS"), rare_cycle],
        "valueDirections": [
            {
                "key": "metals",
                "industryCycle": merge_industry_cycles(industrial_cycle, rare_cycle),
                "plans": own,
                "quantitativeEvidence": deepcopy(own),
            }
        ],
        "quantitativeScan": {"items": deepcopy([industrial, rare])},
        "actionablePlans": deepcopy([industrial, rare]),
    }
    return report, packet, industrial, rare


def copies(result):
    return [
        result["valueDirections"][0]["plans"],
        result["valueDirections"][0]["quantitativeEvidence"],
        result["quantitativeScan"]["items"],
        result["actionablePlans"],
    ]


def test_rank_preserves_own_cycle_in_all_copies_despite_risky_direction_summary():
    report, packet, industrial, rare = fixture()
    result = rank_report(report, packet)
    assert result["valueDirections"][0]["industryCycle"]["riskLevel"] == "CONFIRMED"
    for rows in copies(result):
        ordinary, protected = rows
        assert ordinary["industryCycle"]["riskFlags"] == []
        assert ordinary["scoreBreakdown"]["industryCyclePenalty"] == 0
        assert (
            ordinary["tracks"][0]["entryZoneMax"]
            == industrial["tracks"][0]["entryZoneMax"]
        )
        assert protected["industryCycle"]["confirmedRiskFlags"] == ["FINANCING_COOLING"]
        assert float(protected["tracks"][0]["entryZoneMax"]) < float(
            rare["tracks"][0]["entryZoneMax"]
        )
    assert rank_report(result, packet) == result


def test_rank_prefers_server_anchor_sector_to_conflicting_scanner_sector():
    report, packet, industrial, _ = fixture()
    report["quantitativeScan"]["items"][0]["discoverySector"] = "RARE_EARTH"
    result = rank_report(report, packet)
    ordinary = result["quantitativeScan"]["items"][0]
    assert ordinary["industryCycle"]["riskFlags"] == []
    assert (
        ordinary["tracks"][0]["entryZoneMax"] == industrial["tracks"][0]["entryZoneMax"]
    )


def test_scanner_sector_review_also_uses_anchor_when_direction_has_no_plan():
    report, packet, industrial, _ = fixture()
    report["valueDirections"] = []
    report["quantitativeScan"]["items"][0]["discoverySector"] = "RARE_EARTH"
    result = rank_report(report, packet)
    ordinary = result["quantitativeScan"]["items"][0]
    assert ordinary["industryCycle"]["riskFlags"] == []
    assert (
        ordinary["tracks"][0]["entryZoneMax"] == industrial["tracks"][0]["entryZoneMax"]
    )
