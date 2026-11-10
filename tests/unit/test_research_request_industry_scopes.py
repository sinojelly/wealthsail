"""Frozen searches never limit global industries or emerging model insights."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest

from backend.macro import daily_research as research
from backend.macro.model_projection import model_packet
from backend.macro import research_topics as topics
from backend.macro.industry_contract import STANDARD_INDUSTRY_CODES
from tests.unit.test_daily_research import NOW
from tests.unit.test_daily_research_job import NOW as JOB_NOW, isolate, settings
from tests.unit.test_research_frozen_contract_projection import frozen as historical_v2


SELECTED = ["DEFENCE", "BROKER", "FINTECH", "RARE_EARTH", "BROAD"]
DEFERRED = [
    "ROBOT", "CONSUMPTION", "NONFERROUS", "AI", "INNOVATIVE_DRUG", "US_TECH",
    "GOLD", "COMMUNICATION", "ACTIVE_GROWTH", "ROBOTICS_MOBILITY", "BANK",
    "GLOBAL_ENERGY", "FINANCE_POLICY", "RARE_EARTH_METALS",
]


def packet(now=NOW, scopes=None):
    return topics.freeze_discovery_packet(
        {
            "asOf": now.isoformat(),
            "pendingTopicScopes": [
                {"industryId": scope}
                for scope in (SELECTED + DEFERRED if scopes is None else scopes)
            ],
        },
        now,
    )


def prompt_view(value):
    return json.loads(research.build_prompt(value).split("\n")[-1])


def review_id_schema(schema):
    return schema["$defs"]["ValueDirectionProposal"]["properties"]["reviewIndustryIds"]


def test_new_prompt_distinguishes_global_industry_identity_from_frozen_audit_work():
    value = packet()
    before = deepcopy(value)
    manifest = topics.frozen_manifest(value)
    assert manifest["selectedScopes"] == SELECTED
    assert manifest["deferredScopes"] == DEFERRED
    view = prompt_view(value)
    contract = view["industryCycleResearch"]
    assert set(contract["allowedReviewIndustryIds"]) == STANDARD_INDUSTRY_CODES
    assert contract["freeIndustryLabelsAllowed"] is True
    assert contract["observationScopes"] == DEFERRED
    assert "MEMORY" in view["topicIdentityRegistry"]["standardIndustryCodes"]
    assert "SEMICONDUCTOR_AI" in view["topicIdentityRegistry"]["standardIndustryCodes"]
    assert {"MEMORY", "SEMICONDUCTOR_AI"} <= set(contract["allowedReviewIndustryIds"])
    assert value == before


def test_current_prompt_opens_global_industries_and_preserves_historical_frozen_views():
    for value in (packet(), historical_v2()):
        before = deepcopy(value)
        for version in ("daily-model-input-v1", "daily-model-input-v2"):
            view = model_packet(value, version=version)["industryCycleResearch"]
            assert "allowedReviewIndustryIds" not in view
            assert "observationScopes" not in view
        assert value == before
    old = historical_v2()
    view = prompt_view(old)["industryCycleResearch"]
    assert set(view["allowedReviewIndustryIds"]) == STANDARD_INDUSTRY_CODES
    assert view["observationScopes"] == []
    legacy = prompt_view({"industryCycleResearch": {"required": True}})
    assert "allowedReviewIndustryIds" not in legacy["industryCycleResearch"]


def test_request_schema_accepts_unfrozen_industries_and_free_labels_independently():
    value = packet()
    before, base = deepcopy(value), deepcopy(research.REPORT_SCHEMA)
    schema = research.report_schema_for_packet(value)
    field = review_id_schema(schema)
    assert "enum" not in field["items"]
    assert field["items"]["type"] == "string"
    assert field.get("minItems", 0) == 0
    schema["$defs"]["ValueDirectionProposal"]["properties"]["title"]["maxLength"] = 1
    field["items"]["description"] = "free industry labels"
    assert "description" not in review_id_schema(research.report_schema_for_packet(value))["items"]
    assert research.REPORT_SCHEMA == base
    assert value == before


def test_empty_frozen_audit_scope_still_allows_industry_discovery():
    schema = research.report_schema_for_packet(packet(scopes=[]))
    field = review_id_schema(schema)
    assert field["maxItems"] > 0
    assert "enum" not in field["items"]
    assert field.get("minItems", 0) == 0


@pytest.mark.parametrize("value", [None, "v2"])
def test_non_targeted_request_schema_is_an_independent_unchanged_copy(value):
    original = deepcopy(research.REPORT_SCHEMA)
    schema = research.report_schema_for_packet(historical_v2() if value == "v2" else {})
    assert schema == original
    schema["$defs"]["ValueDirectionProposal"]["properties"]["reviewIndustryIds"]["maxItems"] = 0
    assert research.REPORT_SCHEMA == original


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_both_dispatch_paths_send_the_packet_bound_schema(monkeypatch, remote):
    from backend.integrations import codex_client

    mod, state = isolate(monkeypatch)
    value, before = packet(JOB_NOW), deepcopy(research.REPORT_SCHEMA)
    configured = settings(codex_runner_url="http://fixture" if remote else "")
    if remote:
        monkeypatch.setattr(codex_client, "preflight", AsyncMock(return_value={"version": "fixture"}))
        provider = AsyncMock(return_value={"summary": "public", "sources": []})
        monkeypatch.setattr(codex_client, "research", provider)
    else:
        provider = mod.run_codex_research
    await mod._execute_daily_research(object(), configured, JOB_NOW, packet=value)
    assert state["consumed"] is True
    provider.assert_awaited_once()
    sent = provider.await_args.args[2]
    assert "enum" not in review_id_schema(sent)["items"]
    assert sent is not research.REPORT_SCHEMA
    assert research.REPORT_SCHEMA == before


@pytest.mark.asyncio
async def test_schema_preparation_failure_precedes_model_guard_and_dispatch(monkeypatch):
    mod, state = isolate(monkeypatch)
    value = packet(JOB_NOW)

    def invalid_schema(_packet):
        raise ValueError("schema preparation failed")

    monkeypatch.setattr(mod, "report_schema_for_packet", invalid_schema)
    with pytest.raises(ValueError, match="schema preparation failed"):
        await mod._execute_daily_research(object(), settings(), JOB_NOW, packet=value)
    assert state["consumed"] is False
    mod.create_model_guard.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()
