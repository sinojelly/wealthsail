"""Research observations retain their products and current price coordinates."""

from copy import deepcopy
from decimal import Decimal
from datetime import timedelta
from unittest.mock import AsyncMock

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
    assert candidate["confirmedNav"]["changePct"] == zones["currentChangePct"]
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


@pytest.mark.asyncio
async def test_identity_conflict_exposes_no_partial_actionable_zone_object():
    from backend.investment.opportunity_fund_prices import enrich_fund_price_positions
    from tests.unit.test_live_fund_plans_v6 import NOW as PRICE_NOW
    from uuid import uuid4

    wrong = {**product(), "subjectKind": "fund", "subjectId": str(uuid4())}
    result = await enrich_fund_price_positions(
        LocalHistory([history("987651")]),
        {"valid": {"funds": [product()]}, "conflict": {"funds": [wrong]}}, {}, PRICE_NOW,
    )
    candidate = result["conflict"]["funds"][0]
    assert candidate["actionableZones"] is None
    assert candidate["pricePosition"]["status"] == "DATA_PENDING"


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


def test_current_high_scores_sort_before_narrative_source_count():
    high, backed = topic("score_95"), topic("score_75", sourceIds=["s1", "s2"])
    items = project_topic_recommendations(
        [backed, high],
        {row["topicId"]: {"funds": [{**fund(score), "pricePosition": {"status": "NEUTRAL"}}]}
         for row, score in [(high, 95), (backed, 75)]},
        sources=[source(), {**source(), "id": "s2"}], reviews=[], coverage={}, packet={},
        validated_at=NOW, now=NOW,
    )
    assert [item["key"] for item in items] == ["score_95", "score_75"]


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


@pytest.mark.asyncio
async def test_actor_recommendation_repairs_nav_before_current_plan_calculation(monkeypatch):
    from backend.investment.opportunity_fund_prices import enrich_fund_price_positions
    from tests.unit.test_live_fund_plans_v6 import NOW as PRICE_NOW

    synced_at = PRICE_NOW + timedelta(seconds=2)
    ensure = AsyncMock(return_value={
        "asOf": synced_at, "histories": [history("987651")],
        "refresh": {"987651": {"state": "READY"}}, "latestConfirmed": {},
    })
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", ensure)
    result = await enrich_fund_price_positions(
        conn := LocalHistory([]), {"topic:new": {"funds": [product()]}}, {}, PRICE_NOW,
        tenant_id="tenant", user_id="actor",
    )
    ensure.assert_awaited_once_with(
        conn, ["987651"], PRICE_NOW, tenant_id="tenant", user_id="actor", provider_budget=1,
    )
    candidate = result["topic:new"]["funds"][0]
    assert candidate["actionableZones"]["priceRefresh"]["calculatedAt"] == synced_at.isoformat()
    assert candidate["actionableZones"]["navSync"] == {"state": "READY"}
    assert candidate["confirmedNav"]["value"] is not None


@pytest.mark.asyncio
async def test_all_displayed_funds_are_synced_even_when_more_than_thirty_two(monkeypatch):
    from backend.investment.opportunity_fund_prices import enrich_fund_price_positions
    from tests.unit.test_live_fund_plans_v6 import NOW as PRICE_NOW

    codes = [f"{981000 + index:06}" for index in range(36)]
    ensure = AsyncMock(return_value={"asOf": PRICE_NOW, "histories": [], "refresh": {}})
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", ensure)
    recalls = {
        f"topic:{index}": {"funds": [product(code) for code in codes[index * 3:index * 3 + 3]]}
        for index in range(12)
    }
    result = await enrich_fund_price_positions(
        LocalHistory([]), recalls, {}, PRICE_NOW, tenant_id="tenant", user_id="actor",
    )
    assert set(ensure.await_args.args[1]) == set(codes)
    assert ensure.await_args.kwargs["provider_budget"] == 36
    assert sum(len(item["funds"]) for item in result.values()) == 36


@pytest.mark.asyncio
async def test_newer_confirmed_nav_prevents_stale_history_from_appearing_ready(monkeypatch):
    from backend.investment.opportunity_fund_prices import enrich_fund_price_positions
    from tests.unit.test_live_fund_plans_v6 import NOW as PRICE_NOW

    ensure = AsyncMock(return_value={
        "asOf": PRICE_NOW, "histories": [history("987651")],
        "refresh": {"987651": {"state": "FAILED", "reason": "NAV_SOURCE_SYNC_FAILED"}},
        "latestConfirmed": {"987651": {"asOf": "2026-10-28", "quality": "FRESH"}},
    })
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", ensure)
    result = await enrich_fund_price_positions(
        LocalHistory([]), {"topic:new": {"funds": [product()]}}, {}, PRICE_NOW,
        tenant_id="tenant", user_id="actor",
    )
    candidate = result["topic:new"]["funds"][0]
    zones = candidate["actionableZones"]
    assert zones["priceRefresh"]["state"] == "PARTIAL"
    assert zones["priceRefresh"]["reason"] == "NEWER_CONFIRMED_NAV_AVAILABLE"
    assert zones["series"]["state"] == "PARTIAL"
    assert candidate["pricePosition"]["status"] == "DATA_PENDING"
    assert candidate["quantitativeScore"] is None
    assert candidate["confirmedNav"]["asOf"] == "2026-10-27"
