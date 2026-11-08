"""Historical audit contracts are versioned; model inputs carry no duplicate manifest."""

from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from backend.macro import research_topics as topics
from backend.macro.model_projection import model_packet
from tests.unit.test_industrial_metals_research_regression import case
from tests.unit.test_daily_research import NOW


def frozen():
    packet, _, _ = case()
    # Compatibility tests must retain the historical writer after new runtime
    # defaults change, rather than labelling a v3 packet as an old profile.
    with patch.multiple(topics, DISCOVERY_VERSION="research-discovery-v2", ALLOCATION_VERSION="industry-allocation-v2", BUDGET={
        "maxTopics": 64, "maxFormalDirections": 8, "maxScopes": 10, "maxQueries": 50,
        "maxSources": 24, "maxModelCalls": 1, "maxInputBytes": 4194304,
        "maxOutputBytes": 2097152, "maxExecutionSeconds": 1200,
    }):
        return topics.freeze_discovery_packet(packet, NOW)


def rehash(packet):
    manifest = packet["researchDiscovery"]["auditManifest"]
    manifest["manifestHash"] = topics.digest(
        {key: value for key, value in manifest.items() if key != "manifestHash"}
    )


def test_v2_historical_budget_does_not_follow_new_runtime_budget(monkeypatch):
    packet = frozen()
    original = deepcopy(packet)
    monkeypatch.setitem(topics.BUDGET, "maxScopes", 8)
    monkeypatch.setitem(topics.BUDGET, "maxQueries", 40)
    assert (
        topics.frozen_manifest(packet) == original["researchDiscovery"]["auditManifest"]
    )
    assert topics.freeze_discovery_packet(packet, NOW) == original
    assert packet == original


def test_original_v2_budget_profile_remains_supported_without_computing_new_hash():
    packet = frozen()
    keep = {"maxTopics", "maxFormalDirections", "maxScopes", "maxQueries", "maxSources"}
    for budget in (
        packet["researchDiscovery"]["budget"],
        packet["researchDiscovery"]["auditManifest"]["budget"],
    ):
        for key in list(budget):
            if key not in keep:
                del budget[key]
    rehash(packet)
    original = deepcopy(packet)
    assert topics.frozen_manifest(packet)["budget"]["maxQueries"] == 50
    assert topics.freeze_discovery_packet(packet, NOW) == original


@pytest.mark.parametrize(
    "entry", ["is_v2", "frozen_manifest", "freeze_discovery_packet"]
)
def test_unknown_discovery_version_is_incompatible_without_v1_fallback(entry):
    packet = frozen()
    packet["researchDiscovery"]["version"] = "research-discovery-future"
    original = deepcopy(packet)
    with pytest.raises(ValueError) as error:
        getattr(topics, entry)(
            packet, NOW
        ) if entry == "freeze_discovery_packet" else getattr(topics, entry)(packet)
    assert getattr(error.value, "code", None) == "INCOMPATIBLE"
    assert "hash" not in str(error.value).lower()
    assert packet == original


def test_unknown_manifest_version_is_not_misreported_as_hash_damage():
    packet = frozen()
    packet["researchDiscovery"]["auditManifest"]["version"] = (
        "research-audit-manifest-future"
    )
    with pytest.raises(ValueError) as error:
        topics.frozen_manifest(packet)
    assert getattr(error.value, "code", None) == "INCOMPATIBLE"


def test_unrecognized_version_value_cannot_raise_unclassified_hash_table_error():
    packet = frozen()
    packet["researchDiscovery"]["version"] = ["future-version"]
    with pytest.raises(ValueError) as error:
        topics.is_v2(packet)
    assert getattr(error.value, "code", None) == "INCOMPATIBLE"


def test_changed_budget_requires_a_different_supported_contract_version():
    packet = frozen()
    packet["researchDiscovery"]["budget"]["maxScopes"] = 8
    packet["researchDiscovery"]["auditManifest"]["budget"]["maxScopes"] = 8
    rehash(packet)
    with pytest.raises(ValueError) as error:
        topics.frozen_manifest(packet)
    assert getattr(error.value, "code", None) == "INCOMPATIBLE"


def test_supported_v2_still_rejects_changed_frozen_hash():
    packet = frozen()
    packet["researchDiscovery"]["auditManifest"]["manifestHash"] = "0" * 64
    with pytest.raises(ValueError) as error:
        topics.frozen_manifest(packet)
    assert getattr(error.value, "code", None) != "INCOMPATIBLE"


def test_model_gets_one_task_view_while_original_frozen_packet_and_hash_remain_exact():
    packet = frozen()
    original = deepcopy(packet)
    manifest = original["researchDiscovery"]["auditManifest"]
    result = model_packet(packet)
    assert "researchDiscovery" not in result
    view = result["industryCycleResearch"]
    assert view["window"] == manifest["window"]
    assert [row["industry"] for row in view["candidates"]] == manifest["selectedScopes"]
    assert view["omittedIndustries"] == manifest["deferredScopes"]
    tasks = [query for row in view["candidates"] for query in row["queries"]]
    assert {row["taskId"] for row in tasks} == {
        row["taskId"] for row in manifest["tasks"]
    }
    encoded = json.dumps(result, ensure_ascii=False)
    for task in manifest["tasks"]:
        assert encoded.count(json.dumps(task["query"], ensure_ascii=False)) == 1
    assert "candidateContract" not in view and "manifestHash" not in encoded
    baseline = json.dumps(
        {key: original[key] for key in ("researchDiscovery", "industryCycleResearch")},
        ensure_ascii=False,
    )
    assert len(json.dumps(view, ensure_ascii=False).encode()) < len(baseline.encode())
    view["candidates"][0]["queries"][0]["query"] = "model-side mutation"
    assert packet == original
    assert topics.frozen_manifest(packet)["manifestHash"] == manifest["manifestHash"]


def test_v1_model_input_keeps_its_existing_audit_contract():
    packet, _, _ = case()
    packet["industryCycleResearch"] = {
        "required": True,
        "candidates": [
            {
                "industry": "NONFERROUS",
                "queries": [{"kind": "FINANCING_COOLING", "query": "原有兼容查询"}],
            }
        ],
    }
    assert topics.is_v2(packet) is False
    assert topics.frozen_manifest(packet) is None
    assert (
        model_packet(packet)["industryCycleResearch"] == packet["industryCycleResearch"]
    )
