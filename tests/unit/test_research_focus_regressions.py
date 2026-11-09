"""Research observations retain their products and current price coordinates."""

from copy import deepcopy
from decimal import Decimal

import pytest

from backend.macro.research_delivery import _observations
from backend.macro.research_opportunities import project_topic_recommendations
from tests.unit.test_live_fund_plans_v6 import LocalHistory, history
from tests.unit.test_opportunity_fund_prices import product
from tests.unit.test_topic_fund_recommendations import NOW, fund, source, topic


def test_observation_delivery_preserves_resolved_narrative_mentions_without_authority():
    accepted = {
        "valueDirections": [
            {"key": f"watch_{i}", "thesis": "观察研究", "sourceIds": []}
            for i in range(10)
        ],
        "mentionedFunds": [
            {
                "fundCode": "008298", "subjectKind": "nav", "subjectId": "008298",
                "market": "FUND_CN", "label": "黄金观察", "directionKeys": ["watch_0"],
                "mentionLocations": ["summary"], "qualificationStatus": "FORMAL",
                "investmentEligible": True, "deploymentToken": "private",
            },
            {"fundCode": "090909", "subjectKind": "nav", "subjectId": "999999"},
        ],
    }
    result, _ = _observations(accepted, NOW)
    assert len(result["valueDirections"]) == 10
    assert len(result["mentionedFunds"]) == 1
    mention = result["mentionedFunds"][0]
    assert mention["fundCode"] == "008298"
    assert mention["directionKeys"] == ["watch_0"]
    assert mention["investmentEligible"] is False
    assert mention["qualificationStatus"] == "OBSERVATION_ONLY"
    assert "deploymentToken" not in mention


@pytest.mark.asyncio
async def test_every_candidate_has_current_full_actionable_zone_contract():
    from backend.investment.opportunity_fund_prices import enrich_fund_price_positions
    from tests.unit.test_live_fund_plans_v6 import NOW as PRICE_NOW

    result = await enrich_fund_price_positions(
        LocalHistory([history("987651")]),
        {"topic:new": {"funds": [product()], "indirectFunds": []}}, {}, PRICE_NOW,
    )
    candidate = result["topic:new"]["funds"][0]
    zones = candidate["actionableZones"]
    assert zones["fundCode"] == candidate["fundCode"]
    assert zones["currentPrice"] == candidate["confirmedNav"]["value"]
    assert zones["asOf"] == candidate["confirmedNav"]["asOf"]
    assert zones["priceRefresh"]["calculatedAt"] == PRICE_NOW.isoformat()
    assert zones["tracks"] == candidate["referenceTracks"]
    assert zones["buyZone"]["min"] is not None
    assert zones["sellZone"]["max"] is not None
    assert zones["stopLoss"] is not None and zones["targets"]
    assert zones["series"]["points"][-1]["date"] == zones["asOf"]
    assert Decimal(zones["series"]["points"][-1]["value"]) == Decimal(zones["currentPrice"])


@pytest.mark.asyncio
async def test_missing_history_keeps_fund_and_explicit_empty_price_contract():
    from backend.investment.opportunity_fund_prices import enrich_fund_price_positions
    from tests.unit.test_live_fund_plans_v6 import NOW as PRICE_NOW

    result = await enrich_fund_price_positions(
        LocalHistory([]), {"topic:new": {"funds": [product()]}}, {}, PRICE_NOW,
    )
    candidate = result["topic:new"]["funds"][0]
    assert candidate["actionableZones"]["historyQuality"] == "UNAVAILABLE"
    assert candidate["actionableZones"]["buyZone"] == {"min": None, "max": None}
    assert candidate["pricePosition"]["status"] == "DATA_PENDING"
    assert candidate["quantitativeScore"] is None


def test_focus_sorts_all_twelve_topics_by_current_fund_price_state():
    states = ["OVERHEATED", "NEUTRAL", "NEAR_ENTRY_ZONE", "ENTRY_ZONE"]
    topics, recalls = [], {}
    for i in range(12):
        row = topic(f"focus_{i}")
        topics.append(row)
        recalls[row["topicId"]] = {"funds": [{
            **fund(90 if i % 4 == 1 else 30), "fundCode": f"{i:06}",
            "pricePosition": {"status": states[i % 4]},
        }]}
    original = deepcopy(recalls)
    items = project_topic_recommendations(
        topics, recalls, sources=[source()], reviews=[], coverage={}, packet={},
        validated_at=NOW, now=NOW,
    )
    assert len(items) == 12
    assert [item["key"] for item in items] == [
        f"focus_{i}" for i in [3, 7, 11, 2, 6, 10, 1, 5, 9, 0, 4, 8]
    ]
    assert recalls == original


def test_funds_within_one_topic_are_sorted_before_the_display_limit():
    row = topic()
    funds = [{**fund(90), "fundCode": f"{i:06}",
              "pricePosition": {"status": "OVERHEATED"}} for i in range(3)]
    funds.append({**fund(30), "fundCode": "888888",
                  "pricePosition": {"status": "ENTRY_ZONE"}})
    items = project_topic_recommendations(
        [row], {row["topicId"]: {"funds": funds}}, sources=[source()], reviews=[],
        coverage={}, packet={}, validated_at=NOW, now=NOW,
    )
    assert items[0]["recommendedFunds"][0]["fundCode"] == "888888"


def test_near_entry_position_reports_real_distance_without_a_buy_grant():
    from backend.investment.opportunity_fund_prices import price_position
    from tests.unit.test_live_fund_plans_v6 import NOW as PRICE_NOW

    position = price_position({
        "historyQuality": "READY", "asOf": "2026-10-27", "currentPrice": "1.02",
        "priceRefresh": {"state": "READY"},
        "tracks": [{"priceState": "READY", "entryZoneMin": "0.95",
                    "entryZoneMax": "1.00", "takeProfitT1": "1.2"}],
    }, PRICE_NOW)
    assert position["status"] == "NEAR_ENTRY_ZONE"
    assert 1.9 < position["distanceToEntryPct"] < 2.0
    assert position["actionable"] is False


@pytest.mark.asyncio
async def test_body_fund_code_recalls_verified_master_identity_outside_frozen_pool():
    from backend.investment.opportunity_fund_recall import recall_topic_funds
    from tests.unit.test_opportunity_fund_recall import Connection, product, status, topic, NOW

    connection = Connection([product("981002", "测试成长精选A", "")], [status("981002")])
    result = await recall_topic_funds(
        connection, [topic(summary="正文提及981002，观察企业订单增长。")], {}, NOW,
    )
    candidate = result["topic:chip"]["funds"][0]
    assert candidate["fundCode"] == "981002"
    assert "正文提及" in candidate["matchReason"]
    assert "981002" in connection.calls[0][1][2]


def test_events_and_coverage_body_mentions_keep_their_topic_links():
    from backend.macro.research_mentions import extract_mentioned_funds
    from tests.unit.test_research_mentions import packet

    result = extract_mentioned_funds({
        "coverage": [{"summary": "保留008298观察。"}],
        "events": [{"summary": "007466属于商品观察。", "relatedTopicKeys": ["soy"]}],
    }, packet())
    assert {row["fundCode"] for row in result} == {"008298", "007466"}
    soy = next(row for row in result if row["fundCode"] == "007466")
    assert soy["topicKeys"] == ["soy"]


def test_acceptance_receipt_keeps_resolved_mentions_before_read_projection():
    from backend.macro.research_acceptance import validate_report_outcome
    from tests.unit.test_research_autodowngrade_audit import public_failure, RUN

    value, at = public_failure()
    value["raw"]["macroCycle"] += " 018344属于观察。"
    outcome = validate_report_outcome(
        value["raw"], value["packet"], value["sourceChecks"],
        research_id=RUN, completed_at=at,
    )
    assert any(row["fundCode"] == "018344" for row in outcome.accepted["mentionedFunds"])
