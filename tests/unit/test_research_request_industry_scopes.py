"""New requests expose and constrain the exact frozen direction industry scope."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest

from backend.macro import daily_research as research
from backend.macro.model_projection import model_packet
from backend.macro import research_topics as topics
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


def test_new_prompt_distinguishes_registered_identity_from_frozen_direction_permission():
    value = packet()
    before = deepcopy(value)
    manifest = topics.frozen_manifest(value)
    assert manifest["selectedScopes"] == SELECTED
    assert manifest["deferredScopes"] == DEFERRED
    view = prompt_view(value)
    contract = view["industryCycleResearch"]
    assert contract["allowedReviewIndustryIds"] == SELECTED + DEFERRED
    assert contract["observationScopes"] == DEFERRED
    assert "MEMORY" in view["topicIdentityRegistry"]["standardIndustryCodes"]
    assert "SEMICONDUCTOR_AI" in view["topicIdentityRegistry"]["standardIndustryCodes"]
    assert not {"MEMORY", "SEMICONDUCTOR_AI"} & set(contract["allowedReviewIndustryIds"])
    assert value == before


def test_v24_prompt_uses_selected_only_for_old_contract_and_keeps_historical_views():
    for value in (packet(), historical_v2()):
        before = deepcopy(value)
        for version in ("daily-model-input-v1", "daily-model-input-v2"):
            view = model_packet(value, version=version)["industryCycleResearch"]
            assert "allowedReviewIndustryIds" not in view
            assert "observationScopes" not in view
        assert value == before
    old = historical_v2()
    manifest = topics.frozen_manifest(old)
    view = prompt_view(old)["industryCycleResearch"]
    assert view["allowedReviewIndustryIds"] == manifest["selectedScopes"]
    assert view["observationScopes"] == []
    legacy = prompt_view({"industryCycleResearch": {"required": True}})
    assert "allowedReviewIndustryIds" not in legacy["industryCycleResearch"]


def test_request_schema_excludes_registered_but_unfrozen_ids_and_preserves_observations():
    value = packet()
    before, base = deepcopy(value), deepcopy(research.REPORT_SCHEMA)
    schema = research.report_schema_for_packet(value)
    field = review_id_schema(schema)
    assert field["items"]["enum"] == SELECTED + DEFERRED
    assert "US_TECH" in field["items"]["enum"]
    assert not {"MEMORY", "SEMICONDUCTOR_AI"} & set(field["items"]["enum"])
    assert field.get("minItems", 0) == 0
    schema["$defs"]["ValueDirectionProposal"]["properties"]["title"]["maxLength"] = 1
    field["items"]["enum"].append("INVENTED")
    assert review_id_schema(research.report_schema_for_packet(value))["items"]["enum"] == SELECTED + DEFERRED
    assert research.REPORT_SCHEMA == base
    assert value == before


def test_empty_frozen_scope_allows_only_empty_direction_review_ids_without_empty_enum():
    schema = research.report_schema_for_packet(packet(scopes=[]))
    field = review_id_schema(schema)
    assert field["maxItems"] == 0
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
    assert review_id_schema(sent)["items"]["enum"] == SELECTED + DEFERRED
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
