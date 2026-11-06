"""Opening a page reads bounded candidates; it never repeats catalog discovery."""

from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.macro import research_opportunities as reader
from tests.unit.test_topic_fund_recommendations import NOW, fund, source, topic
from tests.unit.test_research_delivery import summary


def setup(monkeypatch):
    run_id = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {
        "run_id": run_id,
        "dispatched_at": NOW - timedelta(minutes=5),
        "public_packet": {"asOf": (NOW - timedelta(minutes=5)).isoformat()},
    }
    monkeypatch.setattr(
        reader.acceptance,
        "read_validation_summary",
        AsyncMock(
            return_value=summary(
                researchId=str(run_id), accepted={"sources": [source()]}
            )
        ),
    )
    monkeypatch.setattr(
        reader, "_topics", AsyncMock(return_value=([topic()], {}, "ARCHIVED"))
    )
    return conn, run_id


@pytest.mark.asyncio
async def test_page_cache_miss_reports_pending_without_full_catalog_query(monkeypatch):
    from backend.investment import opportunity_fund_cache as cache
    from backend.investment import opportunity_fund_recall as recall

    conn, run_id = setup(monkeypatch)
    monkeypatch.setattr(cache, "read_cached_topic_funds", AsyncMock(return_value=None))
    discovery = AsyncMock(side_effect=AssertionError("GET cannot discover funds"))
    monkeypatch.setattr(recall, "recall_topic_funds", discovery)
    result = await reader.read_topic_fund_recommendations(conn, run_id, NOW)
    assert result["status"] == "UNAVAILABLE"
    assert result["missingEvidence"] == ["FUND_RECALL_PENDING"]
    discovery.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_hit_enriches_only_current_facts_and_preserves_no_buy_authority(
    monkeypatch,
):
    from backend.investment import opportunity_fund_cache as cache
    from backend.investment import opportunity_fund_recall as recall
    from backend.investment import opportunity_fund_prices as prices

    conn, run_id = setup(monkeypatch)
    cached = {topic()["topicId"]: {"topicId": topic()["topicId"], "funds": [fund()]}}
    monkeypatch.setattr(
        cache, "read_cached_topic_funds", AsyncMock(return_value=cached)
    )
    live = AsyncMock(return_value=cached)
    monkeypatch.setattr(recall, "enrich_topic_funds", live)
    local = AsyncMock(return_value=cached)
    monkeypatch.setattr(prices, "enrich_fund_price_positions", local)
    monkeypatch.setattr(
        recall,
        "recall_topic_funds",
        AsyncMock(side_effect=AssertionError("no discovery on GET")),
    )
    result = await reader.read_topic_fund_recommendations(conn, run_id, NOW)
    assert result["items"][0]["recommendedFunds"][0]["fundCode"] == "123456"
    assert result["items"][0]["recommendedFunds"][0]["canBuyNow"] is False
    assert live.await_args.args[1] == cached and local.await_count == 1
