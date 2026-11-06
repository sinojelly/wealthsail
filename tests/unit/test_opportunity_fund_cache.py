"""Candidate matching belongs to the archive worker; GET only reads bounded identities."""

from datetime import datetime, timezone
from importlib import import_module
from importlib.util import find_spec
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
TOPIC = "topic:" + "a" * 32


def cache_module():
    assert find_spec("backend.investment.opportunity_fund_cache"), (
        "Archived candidates need durable versioned storage, not request-time matching"
    )
    return import_module("backend.investment.opportunity_fund_cache")


def candidates():
    return {
        TOPIC: {
            "topicId": TOPIC,
            "status": "MATCHED",
            "funds": [
                {
                    "fundCode": "000001",
                    "name": "测试半导体A",
                    "subjectKind": "nav",
                    "subjectId": "000001",
                    "market": "FUND_CN",
                    "associationLevel": "NAME_CANDIDATE",
                    "matchReason": "名称包含半导体",
                    "fundGroupKey": "测试半导体",
                    "canBuyNow": True,
                    "purchaseStatus": "OPEN",
                    "quantitativeScore": 99,
                }
            ],
            "indirectFunds": [],
        }
    }


def test_cache_never_persists_mutable_price_subscription_or_execution_state():
    mod = cache_module()
    body = mod.prepare_cached_candidates(candidates(), [TOPIC])
    fund = body[TOPIC]["funds"][0]
    assert fund["fundCode"] == "000001"
    assert not {"canBuyNow", "purchaseStatus", "quantitativeScore"} & fund.keys()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value[TOPIC]["funds"][0].update(fundCode="bad"),
        lambda value: value.update({"private-topic": value[TOPIC]}),
        lambda value: value[TOPIC].update(funds=[value[TOPIC]["funds"][0]] * 97),
    ],
)
def test_cache_rejects_unbounded_or_unowned_candidate_material(mutation):
    mod = cache_module()
    value = candidates()
    mutation(value)
    with pytest.raises(ValueError):
        mod.prepare_cached_candidates(value, [TOPIC])


@pytest.mark.asyncio
async def test_cache_miss_has_no_matcher_or_catalog_scan_side_effect():
    mod = cache_module()
    conn = type("Conn", (), {"fetchrow": AsyncMock(return_value=None)})()
    assert (
        await mod.read_cached_topic_funds(conn, uuid4(), NOW, [{"topicId": TOPIC}], NOW)
        is None
    )
    sql = conn.fetchrow.await_args.args[0]
    assert "ILIKE" not in sql and "unnest" not in sql
    assert "catalog_revision" in sql and "content_digest" in sql


@pytest.mark.asyncio
async def test_background_batch_is_independent_of_economic_archive_success(monkeypatch):
    mod = import_module("backend.jobs.research_archive")
    archive = AsyncMock(return_value={"auditStatus": "AUDIT_PENDING", "claimed": False})
    cached = AsyncMock(return_value={"claimed": True, "status": "READY"})
    monkeypatch.setattr(mod, "archive_research_run", archive)
    monkeypatch.setattr(mod, "precompute_topic_funds", cached, raising=False)
    await mod.sync_research_archives(object(), now=NOW)
    assert cached.await_count >= 1, (
        "Already archived runs must be backfilled without a GET"
    )


@pytest.mark.asyncio
async def test_cache_status_exposes_exhausted_retry_without_private_lease_material():
    mod = cache_module()
    assert hasattr(mod, "read_topic_fund_cache_status"), (
        "Retry exhaustion must stop bounded UI polling"
    )
    conn = type("Conn", (), {"fetchval": AsyncMock(return_value="FAILED")})()
    assert await mod.read_topic_fund_cache_status(conn, uuid4(), NOW, NOW) == "FAILED"
    sql = conn.fetchval.await_args.args[0]
    assert "lease_token" not in sql and "ILIKE" not in sql
