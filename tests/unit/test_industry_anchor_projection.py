"""The real packet projection retains the catalog's authoritative fund sector."""

from copy import deepcopy

import pytest

from backend.investment.discovery import discovery_packet
from backend.macro.industry_contract import plan_industry_ids
from test_phase2_research import NOW, anchor


def packet():
    product = {
        "subjectKind": "nav",
        "subjectId": "004432",
        "fundCode": "004432",
        "market": "FUND_CN",
        "label": "有色金属联接",
        "discoverySector": "NONFERROUS",
    }
    # A stale/raw price plan is not allowed to relabel the catalog product.
    price = {**anchor(), **product, "discoverySector": "RARE_EARTH"}
    return discovery_packet([product], [price], {}, NOW)


def test_real_discovery_projection_copies_catalog_sector_over_raw_plan_sector():
    public = packet()
    selected = public["discoveryActionableZones"][0]
    assert selected["discoverySector"] == "NONFERROUS"


def test_real_projected_anchor_rejects_conflicting_scanner_industry_expansion():
    public = packet()
    selected = public["discoveryActionableZones"][0]
    public["quantitativeScan"] = {
        "items": [{**deepcopy(selected), "discoverySector": "RARE_EARTH"}]
    }
    assert plan_industry_ids(public, selected) == {"NONFERROUS"}


def test_legacy_sectorless_anchor_uses_unique_catalog_before_scanner():
    public = packet()
    selected = public["discoveryActionableZones"][0]
    selected.pop("discoverySector", None)
    public["quantitativeScan"] = {
        "items": [{**deepcopy(selected), "discoverySector": "RARE_EARTH"}]
    }
    assert plan_industry_ids(public, selected) == {"NONFERROUS"}


@pytest.mark.parametrize("source", ["catalog", "scanner"])
def test_ambiguous_fallback_sectors_raise_instead_of_skipping_required_review(source):
    public = packet()
    selected = public["discoveryActionableZones"][0]
    selected.pop("discoverySector", None)
    conflicting = [
        {**deepcopy(selected), "discoverySector": "NONFERROUS"},
        {**deepcopy(selected), "discoverySector": "RARE_EARTH"},
    ]
    if source == "catalog":
        public["discoveryUniverse"] = conflicting
    else:
        public["discoveryUniverse"] = []
        public["quantitativeScan"] = {"items": conflicting}
    with pytest.raises(ValueError, match="^ambiguous server fund industry$"):
        plan_industry_ids(public, selected)


def test_legacy_sectorless_anchor_can_use_a_single_scanner_sector_without_catalog():
    public = packet()
    selected = public["discoveryActionableZones"][0]
    selected.pop("discoverySector", None)
    public["discoveryUniverse"] = []
    public["quantitativeScan"] = {
        "items": [{**deepcopy(selected), "discoverySector": "NONFERROUS"}]
    }
    assert plan_industry_ids(public, selected) == {"NONFERROUS"}
