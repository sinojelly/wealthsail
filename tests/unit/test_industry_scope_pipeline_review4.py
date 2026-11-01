"""Bind industry scope at both report and direction validation boundaries."""

from copy import deepcopy

import pytest

from backend.macro import daily_research
from backend.macro.industry_cycle import normalize_industry_cycle
from backend.macro.value_directions import validate_value_directions
from test_daily_research import NOW, checks as source_checks, report
from test_industry_cycle import cycle
from test_value_directions import direction, packet


def risk_cycle():
    raw = cycle(stage="UNKNOWN", sourceIds=["s1"], evidence=[])
    raw["riskChecks"][0].update(status="FOUND", sourceIds=["s1"])
    return raw


def proof(quote, title="产业经营披露"):
    return {
        "verification": "SOURCE_CHECKED",
        "publishedDate": "2026-10-15",
        "quote": quote,
        "title": title,
    }


@pytest.mark.parametrize(
    "industry,verified", [("半导体", False), ("具身智能与人形机器人", True)]
)
def test_report_review_uses_its_own_industry_as_scope(industry, verified):
    raw = {
        **report(),
        "events": [],
        "industryCycleReviews": [
            {
                "industry": "ROBOT",
                "sourceIds": ["s1"],
                "riskChecks": risk_cycle()["riskChecks"],
            }
        ],
    }
    raw["sources"][0].update(
        title="产业经营披露", quote=f"2026年第三季度{industry}行业融资笔数下降30%。"
    )
    checked = source_checks()
    checked["s1"]["title"] = raw["sources"][0]["title"]
    result = daily_research.validate_report(
        raw, {"asOf": NOW.isoformat(), "directions": []}, checked, completed_at=NOW
    )
    actual = result["industryCycleReviews"][0]
    assert actual["riskChecks"][0]["industryScopeVerified"] is verified
    assert actual["riskLevel"] == ("CONFIRMED" if verified else "SUSPECTED")
    assert actual["leftDiscountScale"] == (1.0 if verified else 0.5)


@pytest.mark.parametrize(
    "scope_source", ["review", "anchor", "scan", "universe", "title"]
)
def test_direction_scope_uses_linked_review_or_server_product_sector_or_title(
    scope_source,
):
    public = packet()
    raw = direction(title="价值观察", industryCycle=risk_cycle())
    reviews = []
    if scope_source == "review":
        raw["reviewIndustryIds"] = ["ROBOT"]
        reviews = [{"industry": "ROBOT", **normalize_industry_cycle(None)}]
    elif scope_source == "anchor":
        public["discoveryActionableZones"][0]["discoverySector"] = "ROBOT"
    elif scope_source == "scan":
        public["quantitativeScan"] = {
            "items": [
                {
                    "subjectKind": "nav",
                    "subjectId": "005112",
                    "discoverySector": "ROBOT",
                }
            ]
        }
    elif scope_source == "universe":
        public["discoveryUniverse"] = [
            {"fundCode": "005112", "discoverySector": "ROBOT"}
        ]
    else:
        raw["title"] = "机器人产业链价值观察"
    actual = validate_value_directions(
        [raw],
        public,
        NOW,
        {"s1": "source:one"},
        sources={"source:one": proof("2026年第三季度半导体行业融资笔数下降30%。")},
        industry_reviews=reviews,
    )[0]
    check = actual["industryCycle"]["riskChecks"][0]
    assert check["industryScopeVerified"] is False
    assert check["strength"] == "SUSPECTED"
    assert actual["industryCycle"]["leftDiscountScale"] == 0.5


def test_model_title_cannot_broaden_a_known_server_sector():
    public = packet()
    public["discoveryActionableZones"][0]["discoverySector"] = "ROBOT"
    raw = direction(title="半导体配置机会", industryCycle=risk_cycle())
    before = deepcopy(public)
    actual = validate_value_directions(
        [raw],
        public,
        NOW,
        {"s1": "source:one"},
        sources={"source:one": proof("近期半导体行业融资笔数下降30%。")},
    )[0]
    assert actual["industryCycle"]["riskChecks"][0]["industryScopeVerified"] is False
    assert actual["industryCycle"]["riskLevel"] == "SUSPECTED"
    assert public == before


def test_public_projection_keeps_scope_and_structure_without_certifying_search_execution():
    actual = daily_research._public_industry_cycle(
        {
            "auditStructureStatus": "COMPLETE",
            "researchStatus": "COMPLETE",
            "searchExecutionVerified": False,
            "riskChecks": [
                {"kind": "FINANCING_COOLING", "industryScopeVerified": False}
            ],
        }
    )
    assert actual["auditStructureStatus"] == "COMPLETE"
    assert actual["searchExecutionVerified"] is False
    assert actual["riskChecks"][0]["industryScopeVerified"] is False
