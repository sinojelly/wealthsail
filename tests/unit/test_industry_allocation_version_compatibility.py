"""Future allocation is opt-in; absent frozen contracts keep legacy duties."""

from copy import deepcopy

import pytest

from backend.integrations.daily_sources import industry_cycle_research_tasks
from backend.macro.research_topics import ALLOCATION_VERSION, freeze_discovery_packet
from tests.unit.test_daily_sources import NOW


def packet():
    return {
        "asOf": NOW.isoformat(),
        "discoveryUniverse": [{"discoverySector": "MOBILITY"}],
        # An untrusted loose object cannot opt a historical packet into a policy.
        "industryCycleResearch": {"allocationVersion": ALLOCATION_VERSION},
    }


def test_absent_frozen_contract_uses_legacy_even_with_unhashed_policy_claim():
    value = packet()
    before = deepcopy(value)
    result = industry_cycle_research_tasks(value, NOW)
    assert [row["industry"] for row in result["candidates"]] == ["MOBILITY"]
    assert "allocationVersion" not in result
    assert value == before


def test_explicit_future_and_new_freeze_reject_unknown_product_scope():
    result = industry_cycle_research_tasks(packet(), NOW, allocation_version=ALLOCATION_VERSION)
    assert result["allocationVersion"] == ALLOCATION_VERSION
    assert result["candidates"] == []
    assert result["excludedScopes"][0]["reasonCode"] == "UNKNOWN_PRODUCT_SCOPE"
    frozen = freeze_discovery_packet(packet(), NOW)
    assert frozen["researchDiscovery"]["auditManifest"]["candidateContract"] == result


def test_unknown_explicit_allocation_policy_cannot_fall_back():
    with pytest.raises(ValueError, match="unsupported"):
        industry_cycle_research_tasks(packet(), NOW, allocation_version="unrecognized")
