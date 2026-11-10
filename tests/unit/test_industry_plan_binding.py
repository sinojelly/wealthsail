"""Industry facts bind to the server product, not a direction's other themes."""

from copy import deepcopy

import pytest

from backend.macro.industry_cycle import normalize_industry_cycle
from backend.macro.value_directions import validate_value_directions
from test_industry_cycle import cycle
from test_industry_scope_review4 import normalize, proof
from test_value_directions import NOW, direction, packet


@pytest.mark.parametrize(
    "sector,quote,verified",
    [
        ("NONFERROUS", "近期铜铝等工业金属行业融资规模下降30%。", True),
        ("NONFERROUS", "近期有色金属行业融资规模下降30%。", True),
        ("NONFERROUS", "近期稀土行业融资规模下降30%。", False),
        ("RARE_EARTH_METALS", "近期铜铝等工业金属行业融资规模下降30%。", True),
        ("RARE_EARTH_METALS", "近期稀土行业融资规模下降30%。", True),
        ("RARE_EARTH", "近期稀土永磁行业融资规模下降30%。", True),
        ("RARE_EARTH", "近期有色金属行业融资规模下降30%。", False),
        ("RARE_EARTH", "近期nonferrous industry financing declined 30%。", False),
        ("RARE_EARTH", "近期铜铝等工业金属行业融资规模下降30%。", False),
    ],
)
def test_metals_scope_distinguishes_rare_earth_from_industrial_metals(
    sector, quote, verified
):
    result = normalize(proof(quote), expected=[sector])
    assert result["riskChecks"][0]["industryScopeVerified"] is verified
    assert result["riskLevel"] == ("CONFIRMED" if verified else "SUSPECTED")


def metals_packet(*sectors):
    public = packet()
    original = public["discoveryActionableZones"][0]
    codes = {"NONFERROUS": "004432", "RARE_EARTH": "011035"}
    public["discoveryActionableZones"] = [
        {
            **deepcopy(original),
            "subjectId": codes[sector],
            "fundCode": codes[sector],
            "market": "FUND_CN",
            "discoverySector": sector,
        }
        for sector in sectors
    ]
    public["discoveryUniverse"] = deepcopy(public["discoveryActionableZones"])
    return public


def metals_direction(public, **updates):
    raw = direction(signal="WATCH", **updates)
    template = raw["plans"][0]
    raw["plans"] = [
        {
            **deepcopy(template),
            "subjectKind": row["subjectKind"],
            "subjectId": row["subjectId"],
        }
        for row in public["discoveryActionableZones"]
    ]
    return raw


def review(sector, *, risk=False):
    if not risk:
        return {"industry": sector, **normalize_industry_cycle(None)}
    quote = "近期稀土行业融资规模下降30%。"
    return {"industry": sector, **normalize(proof(quote), expected=[sector])}


def validate(raw, public, reviews):
    return validate_value_directions(
        [raw], public, NOW, {"s1": "source:one"}, industry_reviews=reviews
    )[0]


def test_direction_summary_risk_does_not_spread_to_its_other_fund():
    public = metals_packet("NONFERROUS", "RARE_EARTH")
    actual = validate(
        metals_direction(public, reviewIndustryIds=["NONFERROUS", "RARE_EARTH"]),
        public,
        [review("NONFERROUS"), review("RARE_EARTH", risk=True)],
    )
    assert actual["industryCycle"]["riskLevel"] == "CONFIRMED"
    industrial, rare = actual["plans"]
    assert industrial["industryCycle"]["riskFlags"] == []
    assert industrial["industryCycle"]["leftDiscountScale"] == 0
    assert rare["industryCycle"]["confirmedRiskFlags"] == ["FINANCING_COOLING"]


def test_unrelated_direction_fact_cannot_create_even_half_discount_on_fund():
    public = metals_packet("NONFERROUS")
    raw_cycle = cycle(stage="UNKNOWN", evidence=[])
    raw_cycle["riskChecks"][0].update(status="FOUND", sourceIds=["s1"])
    actual = validate_value_directions(
        [metals_direction(public, industryCycle=raw_cycle)],
        public,
        NOW,
        {"s1": "source:one"},
        sources={"source:one": proof("近期稀土行业融资规模下降30%。")},
        industry_reviews=[review("NONFERROUS")],
    )[0]
    assert actual["plans"][0]["industryCycle"]["riskFlags"] == []


def test_verified_matching_direction_fact_still_protects_its_fund():
    public = metals_packet("NONFERROUS")
    raw_cycle = cycle(stage="UNKNOWN", evidence=[])
    raw_cycle["riskChecks"][0].update(status="FOUND", sourceIds=["s1"])
    actual = validate_value_directions(
        [metals_direction(public, industryCycle=raw_cycle)],
        public,
        NOW,
        {"s1": "source:one"},
        sources={"source:one": proof("近期铜铝等工业金属行业融资规模下降30%。")},
        industry_reviews=[review("NONFERROUS")],
    )[0]
    assert actual["plans"][0]["industryCycle"]["riskLevel"] == "CONFIRMED"


def test_matching_unchecked_direction_fact_stays_suspected_for_its_fund():
    public = metals_packet("NONFERROUS")
    raw_cycle = cycle(stage="UNKNOWN", evidence=[])
    raw_cycle["riskChecks"][0].update(status="FOUND", sourceIds=["s1"])
    actual = validate_value_directions(
        [metals_direction(public, industryCycle=raw_cycle)],
        public,
        NOW,
        {"s1": "source:one"},
        sources={
            "source:one": proof(
                "近期铜铝等工业金属行业融资规模下降30%。", checked=False
            )
        },
        industry_reviews=[review("NONFERROUS")],
    )[0]
    own = actual["plans"][0]["industryCycle"]
    assert own["riskLevel"] == "SUSPECTED"
    assert own["leftDiscountScale"] == 0.5


def test_historical_direction_key_risk_cannot_spread_to_another_server_sector():
    public = metals_packet("NONFERROUS")
    raw = metals_direction(
        public,
        reviewIndustryIds=["NONFERROUS"],
        industryCycle=cycle(stage="UNKNOWN", evidence=[]),
    )
    actual = validate_value_directions(
        [raw],
        public,
        NOW,
        {"s1": "source:one"},
        industry_reviews=[review("NONFERROUS")],
        historical_cycles={
            ("key", raw["key"]): {
                "industryCycle": review("RARE_EARTH", risk=True),
                "researchedAt": "2026-10-16T08:35:00+08:00",
            }
        },
    )[0]
    assert "PERSISTED_RISK_WATCH" in actual["industryCycle"]["riskFlags"]
    assert actual["plans"][0]["industryCycle"]["riskFlags"] == []


def test_anchor_sector_wins_over_conflicting_same_code_scan_row():
    public = metals_packet("NONFERROUS")
    public["quantitativeScan"] = {
        "items": [
            {
                **deepcopy(public["discoveryActionableZones"][0]),
                "discoverySector": "RARE_EARTH",
            }
        ]
    }
    actual = validate(metals_direction(public), public, [review("NONFERROUS")])
    assert actual["reviewIndustryIds"] == ["NONFERROUS"]
    assert actual["plans"][0]["industryCycle"]["riskFlags"] == []


def test_declared_unknown_review_keeps_original_and_maps_observation_to_broad():
    public = metals_packet("NONFERROUS")
    actual = validate(
            metals_direction(public, reviewIndustryIds=["model-invented-sector"]),
            public,
            [review("NONFERROUS")],
        )
    assert actual["plans"]
    assert "BROAD" in actual["reviewIndustryIds"]
    assert actual["originalIndustryIds"] == ["model-invented-sector"]


def test_unreviewed_server_sectors_are_attached_without_deleting_fund_plans():
    public = metals_packet("NONFERROUS", "RARE_EARTH")
    rows = [
        direction(key="first", plans=[], signal="WATCH", reviewIndustryIds=["ROBOT"]),
        metals_direction(public, key="second"),
    ]
    actual = validate_value_directions(
            rows, public, NOW, {"s1": "source:one"}, industry_reviews=[review("ROBOT")]
        )
    assert actual[1]["reviewIndustryIds"] == ["NONFERROUS", "RARE_EARTH"]
    assert actual[1]["plans"]
    assert actual[1]["auditStatus"] == "UNAUDITED_SCOPE"


def test_fund_without_server_industry_keeps_plan_without_borrowing_robot_risk():
    public = packet()
    public["industryCycleResearch"] = {"required": True}
    raw = direction(
        industryCycle=cycle(stage="UNKNOWN", sourceIds=[], evidence=[]),
        reviewIndustryIds=["ROBOT"],
    )
    actual = validate(raw, public, [review("ROBOT")])
    assert actual["plans"]
    assert actual["plans"][0]["industryCycle"]["riskFlags"] == []
    assert actual["auditStatus"] == "UNAUDITED_SCOPE"


def test_required_broad_index_does_not_need_a_fund_industry_identity():
    public = packet()
    public["industryCycleResearch"] = {"required": True}
    public["discoveryActionableZones"][0].update(
        subjectKind="index", subjectId="hs300", priceBasis="INDEX_POINTS", unit="points"
    )
    raw = direction(
        industryCycle=cycle(stage="UNKNOWN", sourceIds=[], evidence=[]),
        reviewIndustryIds=["ROBOT"],
    )
    raw["plans"][0].update(subjectKind="index", subjectId="hs300")
    actual = validate(raw, public, [review("ROBOT")])
    assert actual["plans"][0]["subjectKind"] == "index"
