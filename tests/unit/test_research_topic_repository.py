"""Topic archives retain observations without acquiring trading capability."""

from importlib import import_module
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from backend.errors import ValidationError_
from tests.unit.test_research_acceptance_repository import NOW


def registry(run_id, count=30):
    return {
        "researchId": str(run_id),
        "validatedAt": NOW.isoformat(),
        "version": "research-topics-v2",
        "revision": "a" * 64,
        "buildCommit": None,
        "coverage": {"plannedScopeCount": 10, "pendingScopeCount": 2},
        "auditTasks": [],
        "issues": [],
        "items": [
            {
                "topicId": f"topic:{index:032x}",
                "key": f"topic-{index}",
                "title": f"主题{index}",
                "summary": "仅供研究观察",
                "status": "PENDING_REVIEW",
                "reasonCode": "AUDIT_BUDGET_DEFERRED",
                "reason": "尚待审核",
                "impact": "缺口保留",
                "measures": ["等待补证"],
                "pendingTasks": [],
                "sourceIds": ["s1"],
                "match": {
                    "status": "STANDARD",
                    "industryIds": ["NONFERROUS"],
                    "relatedIndustryIds": [],
                    "evidence": ["STANDARD_CODE"],
                    "registryVersion": "industry-registry-v1",
                },
                "investmentEligible": False,
                "plans": [{"price": "PRIVATE_PRICE"}],
            }
            for index in range(count)
        ],
    }


def repository():
    return import_module("backend.macro.research_topic_repository")


def test_topic_projection_keeps_status_explanation_and_never_copies_plans():
    run_id = uuid4()
    projected = repository().prepare_topic_registry(registry(run_id), run_id, NOW)
    assert len(projected["items"]) == 30
    assert projected["items"][0]["status"] == "PENDING_REVIEW"
    assert projected["items"][0]["pendingTasks"] == []
    assert projected["actionable"] is False
    assert "PRIVATE_PRICE" not in str(projected) and "plans" not in str(projected)


@pytest.mark.parametrize("count", [73])
def test_topic_archive_rejects_over_budget_instead_of_silent_top_n(count):
    run_id = uuid4()
    with pytest.raises(ValidationError_):
        repository().prepare_topic_registry(registry(run_id, count), run_id, NOW)


def test_topic_archive_rejects_wrong_run_or_clock_and_false_formal_eligibility():
    run_id = uuid4()
    value = registry(run_id, 1)
    with pytest.raises(ValidationError_):
        repository().prepare_topic_registry(value, uuid4(), NOW)
    value["items"][0]["investmentEligible"] = True
    with pytest.raises(ValidationError_):
        repository().prepare_topic_registry(value, run_id, NOW)


def test_topic_archive_does_not_excerpt_oversize_entry():
    run_id = uuid4()
    value = registry(run_id, 1)
    value["items"][0]["summary"] = "字" * 17000
    with pytest.raises(ValidationError_):
        repository().prepare_topic_registry(value, run_id, NOW)


def test_real_batch_registry_contract_keeps_formal_and_all_discovered_topics():
    from tests.unit.test_research_topics_v2 import evaluate, NOW as CORE_NOW, RUN

    raw = evaluate(30)["registry"]
    projected = repository().prepare_topic_registry(raw, RUN, CORE_NOW)
    assert len(projected["items"]) == 31
    assert projected["coverage"]["maxModelCalls"] == 1
    assert projected["coverage"]["additionalModelCalls"] == 0
    assert projected["coverage"]["searchExecutionVerified"] is False
    assert projected["items"][0]["match"]["scopeVerified"] is not None


def test_registry_supports_full_union72_and_preserves_explicit_budget_coverage():
    run_id = uuid4()
    assert (
        len(
            repository().prepare_topic_registry(registry(run_id, 72), run_id, NOW)[
                "items"
            ]
        )
        == 72
    )
    value = registry(run_id, 0)
    value["coverage"].update(
        hasMore=True, totalCount=30, countsByStatus={"PENDING_REVIEW": 30}
    )
    projected = repository().prepare_topic_registry(value, run_id, NOW)
    assert projected["coverage"]["totalCount"] == 30
    assert projected["coverage"]["storedCount"] == 0
    assert projected["coverage"]["hasMore"] is True


def test_unavailable_code_revision_is_explicit_metadata_not_research_failure():
    run_id = uuid4()
    value = registry(run_id, 1)
    value["revision"] = None
    projected = repository().prepare_topic_registry(value, run_id, NOW)
    assert projected["revision"] is None
    assert projected["revisionStatus"] == "UNAVAILABLE"
    value["revision"] = "unverified-runtime-label"
    with pytest.raises(ValidationError_):
        repository().prepare_topic_registry(value, run_id, NOW)


def test_frozen_resource_budget_and_dynamic_unexecuted_scope_remain_explicit():
    from backend.macro.research_topics import make_task

    run_id = uuid4()
    value = registry(run_id, 1)
    value["coverage"].update(
        maxModelCalls=1,
        additionalModelCalls=0,
        maxInputBytes=4194304,
        maxOutputBytes=2097152,
        maxExecutionSeconds=1200,
    )
    task = make_task(
        "TOPIC_SCOPE:" + "a" * 32,
        "FINANCING_COOLING",
        "新主题融资核查",
        {"start": "2026-07-01", "end": "2026-09-29", "lookbackDays": 90},
        status="PLANNED_DEFERRED",
        reason_code="INDUSTRY_IDENTITY_PENDING",
    )
    task["searchTerm"] = "明确新主题"
    value["items"][0]["pendingTasks"] = [task]
    value["items"][0]["match"].update(
        unresolvedClaims=["明确新主题"],
        proposedScopes=[
            {
                "industryId": task["industryId"],
                "searchTerm": "明确新主题",
                "registryVersion": "industry-registry-v1",
            }
        ],
    )
    projected = repository().prepare_topic_registry(value, run_id, NOW)
    assert projected["coverage"]["maxInputBytes"] == 4194304
    assert projected["coverage"]["additionalModelCalls"] == 0
    assert projected["items"][0]["pendingTasks"][0]["status"] == "PLANNED_DEFERRED"
    assert (
        projected["items"][0]["match"]["proposedScopes"][0]["searchTerm"]
        == "明确新主题"
    )


def test_cursor_is_bound_to_snapshot_status_and_cannot_skip_invalid_position():
    module = repository()
    snapshot = uuid4()
    cursor = module.encode_cursor(snapshot, 12, "PENDING_REVIEW")
    assert module.decode_cursor(cursor, snapshot, "PENDING_REVIEW") == 12
    for identity, status in [(uuid4(), "PENDING_REVIEW"), (snapshot, "FORMAL")]:
        with pytest.raises(ValidationError_):
            module.decode_cursor(cursor, identity, status)
    with pytest.raises(ValidationError_):
        module.decode_cursor("not-a-cursor", snapshot, None)


@pytest.mark.asyncio
async def test_registry_size_omission_is_frozen_without_blocking_original_receipt():
    import json
    from backend.macro import research_acceptance_repository as acceptance
    from tests.unit.test_research_acceptance_database import outcome

    run_id = uuid4()
    value = registry(run_id, 1)
    value["items"][0]["summary"] = "字" * 17000
    conn = AsyncMock()
    conn.fetchrow.return_value = {"run_id": run_id, "status": "AUDIT_PENDING"}
    await acceptance.enqueue_research_archive(
        conn, run_id, {}, {}, {}, NOW, outcome(run_id, NOW), topic_registry=value
    )
    frozen = json.loads(conn.execute.await_args.args[3])
    assert frozen["topicRegistryOmission"]["content"] is None
    assert frozen["topicRegistryOmission"]["omissionReason"] == "SIZE_LIMIT"
    assert frozen["outcome"]["topicRegistryAvailable"] is False
    assert frozen["outcome"]["topicArchiveIssue"] == "REGISTRY_OUTPUT_LIMIT"


@pytest.mark.asyncio
async def test_topic_size_database_failure_is_not_misclassified_as_resource_omission():
    from backend.macro import research_acceptance_repository as acceptance
    from tests.unit.test_research_acceptance_database import outcome

    run_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = OSError("owned database probe failed")
    with pytest.raises(OSError):
        await acceptance.enqueue_research_archive(
            conn,
            run_id,
            {},
            {},
            {},
            NOW,
            outcome(run_id, NOW),
            topic_registry=registry(run_id, 1),
        )
    conn.execute.assert_not_awaited()
