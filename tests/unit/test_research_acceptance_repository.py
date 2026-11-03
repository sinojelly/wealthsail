"""Frozen acceptance evidence is bounded and replay never acquires execution authority."""

from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module, util
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import asyncpg

from backend.errors import ForbiddenError, ValidationError_

NOW = datetime(2026, 11, 3, 8, tzinfo=timezone.utc)
RUN = uuid4()


def repo():
    assert util.find_spec("backend.macro.research_acceptance_repository"), (
        "acceptance repository missing"
    )
    return import_module("backend.macro.research_acceptance_repository")


def outcome(**changes):
    return {
        "researchId": str(RUN),
        "validatorVersion": "research-acceptance-v1",
        "validatedAt": NOW.isoformat(),
        "decision": "PARTIAL",
        "formalPublishable": False,
        "actionable": False,
        "issueListComplete": True,
        "counts": {
            "sources": {"inputCount": 2, "acceptedCount": 1, "quarantinedCount": 1}
        },
        "issues": [],
        "accepted": {
            "sources": [
                {
                    "id": "s1",
                    "title": "公共证据",
                    "url": "https://example.com/report",
                    "plans": ["PRIVATE"],
                }
            ],
            "valueDirections": [],
            "industryCycleReviews": [],
            "events": [],
            "upcoming": [],
        },
        **changes,
    }


def test_freeze_keeps_exact_raw_strings_and_canonical_json_hashes():
    module = repo()
    raw = ' {"model":"原始"}\n'
    frozen = module.freeze_artifact(raw, 1024, raw=True)
    assert frozen == {
        "content": raw,
        "sha256": sha256(raw.encode()).hexdigest(),
        "byteLength": len(raw.encode()),
        "omissionReason": None,
    }
    canonical = module.freeze_artifact({"z": 1, "a": "中文"}, 1024)
    assert canonical["content"] == '{"a":"中文","z":1}'
    assert canonical["sha256"] == sha256(canonical["content"].encode()).hexdigest()


@pytest.mark.parametrize(
    "limit,raw",
    [(4 * 1024 * 1024, False), (2 * 1024 * 1024, True), (512 * 1024, False)],
)
def test_oversize_artifacts_keep_hash_and_size_without_silent_truncation(limit, raw):
    module = repo()
    value = "中" * (limit // 3 + 1)
    frozen = module.freeze_artifact(value, limit, raw=raw)
    assert frozen["content"] is None
    assert frozen["byteLength"] > limit and frozen["omissionReason"] == "SIZE_LIMIT"
    assert len(frozen["sha256"]) == 64


def test_public_summary_is_an_explicit_observation_projection_never_plans_or_raw():
    summary = repo().safe_outcome_summary(
        outcome(
            raw="PRIVATE",
            accepted={
                "sources": [
                    {
                        "id": "s1",
                        "title": "安全观察",
                        "url": "https://example.com",
                        "privatePayload": "PRIVATE",
                        "plans": ["PRIVATE"],
                    }
                ],
                "valueDirections": [
                    {
                        "key": "direction",
                        "title": "观察方向",
                        "thesis": "待核验",
                        "plans": [{"price": 1}],
                        "origin": "PRIVATE",
                    }
                ],
                "events": [],
                "upcoming": [],
                "industryCycleReviews": [],
            },
        )
    )
    assert summary["accepted"]["sources"][0]["title"] == "安全观察"
    assert summary["accepted"]["valueDirections"][0]["title"] == "观察方向"
    assert summary["actionable"] is False
    assert "PRIVATE" not in json.dumps(summary)
    assert "plans" not in json.dumps(summary) and "price" not in json.dumps(summary)


@pytest.mark.parametrize(
    "fields,expected", [({}, True), ({"acceptedContentComplete": False}, False)]
)
def test_safe_summary_preserves_content_completeness_across_projection(
    fields, expected
):
    summary = repo().safe_outcome_summary(outcome(raw="PRIVATE_RAW", **fields))
    assert summary["acceptedContentComplete"] is expected
    projected = repo().safe_outcome_summary(summary)
    assert projected["acceptedContentComplete"] is expected
    assert "PRIVATE_RAW" not in json.dumps(projected)


def test_public_projection_marks_only_actual_observation_excerpts_as_incomplete():
    cases = [
        (
            {
                "events": [
                    {
                        "title": "观察",
                        "watchFor": ["关注"],
                        "plans": ["PRIVATE" * 1000],
                        "privateField": "PRIVATE" * 1000,
                    }
                ]
            },
            True,
        ),
        ({"events": [{"title": "观察", "watchFor": ["字" * 500]}]}, False),
        ({"events": [{"summary": "字" * 1001}]}, False),
        ({"events": [{"watchFor": ["关注"] * 17}]}, False),
        ({"events": [{"title": "观察"}] * 13}, False),
        ({"sources": [{"id": "s1", "quote": "字" * 301}]}, False),
        (
            {
                "valueDirections": [
                    {
                        "key": "observe",
                        "industryCycle": {"evidenceSummary": "字" * 1001},
                    }
                ]
            },
            False,
        ),
    ]
    for accepted, complete in cases:
        summary = repo().safe_outcome_summary(
            outcome(
                accepted=accepted,
                acceptedContentComplete=True,
                decision="COMPLETE",
                formalPublishable=True,
            )
        )
        assert summary["acceptedContentComplete"] is complete
        assert (
            summary["decision"] == "COMPLETE" and summary["formalPublishable"] is True
        )
        assert summary["issueListComplete"] is True
        assert summary["issues"] == []


@pytest.mark.asyncio
async def test_artifact_save_is_insert_once_with_three_bounded_frozen_inputs():
    module = repo()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "run_id": RUN,
        "validated_at": NOW,
        "packet": module.freeze_artifact({"input": "frozen"}, 1024),
        "raw_output": module.freeze_artifact("raw", 1024, raw=True),
        "source_checks": module.freeze_artifact({}, 1024),
    }
    result = await module.save_research_artifacts(
        conn, RUN, {"input": "frozen"}, "raw", {}, NOW
    )
    assert "ON CONFLICT(run_id) DO NOTHING" in conn.execute.await_args.args[0]
    assert result["researchId"] == str(RUN) and result["complete"] is True
    assert len(result["items"]) == 3
    assert all(item["content"] is None for item in result["items"]), (
        "artifact refs must not return private bodies"
    )


@pytest.mark.asyncio
async def test_outcome_over_one_mebibyte_is_rejected_before_insertion():
    conn = AsyncMock()
    with pytest.raises(ValidationError_):
        await repo().save_validation_outcome(
            conn, RUN, outcome(extra="x" * (1024 * 1024))
        )
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_missing_or_modified_artifacts_cannot_call_validator(monkeypatch):
    module = repo()
    read = AsyncMock(
        return_value={
            "researchId": str(RUN),
            "validatedAt": NOW.isoformat(),
            "complete": False,
            "items": [],
        }
    )
    monkeypatch.setattr(module, "read_artifacts_admin", read)
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetchval.side_effect = [{"id": str(uuid4()), "status": "RUNNING"}, True]
    conn.transaction = Mock(return_value=AsyncMock())
    failure = await module.replay_validation(conn, RUN, uuid4(), uuid4(), uuid4(), NOW)
    assert isinstance(failure.error, ValidationError_)
    assert all("INSERT" not in call.args[0] for call in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_missing_checks_and_bounded_metadata_are_not_disguised_as_replayable_raw():
    module = repo()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "run_id": RUN,
        "validated_at": NOW,
        "packet": module.freeze_artifact({}, 1024),
        "raw_output": {
            "content": None,
            "sha256": "a" * 64,
            "byteLength": 3_000_000,
            "omissionReason": "SIZE_LIMIT",
        },
        "source_checks": {
            "content": None,
            "sha256": sha256(b"null").hexdigest(),
            "byteLength": 4,
            "omissionReason": "MISSING",
        },
    }
    result = await module.save_research_artifacts(
        conn,
        RUN,
        {},
        None,
        None,
        NOW,
        raw_metadata={
            "sha256": "a" * 64,
            "byteLength": 3_000_000,
            "omissionReason": "SIZE_LIMIT",
        },
    )
    values = conn.execute.await_args.args
    assert json.loads(values[4])["content"] is None
    assert json.loads(values[4])["omissionReason"] == "SIZE_LIMIT"
    assert json.loads(values[5])["omissionReason"] == "MISSING"
    assert result["complete"] is False


def test_observation_projection_retains_public_quotes_and_direction_risk_without_prices():
    value = outcome(
        accepted={
            "sources": [{"id": "s1", "title": "原文", "quote": "公开摘录"}],
            "valueDirections": [
                {
                    "key": "observe",
                    "title": "观察",
                    "sourceIds": ["s1"],
                    "industryCycle": {
                        "stage": "UNKNOWN",
                        "riskLevel": "UNPROVEN",
                        "evidenceSummary": "待盈利验证",
                        "plans": ["PRIVATE"],
                    },
                }
            ],
        }
    )
    summary = repo().safe_outcome_summary(value)
    assert summary["accepted"]["sources"][0]["quote"] == "公开摘录"
    direction = summary["accepted"]["valueDirections"][0]
    assert direction["sourceIds"] == ["s1"]
    assert direction["industryCycle"]["evidenceSummary"] == "待盈利验证"
    assert "PRIVATE" not in json.dumps(summary)


def test_public_receipt_retains_revision_closed_risk_conclusions_and_macro_overview():
    cycle = {
        "stage": "UNKNOWN",
        "riskLevel": "SUSPECTED",
        "confirmedRiskFlags": ["ORDER_CUTS", "PRIVATE_FLAG"],
        "suspectedRiskFlags": ["PRODUCTION_DELAY"],
        "persistedRiskFlags": ["FINANCING_COOLING"],
        "evidenceGaps": ["STAGE_INDUSTRY_SCOPE_UNVERIFIED", "PRIVATE_GAP"],
        "riskFlags": ["PERSISTED_RISK_WATCH"],
        "privateField": "PRIVATE",
    }
    value = outcome(
        validatorRevision="a" * 64,
        revisionStatus="AVAILABLE",
        accepted={
            "industryCycleReviews": [{"industry": "ROBOTICS", **cycle}],
            "valueDirections": [{"key": "observe", "industryCycle": cycle}],
            "macroOverview": {
                "summary": "宏观观察",
                "macroCycle": "待传导",
                "coverage": [
                    {
                        "topic": "信用",
                        "status": "PENDING",
                        "summary": "关注数据",
                        "private": "PRIVATE",
                    }
                ],
                "verification": "MODEL_UNVERIFIED",
                "sourceIds": ["s1"],
                "plans": ["PRIVATE"],
            },
        },
    )
    summary = repo().safe_outcome_summary(value)
    assert summary["validatorRevision"] == "a" * 64
    assert summary["revisionStatus"] == "AVAILABLE"
    review = summary["accepted"]["industryCycleReviews"][0]
    direction = summary["accepted"]["valueDirections"][0]["industryCycle"]
    for item in (review, direction):
        assert item["confirmedRiskFlags"] == ["ORDER_CUTS"]
        assert item["suspectedRiskFlags"] == ["PRODUCTION_DELAY"]
        assert item["persistedRiskFlags"] == ["FINANCING_COOLING"]
        assert item["evidenceGaps"] == ["STAGE_INDUSTRY_SCOPE_UNVERIFIED"]
    assert summary["accepted"]["macroOverview"]["summary"] == "宏观观察"
    assert "PRIVATE" not in json.dumps(summary)
    assert repo().safe_outcome_summary(summary)["validatorRevision"] == "a" * 64
    value["accepted"]["macroOverview"]["coverage"][0]["summary"] = "字" * 301
    assert repo().safe_outcome_summary(value)["acceptedContentComplete"] is False


def test_public_issue_paths_are_generated_from_closed_components_not_raw_model_fields():
    row = {
        "issueId": "a" * 32,
        "researchId": str(RUN),
        "itemType": "VALUE_DIRECTION",
        "itemIndex": 2,
        "path": "valueDirections[2].PRIVATE_MODEL_FIELD",
        "ruleId": "ACCEPTANCE_SCHEMA_INVALID",
        "reasonCode": "SCHEMA_VALIDATION_FAILED",
        "disposition": "QUARANTINED",
        "message": "PRIVATE_MODEL_MESSAGE",
        "businessKey": "PRIVATE_BUSINESS",
        "dependencies": [{"itemType": "SOURCE", "businessKey": "PRIVATE_DEP"}],
    }
    summary = repo().safe_outcome_summary(outcome(issues=[row]))
    assert summary["issues"][0]["path"] == "valueDirections[2]"
    assert "PRIVATE" not in json.dumps(summary)
    detailed = repo()._issues([row], admin=True)
    assert detailed[0]["path"] == row["path"]
    assert detailed[0]["businessKey"] == row["businessKey"]
    assert detailed[0]["dependencies"] == row["dependencies"]


@pytest.mark.asyncio
async def test_database_admin_rejection_becomes_a_safe_forbidden_response():
    conn = AsyncMock()
    conn.fetchval.side_effect = asyncpg.InsufficientPrivilegeError(
        "PRIVATE_DATABASE_DETAIL"
    )
    with pytest.raises(ForbiddenError, match="管理员"):
        await repo().read_artifacts_admin(conn, RUN)
