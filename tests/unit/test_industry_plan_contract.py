"""Server fund identity, rather than a model label, owns industry obligations."""

import pytest

from backend.macro.industry_contract import plan_industry_ids, same_fund_identity


def nav(code="004432", **changes):
    return {
        "subjectKind": "nav",
        "subjectId": code,
        "fundCode": code,
        "market": "FUND_CN",
        **changes,
    }


@pytest.mark.parametrize(
    "other",
    [
        {"fundCode": "004432", "market": "FUND_CN"},
        nav(),
        {
            "subjectKind": "fund",
            "subjectId": "uuid-a",
            "fundCode": "004432",
            "market": "FUND_CN",
        },
    ],
)
def test_explicit_same_fund_and_catalog_code_can_associate(other):
    assert same_fund_identity(nav(), other)


@pytest.mark.parametrize(
    "other",
    [
        {},
        {"subjectKind": "index", "subjectId": "004432", "fundCode": "004432"},
        nav(fundCode="011035"),
        nav(market="FUND_HK"),
        nav("011035"),
        {
            "subjectKind": "fund",
            "subjectId": "uuid-b",
            "fundCode": "004432",
            "market": "FUND_CN",
        },
    ],
)
def test_missing_conflicting_or_different_typed_identity_never_associates(other):
    base = {
        "subjectKind": "fund",
        "subjectId": "uuid-a",
        "fundCode": "004432",
        "market": "FUND_CN",
    }
    assert not same_fund_identity(base, other)


def test_selected_anchor_is_authority_and_unrelated_code_fallback_is_ignored():
    anchor = nav(discoverySector="NONFERROUS")
    packet = {
        "discoveryActionableZones": [anchor],
        "quantitativeScan": {
            "items": [
                nav("011035", discoverySector="RARE_EARTH"),
                nav(fundCode="011035", discoverySector="ROBOT"),
                {
                    "subjectKind": "index",
                    "subjectId": "004432",
                    "fundCode": "004432",
                    "discoverySector": "AI",
                },
            ]
        },
        "discoveryUniverse": [
            {"fundCode": "004432", "market": "FUND_CN", "sector": "NONFERROUS"}
        ],
    }
    assert plan_industry_ids(packet, {"subjectKind": "nav", "subjectId": "004432"}) == {
        "NONFERROUS"
    }


def test_absent_exact_server_anchor_does_not_bind_missing_identity_or_model_code():
    packet = {
        "discoveryActionableZones": [nav(discoverySector="NONFERROUS")],
        "discoveryUniverse": [{"fundCode": "004432", "sector": "ROBOT"}],
    }
    assert plan_industry_ids(packet, {}) == set()
    assert (
        plan_industry_ids(packet, {"subjectKind": "index", "subjectId": "004432"})
        == set()
    )


def test_same_catalog_fund_sector_is_required_even_when_anchor_has_no_sector():
    packet = {
        "discoveryActionableZones": [nav()],
        "discoveryUniverse": [
            {"fundCode": "004432", "market": "FUND_CN", "sector": "NONFERROUS"},
        ],
    }
    assert plan_industry_ids(packet, nav()) == {"NONFERROUS"}


def test_anchor_industry_cannot_be_relabelled_by_lower_priority_same_code_rows():
    packet = {
        "discoveryActionableZones": [nav(discoverySector="NONFERROUS")],
        "quantitativeScan": {"items": [nav(discoverySector="RARE_EARTH")]},
        "discoveryUniverse": [{"fundCode": "004432", "sector": "ROBOT"}],
    }
    assert plan_industry_ids(packet, nav()) == {"NONFERROUS"}


def test_plan_metadata_conflicting_with_exact_anchor_cannot_acquire_its_industry():
    packet = {"discoveryActionableZones": [nav(discoverySector="NONFERROUS")]}
    assert plan_industry_ids(packet, nav(fundCode="011035")) == set()
    assert plan_industry_ids(packet, nav(market="FUND_HK")) == set()
