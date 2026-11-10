"""AI directions use the full verified fund master, independent of audit subsets."""

from copy import deepcopy

import pytest

from tests.unit.test_opportunity_fund_recall import Connection, NOW, product, status


@pytest.mark.asyncio
async def test_unaudited_hardware_direction_gets_real_chip_fund_outside_frozen_subset():
    from backend.investment.research_fund_references import resolve_report_fund_references

    original = {"valueDirections": [{
        "key": "ai_hardware", "title": "AI硬件", "thesis": "企业订单回升",
        "reviewIndustryIds": ["SEMICONDUCTOR_AI", "MEMORY"],
        "auditStatus": "UNAUDITED_SCOPE", "qualificationStatus": "OBSERVATION_ONLY", "plans": [],
    }]}
    saved = deepcopy(original)
    connection = Connection([product()], [status()])
    result = await resolve_report_fund_references(connection, original, {}, NOW)
    direction = result["valueDirections"][0]
    assert direction["plans"][0]["fundCode"] == "981001"
    assert direction["plans"][0]["subjectId"] == "981001"
    assert direction["auditStatus"] == "UNAUDITED_SCOPE"
    assert direction["plans"][0]["auditStatus"] == "UNAUDITED_SCOPE"
    assert direction["plans"][0]["currentPrice"] is None
    assert "芯片" in connection.calls[0][1][1]
    assert original == saved


@pytest.mark.asyncio
async def test_every_narrative_code_resolves_outside_frozen_pool_with_direction_links():
    from backend.investment.research_fund_references import resolve_report_fund_references

    codes = ["981001", "981002", "981003", "981004"]
    rows = [product(code, f"测试成长{code}精选A", "") for code in codes]
    original = {"valueDirections": [{
        "key": "new_orders", "title": "订单增长", "thesis": "观察" + "、".join(codes), "plans": [],
    }]}
    result = await resolve_report_fund_references(
        Connection(rows, [status(code) for code in codes]), original, {}, NOW,
    )
    assert {row["fundCode"] for row in result["mentionedFunds"]} == set(codes)
    assert {row["fundCode"] for row in result["valueDirections"][0]["plans"]} == set(codes)
    assert all(row["directionKeys"] == ["new_orders"] for row in result["mentionedFunds"])


@pytest.mark.asyncio
async def test_existing_valid_plan_is_retained_without_unnecessary_catalog_search():
    from backend.investment.research_fund_references import resolve_report_fund_references
    from tests.unit.test_live_fund_plans_v6 import old_plan

    original = {"valueDirections": [{"key": "existing", "plans": [old_plan()]}]}
    connection = Connection()
    assert await resolve_report_fund_references(connection, original, {}, NOW) == original
    assert connection.calls == []


@pytest.mark.asyncio
async def test_all_twelve_topics_match_funds_and_compatibility_catalog_is_not_queried_twice():
    from backend.investment.research_fund_references import resolve_report_fund_references

    topics = [{
        "key": f"hardware_{i}", "title": f"存储芯片方向{i}", "summary": "新增订单增长",
        "match": {"industryIds": ["MEMORY"]},
    } for i in range(12)]
    connection = Connection([product()], [status()])
    result = await resolve_report_fund_references(connection, {
        "researchTopics": topics, "topicCatalog": deepcopy(topics),
    }, {}, NOW)
    assert len(result["researchTopics"]) == 12
    assert all(row["plans"][0]["fundCode"] == "981001" for row in result["researchTopics"])
    assert result["researchTopics"] == result["topicCatalog"]
    assert len(connection.calls) == 3


@pytest.mark.asyncio
async def test_sanitized_topic_related_industry_ids_match_global_fund_catalog():
    from backend.investment.research_fund_references import resolve_report_fund_references

    result = await resolve_report_fund_references(Connection([product()], [status()]), {
        "researchTopics": [{
            "key": "industrial_orders", "title": "产业订单增长", "summary": "企业利润改善",
            "relatedIndustryIds": ["SEMICONDUCTOR_AI"],
        }],
    }, {}, NOW)
    assert result["researchTopics"][0]["plans"][0]["fundCode"] == "981001"
