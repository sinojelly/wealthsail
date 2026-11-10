"""Research insights retain current real NAV coordinates regardless of audit status."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from backend.investment.live_fund_plans import refresh_report_fund_plans
from tests.unit.test_live_fund_plans_v6 import LocalHistory, NOW, history, old_plan


@pytest.mark.asyncio
async def test_observation_direction_and_body_mention_get_real_price_plans_and_charts():
    report = {
        "valueDirections": [{
            "key": "hardware", "qualificationStatus": "OBSERVATION_ONLY",
            "auditStatus": "UNAUDITED_SCOPE", "plans": [old_plan()],
            "fundPlans": [old_plan("007466")],
        }],
        "mentionedFunds": [{
            "fundCode": "011035", "subjectKind": "nav", "subjectId": "011035",
            "market": "FUND_CN", "label": "正文基金", "directionKeys": ["hardware"],
        }],
    }
    saved = deepcopy(report)
    result = await refresh_report_fund_plans(
        LocalHistory([history(code) for code in ("008298", "007466", "011035")]),
        report, NOW, include_series=True,
    )
    direction = result["valueDirections"][0]
    assert direction["auditStatus"] == "UNAUDITED_SCOPE"
    for fund in [*direction["plans"], *direction["fundPlans"], *result["mentionedFunds"]]:
        zones = fund["actionableZones"]
        assert zones["methodVersion"] == "actionable-zones-v6"
        assert zones["currentPrice"] != "99.99"
        assert zones["tracks"][0]["entryZoneMin"] is not None
        assert zones["series"]["points"][-1]["date"] == zones["asOf"]
        assert zones["buyZone"]["min"] is not None
        assert zones["sellZone"]["max"] is not None
        assert zones["stopLoss"] is not None and len(zones["targets"]) == 2
        assert fund["confirmedNav"]["value"] == zones["currentPrice"]
        assert fund["confirmedNav"]["asOf"] == zones["asOf"]
        assert zones["pricePosition"] == fund["pricePosition"]
    assert result["mentionedFunds"][0]["directionKeys"] == ["hardware"]
    assert report == saved


@pytest.mark.asyncio
async def test_authenticated_report_repairs_every_direction_mention_and_quant_fund(monkeypatch):
    prepared_at = NOW + timedelta(seconds=2)
    codes = ["008298", "007466", "011035"]
    ensure = AsyncMock(return_value={
        "asOf": prepared_at, "histories": [history(code) for code in codes],
        "refresh": {code: {"state": "READY"} for code in codes}, "latestConfirmed": {},
    })
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", ensure)
    result = await refresh_report_fund_plans(
        conn := LocalHistory(), {
            "valueDirections": [{"plans": [old_plan()], "qualificationStatus": "PENDING_REVIEW"}],
            "mentionedFunds": [{
                "fundCode": "007466", "subjectKind": "nav", "subjectId": "007466", "market": "FUND_CN",
            }],
            "quantitativeScan": {"items": [old_plan("011035")]},
        }, NOW, tenant_id="tenant", user_id="actor", include_series=True,
    )
    assert set(ensure.await_args.args[1]) == set(codes)
    assert ensure.await_args.args[0] is conn
    assert ensure.await_args.kwargs == {"tenant_id": "tenant", "user_id": "actor", "provider_budget": 3}
    for plan in [result["valueDirections"][0]["plans"][0], result["mentionedFunds"][0], result["quantitativeScan"]["items"][0]]:
        assert plan["actionableZones"]["navSync"] == {"state": "READY"}
        assert plan["actionableZones"]["priceRefresh"]["calculatedAt"] == prepared_at.isoformat()
        assert plan["actionableZones"]["series"]["points"]


@pytest.mark.asyncio
async def test_no_history_keeps_fund_reference_without_inventing_price():
    result = await refresh_report_fund_plans(LocalHistory(), {
        "mentionedFunds": [{
            "fundCode": "011035", "subjectKind": "nav", "subjectId": "011035", "market": "FUND_CN",
        }],
    }, NOW, include_series=True)
    mention = result["mentionedFunds"][0]
    assert mention["fundCode"] == "011035"
    assert mention["actionableZones"]["currentPrice"] is None
    assert mention["actionableZones"]["buyZone"] == {"min": None, "max": None}
    assert mention["actionableZones"]["series"]["points"] == []


@pytest.mark.asyncio
async def test_authenticated_failed_repair_keeps_older_actual_prices_marked_partial(monkeypatch):
    ensure = AsyncMock(return_value={
        "asOf": NOW, "histories": [history()],
        "refresh": {"008298": {"state": "FAILED"}},
        "latestConfirmed": {"008298": {"asOf": "2026-10-28", "quality": "CONFIRMED"}},
    })
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", ensure)
    result = await refresh_report_fund_plans(
        LocalHistory(), {"valueDirections": [{"plans": [old_plan()]}]}, NOW,
        tenant_id="tenant", user_id="actor", include_series=True,
    )
    plan = result["valueDirections"][0]["plans"][0]
    assert plan["priceRefresh"]["state"] == "PARTIAL"
    assert plan["priceRefresh"]["reason"] == "NEWER_CONFIRMED_NAV_AVAILABLE"
    assert plan["asOf"] == "2026-10-27"
    assert plan["actionableZones"]["buyZone"]["min"] is not None
    assert plan["actionableZones"]["series"]["points"]


@pytest.mark.asyncio
async def test_generic_topic_uses_canonical_industry_claims_to_match_real_memory_funds():
    from backend.investment.research_fund_references import resolve_report_fund_references
    from tests.unit.test_opportunity_fund_recall import Connection, NOW as MATCH_NOW, product, status

    original = {"researchTopics": [{
        "key": "industrial_orders", "title": "产业订单增长", "thesis": "企业利润改善",
        "industryClaims": ["MEMORY"], "relatedIndustryIds": ["BANK"],
    }]}
    connection = Connection([product()], [status()])
    result = await resolve_report_fund_references(connection, original, {}, MATCH_NOW)
    assert result["researchTopics"][0]["plans"][0]["fundCode"] == "981001"
    keywords = connection.calls[0][1][1]
    assert "芯片" in keywords and "银行" in keywords
    assert "plans" not in original["researchTopics"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("claims", [None, 0])
async def test_malformed_optional_claims_keep_text_matched_topic_funds(claims):
    from backend.investment.research_fund_references import resolve_report_fund_references
    from tests.unit.test_opportunity_fund_recall import Connection, NOW as MATCH_NOW, product, status

    result = await resolve_report_fund_references(Connection([product()], [status()]), {
        "researchTopics": [{"key": "memory", "title": "存储芯片", "industryClaims": claims}],
    }, {}, MATCH_NOW)
    assert result["researchTopics"][0]["plans"][0]["fundCode"] == "981001"
