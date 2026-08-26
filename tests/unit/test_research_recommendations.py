"""Published recommendations must pass evidence, policy and validity gates."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from backend.research import repository, service


class _QueryConnection:
    def __init__(self) -> None:
        self.sql = ""

    async def fetch(self, sql: str, *_args):
        self.sql = sql
        return []


@pytest.mark.asyncio
async def test_repository_fails_closed_on_expiry_policy_evidence_and_model_status() -> None:
    conn = _QueryConnection()

    await repository.latest_publishable_recommendations(conn, "tenant-1", limit=4)

    assert "rec.valid_until > now()" in conn.sql
    assert "rec.policy_check_status <> 'REJECTED'" in conn.sql
    assert "run.status = 'COMPLETED'" in conn.sql
    assert "EXISTS" in conn.sql and "research.evidence_refs" in conn.sql
    assert "quality_status IN ('UNAVAILABLE', 'CONFLICT')" in conn.sql


@pytest.mark.asyncio
async def test_read_model_maps_only_persisted_candidate_language() -> None:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    rows = [
        {
            "id": "rec-1",
            "recommendation_type": "REDUCE_CANDIDATE",
            "summary": "仓位超过已确认的单标的风险预算，等待用户复核。",
            "counter_case": "若最新财报显著改善，原风险判断可能失效。",
            "confidence": Decimal("0.7200"),
            "policy_check_status": "WARNING",
            "valid_until": now + timedelta(hours=2),
            "created_at": now,
            "instrument_id": "instrument-1",
            "canonical_name": "示例股票",
            "ticker": "600000",
            "evidence_count": 2,
            "evidence_quality": "FRESH",
            "model": "model-a",
            "prompt_template_version": "recommendation/v2",
        }
    ]
    with patch(
        "backend.research.service.repository.latest_publishable_recommendations",
        AsyncMock(return_value=rows),
    ):
        result = await service.get_latest_recommendations(object(), "tenant-1")

    assert result["status"] == "PUBLISHED"
    assert result["items"][0]["action"] == "减仓候选"
    assert result["items"][0]["evidenceCount"] == 2
    assert result["items"][0]["policyCheckStatus"] == "WARNING"
    assert result["qualityStatus"] == "FRESH"


@pytest.mark.asyncio
async def test_no_publishable_recommendation_returns_explicit_abstain() -> None:
    with patch(
        "backend.research.service.repository.latest_publishable_recommendations",
        AsyncMock(return_value=[]),
    ):
        result = await service.get_latest_recommendations(object(), "tenant-1")

    assert result["status"] == "ABSTAIN"
    assert result["reasonCode"] == "NO_GOVERNED_RECOMMENDATION"
    assert result["items"] == []
