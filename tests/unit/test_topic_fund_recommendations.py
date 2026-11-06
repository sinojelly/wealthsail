"""News topics retain concrete fund recommendations without an audit veto."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.errors import NotFoundError
from backend.macro import research_opportunities as reader
from tests.unit.test_research_delivery import NOW, summary


def topic(key="chips", **changes):
    return {
        "topicId": "topic:" + key.encode().hex().ljust(32, "0")[:32],
        "key": key,
        "title": "芯片政策带来设备需求机会",
        "summary": "新闻中的设备采购支持芯片产业需求",
        "status": "PENDING_REVIEW",
        "sourceIds": ["s1"],
        "match": {"industryIds": ["SEMICONDUCTOR_AI"], "scopeVerified": True},
        **changes,
    }


def fund(score=60):
    return {
        "fundCode": "123456",
        "name": "测试芯片ETF联接C",
        "matchLevel": "DYNAMIC_MATCH",
        "matchReason": "按芯片主题匹配真实基金主库",
        "quantitativeScore": score,
        "subscription": {"status": "UNKNOWN", "restricted": None},
        "missingEvidence": ["SUBSCRIPTION_STATUS_UNKNOWN"],
        "actionable": False,
    }


def source():
    return {
        "id": "s1",
        "title": "芯片政策",
        "url": "https://example.com/chip",
        "retrievedAt": NOW.isoformat(),
        "verification": "SOURCE_CHECKED",
    }


@pytest.mark.asyncio
async def test_empty_formal_directions_and_missing_search_receipt_still_recommend(
    monkeypatch,
):
    run_id = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {
        "run_id": run_id,
        "dispatched_at": NOW - timedelta(minutes=5),
        "public_packet": {
            "asOf": (NOW - timedelta(minutes=5)).isoformat(),
            "discoveryUniverse": [],
            "quantitativeScan": {"items": []},
        },
    }
    monkeypatch.setattr(
        reader.acceptance,
        "read_validation_summary",
        AsyncMock(
            return_value=summary(
                researchId=str(run_id),
                accepted={"sources": [source()], "valueDirections": []},
            )
        ),
    )
    monkeypatch.setattr(
        reader, "_topics", AsyncMock(return_value=([topic()], {}, "ARCHIVED"))
    )
    calls = []

    async def recall(connection, topics, packet, now):
        calls.append((topics, now))
        return {topics[0]["topicId"]: {"funds": [fund()], "status": "MATCHED"}}

    function = getattr(reader, "read_topic_fund_recommendations", None)
    assert callable(function), "topic-to-fund recommendation reader missing"
    result = await function(conn, run_id, NOW, _recall=recall)
    item = result["items"][0]
    assert item["recommendedFunds"][0]["fundCode"] == "123456"
    assert item["riskStatus"] == "UNVERIFIED_RISK"
    assert "RISK_AUDIT_INCOMPLETE" in item["missingEvidence"]
    assert item["recommendationStatus"] == "MATCHED"
    assert item["actionable"] is False
    assert calls and result["coverage"]["matchedTopicCount"] == 1
    conn.execute.assert_not_awaited()


def project(
    topics, recalls, *, reviews=(), coverage=None, packet=None, sources=None, now=NOW
):
    function = getattr(reader, "project_topic_recommendations", None)
    assert callable(function), "risk annotation and recommendation ranking missing"
    return function(
        topics,
        recalls,
        sources=[source()] if sources is None else sources,
        reviews=reviews,
        coverage=coverage or {},
        packet=packet or {},
        validated_at=NOW,
        now=now,
    )


def test_new_unregistered_scope_keeps_fund_instead_of_identity_veto():
    row = topic(match={"industryIds": [], "unresolvedClaims": ["SCI_TECH_CHIP"]})
    result = project([row], {row["topicId"]: {"funds": [fund()]}})
    assert len(result) == 1 and len(result[0]["recommendedFunds"]) == 1
    assert result[0]["riskStatus"] == "UNVERIFIED_RISK"
    assert result[0]["candidateDirection"]["origin"] == "NEWS_TOPIC"
    assert result[0]["candidateDirection"]["thesis"] == row["summary"]


def test_negative_news_remains_visible_with_penalty_and_fund():
    row = topic()
    review = {
        "industry": "SEMICONDUCTOR_AI",
        "riskLevel": "CONFIRMED",
        "riskFlags": ["ORDER_CUTS"],
        "sourceIds": ["s1"],
        "riskChecks": [
            {
                "kind": "ORDER_CUTS",
                "status": "FOUND",
                "summary": "下游客户削减订单",
                "sourceIds": ["s1"],
            }
        ],
    }
    result = project([row], {row["topicId"]: {"funds": [fund()]}}, reviews=[review])
    item = result[0]
    assert item["riskStatus"] == "RISK_ALERT"
    assert "下游客户削减订单" in item["riskWarnings"]
    assert item["riskScorePenalty"] == 20
    assert item["recommendedFunds"][0]["quantitativeScore"] == 40
    assert item["recommendedFunds"][0]["unadjustedQuantitativeScore"] == 60


def test_complete_audit_annotates_safe_without_claiming_immediate_buy():
    from backend.macro.industry_cycle import RISK_CHECKS

    row = topic()
    review = {
        "industry": "SEMICONDUCTOR_AI",
        "riskLevel": "NONE",
        "riskChecks": [
            {"kind": kind, "status": "NOT_FOUND", "searchExecutionVerified": True}
            for kind in RISK_CHECKS
        ],
    }
    result = project(
        [row],
        {row["topicId"]: {"funds": [fund()]}},
        reviews=[review],
        coverage={"searchExecutionVerified": True},
        packet={
            "industryCycleResearch": {"candidates": [{"industry": "SEMICONDUCTOR_AI"}]}
        },
    )
    assert result[0]["riskStatus"] == "VERIFIED_SAFE"
    assert result[0]["recommendedFunds"][0]["actionable"] is False


def test_whole_catalog_rank_uses_funds_then_evidence_then_score_then_audit():
    unmatched, low, high, backed = [
        topic(key) for key in ("unmatched", "low", "high", "backed")
    ]
    backed["sourceIds"] = ["s1", "s2"]
    recalls = {
        row["topicId"]: {"funds": [fund(score)]}
        for row, score in ((low, 30), (high, 80), (backed, 20))
    }
    sources = [source(), {**source(), "id": "s2"}]
    result = project([unmatched, low, high, backed], recalls, sources=sources)
    assert [row["key"] for row in result] == ["backed", "high", "low", "unmatched"]


def test_unchecked_sources_never_turn_risk_badge_green_and_quarantine_excluded():
    row = topic()
    invalid = topic("invalid", status="QUARANTINED")
    original = deepcopy(row)
    result = project([row, invalid], {row["topicId"]: {"funds": [fund()]}}, sources=[])
    assert len(result) == 1
    assert result[0]["riskStatus"] == "UNVERIFIED_RISK"
    assert result[0]["recommendationStatus"] == "SOURCE_PENDING"
    assert result[0]["recommendedFunds"]
    assert row == original


@pytest.mark.asyncio
async def test_invisible_run_never_queries_fund_catalog_or_dispatch_packet(monkeypatch):
    conn = AsyncMock()
    conn.fetchval.return_value = True
    monkeypatch.setattr(
        reader.acceptance, "read_validation_summary", AsyncMock(return_value=None)
    )
    function = getattr(reader, "read_topic_fund_recommendations", None)
    assert callable(function), "topic-to-fund recommendation reader missing"
    with pytest.raises(NotFoundError):
        await function(conn, uuid4(), NOW, _recall=AsyncMock())
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_archive_does_not_masquerade_as_no_funds(monkeypatch):
    conn = AsyncMock()
    conn.fetchval.return_value = True
    run_id = uuid4()
    monkeypatch.setattr(
        reader.acceptance,
        "read_validation_summary",
        AsyncMock(
            return_value=summary(researchId=str(run_id), archiveState="AUDIT_PENDING")
        ),
    )
    recall = AsyncMock()
    result = await reader.read_topic_fund_recommendations(
        conn, run_id, NOW, _recall=recall
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["missingEvidence"] == ["TOPIC_ARCHIVE_PENDING"]
    recall.assert_not_awaited()
    conn.fetchrow.assert_not_awaited()


def test_expired_historical_risk_is_not_rebroadcast_as_current_alert():
    row = topic()
    history = {
        "industry": "SEMICONDUCTOR_AI",
        "researchedAt": (NOW - timedelta(days=15)).isoformat(),
        "industryCycle": {"riskLevel": "CONFIRMED", "riskFlags": ["ORDER_CUTS"]},
    }
    result = project(
        [row],
        {row["topicId"]: {"funds": [fund()]}},
        packet={"previousIndustryRiskStates": [history]},
    )
    assert result[0]["riskStatus"] == "UNVERIFIED_RISK"
    assert result[0]["riskScorePenalty"] == 0


@pytest.mark.parametrize("boundary", ["stale", "mixed_identity"])
def test_old_or_partially_resolved_audit_cannot_mark_current_recommendation_safe(
    boundary,
):
    from backend.macro.industry_cycle import RISK_CHECKS

    row = topic()
    if boundary == "mixed_identity":
        row["match"].update(
            unresolvedClaims=["new-theme"],
            proposedScopes=[{"industryId": "TOPIC_SCOPE:" + "a" * 32}],
        )
    review = {
        "industry": "SEMICONDUCTOR_AI",
        "riskLevel": "NONE",
        "riskChecks": [
            {"kind": kind, "status": "NOT_FOUND", "searchExecutionVerified": True}
            for kind in RISK_CHECKS
        ],
    }
    result = project(
        [row],
        {row["topicId"]: {"funds": [fund()]}},
        reviews=[review],
        coverage={"searchExecutionVerified": True},
        packet={
            "industryCycleResearch": {"candidates": [{"industry": "SEMICONDUCTOR_AI"}]}
        },
        now=NOW + timedelta(days=120) if boundary == "stale" else NOW,
    )
    assert result[0]["riskStatus"] == "UNVERIFIED_RISK"
    assert result[0]["recommendedFunds"]
    if boundary == "stale":
        assert "STALE_RISK_AUDIT" in result[0]["missingEvidence"]


def test_current_risk_penalty_does_not_double_count_existing_quantitative_penalty():
    row = topic()
    scored = {**fund(50), "quantitativeRiskPenaltyApplied": 10}
    review = {
        "industry": "SEMICONDUCTOR_AI",
        "riskLevel": "CONFIRMED",
        "riskFlags": ["ORDER_CUTS"],
    }
    result = project([row], {row["topicId"]: {"funds": [scored]}}, reviews=[review])
    assert result[0]["recommendedFunds"][0]["quantitativeScore"] == 40
    assert result[0]["recommendedFunds"][0]["unadjustedQuantitativeScore"] == 60


def test_persisted_negative_summary_reaches_recommendation_warning():
    from backend.macro.research_acceptance_repository import safe_outcome_summary
    from tests.unit.test_research_acceptance_repository import outcome

    row = topic()
    review = {
        "industry": "SEMICONDUCTOR_AI",
        "riskLevel": "CONFIRMED",
        "riskFlags": ["ORDER_CUTS"],
        "sourceIds": ["s1"],
        "riskChecks": [
            {
                "kind": "ORDER_CUTS",
                "status": "FOUND",
                "summary": "下游客户削减订单",
                "sourceIds": ["s1"],
            }
        ],
    }
    stored = safe_outcome_summary(outcome(accepted={"industryCycleReviews": [review]}))
    reloaded = safe_outcome_summary(stored)["accepted"]["industryCycleReviews"]
    result = project([row], {row["topicId"]: {"funds": [fund()]}}, reviews=reloaded)
    assert "下游客户削减订单" in result[0]["riskWarnings"]
