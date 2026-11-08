"""Future identity parsing never rewrites frozen v1 evidence or grants a plan."""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.macro import research_topics as topics
from backend.macro.daily_research import build_prompt
from tests.unit.test_industry_allocation_policy_v2 import freeze_discovery_packet as freeze_v2

NOW = datetime(2026, 11, 6, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
V2 = "topic-identity-v2"


def proposal(claims, related=None):
    return {
        "key": "identity-watch",
        "title": "公开观察",
        "thesis": "保持观察。",
        "industryClaims": claims,
        "relatedIndustryIds": related or [],
        "sourceIds": [],
        "unknowns": [],
        "pendingTasks": [],
    }


@pytest.mark.parametrize(
    "claim,code",
    [
        ("robot", "ROBOT"),
        ("券商", "BROKER"),
        ("金融科技", "FINTECH"),
        ("存储芯片", "MEMORY"),
    ],
)
def test_v2_only_canonical_case_and_registered_exact_names_establish_identity(
    claim, code
):
    assert topics.match_topic(proposal([claim]), version=V2)["industryIds"] == [code]


def test_narrative_claim_is_preserved_but_never_becomes_a_dynamic_search_scope():
    claim = "汽车业务增长不能证明机器人盈利。"
    raw = proposal([claim], ["ROBOT"])
    before = deepcopy(raw)
    match = topics.match_topic(raw, version=V2)
    assert match["industryIds"] == []
    assert match["relatedIndustryIds"] == ["ROBOT"]
    assert match["unresolvedClaims"] == [claim]
    assert match["proposedScopes"] == []
    assert "NARRATIVE_CLAIM_NOT_IDENTITY" in match["evidence"]
    assert raw == before


def test_mixed_claims_keep_resolved_identity_without_promoting_related_or_prose():
    match = topics.match_topic(
        proposal(["ROBOT", "ANZ对2027年铝过剩的判断是预测。"], ["NONFERROUS"]),
        version=V2,
    )
    assert match["industryIds"] == ["ROBOT"]
    assert match["relatedIndustryIds"] == ["NONFERROUS"]
    assert match["proposedScopes"] == []
    assert "PARTIAL_INDUSTRY_IDENTITY" in match["evidence"]


def test_named_unregistered_scope_can_still_schedule_five_deferred_checks():
    match = topics.match_topic(proposal(["合成生物制造"]), version=V2)
    assert len(match["proposedScopes"]) == 1
    manifest = {
        "selectedScopes": [],
        "window": {"start": "2026-08-08", "end": "2026-11-06", "lookbackDays": 90},
    }
    tasks = topics.topic_tasks(match, manifest)
    assert len(tasks) == 5
    assert all(task["searchTerm"] == "合成生物制造" for task in tasks)


def test_english_judgment_cannot_be_a_named_scope_but_new_english_industry_can():
    claim = "Profit growth does not prove cash"
    rejected = topics.match_topic(proposal([claim]), version=V2)
    assert rejected["proposedScopes"] == []
    assert rejected["unresolvedClaims"] == [claim]
    named = topics.match_topic(proposal(["quantum computing"]), version=V2)
    assert named["proposedScopes"][0]["searchTerm"] == "quantum computing"


@pytest.mark.parametrize("code", ["EVENT_ROBOT", "MOBILITY"])
def test_v2_unknown_uppercase_code_cannot_be_repackaged_as_named_scope(code):
    raw = proposal([code])
    before = deepcopy(raw)
    match = topics.match_topic(raw, version=V2)
    assert match["industryIds"] == []
    assert match["unresolvedClaims"] == [code]
    assert match["proposedScopes"] == []
    assert raw == before


def test_standard_uppercase_code_and_legacy_unknown_scope_keep_existing_behavior():
    standard = topics.match_topic(proposal(["ROBOT"]), version=V2)
    assert standard["status"] == "STANDARD"
    assert standard["industryIds"] == ["ROBOT"] and not standard["proposedScopes"]
    for version in (None, "topic-identity-v1"):
        legacy = topics.match_topic(proposal(["EVENT_ROBOT"]), version=version)
        assert legacy["proposedScopes"] == [
            {
                "industryId": "TOPIC_SCOPE:" + topics.digest("EVENT_ROBOT")[:32],
                "searchTerm": "EVENT_ROBOT",
                "registryVersion": topics.INDUSTRY_REGISTRY_VERSION,
            }
        ]


def test_related_industrial_metals_never_becomes_nonferrous_identity():
    match = topics.match_topic(proposal(["工业金属"]), version=V2)
    assert match["industryIds"] == [] and match["relatedIndustryIds"] == ["NONFERROUS"]
    assert match["status"] == "NEW_TOPIC"


def test_related_code_case_normalization_still_cannot_establish_topic_identity():
    match = topics.match_topic(proposal([], ["robot"]), version=V2)
    assert match["relatedIndustryIds"] == ["ROBOT"]
    assert match["industryIds"] == [] and match["proposedScopes"] == []


def test_future_freeze_versions_both_match_and_allocation_in_hashed_manifest():
    frozen = freeze_v2({"asOf": NOW.isoformat()}, NOW)
    manifest = frozen["researchDiscovery"]["auditManifest"]
    assert topics.topic_match_version(frozen) == V2
    assert (
        manifest["topicMatchVersion"]
        == frozen["researchDiscovery"]["topicMatchVersion"]
        == V2
    )
    assert manifest["allocationVersion"] == "industry-allocation-v2"
    assert (
        manifest["candidateContract"]["allocationVersion"] == "industry-allocation-v2"
    )
    assert manifest["manifestHash"] == topics.digest(
        {k: v for k, v in manifest.items() if k != "manifestHash"}
    )


def test_old_frozen_missing_policy_fields_replays_unchanged_with_old_matching():
    frozen = freeze_v2({"asOf": NOW.isoformat()}, NOW)
    discovery = frozen["researchDiscovery"]
    manifest = discovery["auditManifest"]
    for obj in (discovery, manifest, manifest["candidateContract"]):
        obj.pop("topicMatchVersion", None)
        obj.pop("allocationVersion", None)
    manifest["manifestHash"] = topics.digest(
        {k: v for k, v in manifest.items() if k != "manifestHash"}
    )
    before = deepcopy(frozen)
    assert topics.freeze_discovery_packet(frozen, NOW) == before
    assert topics.topic_match_version(frozen) == "topic-identity-v1"
    legacy = topics.match_topic(
        proposal(["汽车业务增长不能证明机器人盈利。"]),
        version=topics.topic_match_version(frozen),
    )
    assert len(legacy["proposedScopes"]) == 1


@pytest.mark.parametrize("field", ["topicMatchVersion", "allocationVersion"])
def test_unknown_frozen_policy_version_is_rejected(field):
    frozen = freeze_v2({"asOf": NOW.isoformat()}, NOW)
    frozen["researchDiscovery"][field] = "future-unknown"
    with pytest.raises(topics.FrozenDiscoveryCompatibilityError):
        topics.freeze_discovery_packet(frozen, NOW)


def test_prompt_and_topic_schema_disambiguate_names_from_risk_statements():
    prompt = build_prompt(
        topics.freeze_discovery_packet({"asOf": NOW.isoformat()}, NOW)
    )
    assert "industryClaims" in prompt and "topicIdentityRegistry" in prompt
    assert "汽车业务增长不能证明机器人盈利" in prompt
    schema = topics.TopicProposal.model_json_schema()
    assert "行业身份" in schema["properties"]["industryClaims"]["description"]
