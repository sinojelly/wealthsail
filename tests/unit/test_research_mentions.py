"""Narrative mentions are known fund references, never investment permission."""

from copy import deepcopy
from importlib import import_module, util


def service():
    assert util.find_spec("backend.macro.research_mentions"), (
        "Narrative fund extraction is missing"
    )
    return import_module("backend.macro.research_mentions")


def packet():
    return {
        "discoveryUniverse": [
            {
                "subjectKind": "nav",
                "subjectId": code,
                "fundCode": code,
                "label": name,
                "market": "FUND_CN",
            }
            for code, name in [
                ("008298", "黄金观察"),
                ("007466", "豆粕观察"),
                ("161129", "原油观察"),
            ]
        ]
    }


def test_resolves_overview_direction_and_topic_body_mentions_without_granting_permission():
    report = {
        "summary": "观察008298与007466。",
        "macroCycle": "161129属于卖区参考。",
        "valueDirections": [{"key": "gold_watch", "thesis": "008298暂勿追涨。"}],
        "topicCatalog": [{"title": "商品观察", "thesis": "保留007466的价格跟踪。"}],
    }
    before = deepcopy((report, packet()))
    rows = service().extract_mentioned_funds(report, packet())
    assert {row["fundCode"] for row in rows} == {"008298", "007466", "161129"}
    assert len(rows) == 3
    assert all(
        row["investmentEligible"] is False
        and row["qualificationStatus"] == "OBSERVATION_ONLY"
        for row in rows
    )
    gold = next(row for row in rows if row["fundCode"] == "008298")
    assert gold["directionKeys"] == ["gold_watch"]
    assert "summary" in gold["mentionLocations"]
    assert (report, packet()) == before


def test_ignores_urls_source_identifiers_private_fields_long_numbers_and_code_fragments():
    report = {
        "summary": "日期20261107，编号X008298Z；链接 https://example.test/007466 。",
        "sources": [{"id": "161129", "quote": "008298"}],
        "deployment": {"token": "007466"},
        "quantitativeFundCodes": ["008298"],
    }
    assert service().mentioned_fund_codes(report) == []


def test_unknown_six_digit_mentions_must_be_resolved_before_publishing_as_a_fund():
    report = {"summary": "008298与090909的观察。"}
    assert {row["fundCode"] for row in service().mentioned_fund_codes(report)} == {
        "008298",
        "090909",
    }
    assert [
        row["fundCode"] for row in service().extract_mentioned_funds(report, packet())
    ] == ["008298"]


def test_ambiguous_fund_code_cannot_select_an_arbitrary_instrument_identity():
    value = packet()
    value["discoveryUniverse"].extend(
        [
            {
                "subjectKind": "fund",
                "subjectId": "10000000-0000-4000-8000-000000000001",
                "fundCode": "008298",
                "label": "冲突身份",
                "market": "FUND_CN",
            },
            {
                "subjectKind": "fund",
                "subjectId": "10000000-0000-4000-8000-000000000002",
                "fundCode": "008298",
                "label": "另一身份",
                "market": "FUND_CN",
            },
        ]
    )
    assert service().extract_mentioned_funds({"summary": "008298"}, value) == []


def test_historical_inventory_retains_narrative_mentions_without_creating_formal_plans():
    from backend.macro.research_preview import _inventory

    funds, _, _ = _inventory({"summary": "008298、007466、161129"}, packet())
    assert {row["fundCode"] for row in funds} == {"008298", "007466", "161129"}
    assert all(not row["originalProposals"] for row in funds)


def test_validated_report_exposes_known_narrative_funds_even_after_formal_plan_downgrade():
    from backend.macro.daily_research import validate_report
    from tests.unit.test_research_autodowngrade_audit import public_failure, RUN

    value, at = public_failure()
    value["raw"]["macroCycle"] += " 018344属于观察。"
    result = validate_report(
        value["raw"],
        value["packet"],
        value["sourceChecks"],
        completed_at=at,
        research_id=RUN,
    )
    assert any(row["fundCode"] == "018344" for row in result["mentionedFunds"])
    assert all(row["investmentEligible"] is False for row in result["mentionedFunds"])


def test_observation_projection_does_not_truncate_new_ten_direction_contract_or_metadata():
    from backend.macro.research_acceptance_repository import _accepted

    result = _accepted(
        {
            "valueDirections": [
                {"key": f"watch_{i}", "thesis": "历史观察"} for i in range(10)
            ],
            "sources": [
                {"id": "s1", "frozenTitleMatched": True, "frozenQuoteMatched": False}
            ],
            "events": [
                {
                    "id": "e1",
                    "category": "TECHNOLOGY",
                    "sourcePublishedAt": "2026-11-07",
                    "sourcePublicationBasis": "SOURCE_PUBLICATION",
                }
            ],
        }
    )
    assert len(result["valueDirections"]) == 10
    assert result["sources"][0]["frozenTitleMatched"] is True
    assert result["sources"][0]["frozenQuoteMatched"] is False
    assert result["events"][0]["category"] == "TECHNOLOGY"
    assert result["events"][0]["sourcePublishedAt"] == "2026-11-07"


def test_historical_nav_chart_rejects_currency_or_price_basis_conflicts():
    from backend.macro.research_preview import _analysis
    from tests.unit.test_research_preview import case, history
    from backend.macro.research_frozen_bundle import decode_frozen_bundle
    from datetime import timedelta

    _, bundle, _, at = case()
    frozen = decode_frozen_bundle(
        bundle, bundle["researchId"], observed_at=at + timedelta(days=1)
    )
    anchor = frozen.packet["discoveryActionableZones"][0]
    valid = history(anchor, at)
    assert _analysis(valid, anchor, at) is not None
    for conflict in (
        {"currency": "USD"},
        {"unit": "USD/share"},
        {"fundName": "美元份额"},
    ):
        assert _analysis({**valid, **conflict}, anchor, at) is None
    for conflict in (
        {"priceBasis": "INDEX_POINT"},
        {"currency": "USD"},
        {"market": "SSE"},
    ):
        assert _analysis(valid, {**anchor, **conflict}, at) is None
