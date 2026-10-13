from unittest.mock import AsyncMock

import pytest


def test_value_candidate_filter_does_not_depend_on_technical_score():
    from backend.investment.schemas import ScreenQuery
    from backend.investment.repository import _where
    query = ScreenQuery(valueCandidateOnly=True, performanceOnly=True)
    sql, _ = _where(query)
    assert "value_eligible" in sql and "performance_eligible" in sql
    assert "technical_score" not in sql


@pytest.mark.asyncio
async def test_value_filter_checks_current_mapping_and_independent_freshness():
    from backend.investment.repository import screen
    from backend.investment.schemas import ScreenQuery
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchval.return_value = 0
    await screen(conn, ScreenQuery(valueCandidateOnly=True))
    sql = conn.fetch.await_args.args[0]
    assert "valuationConfig" in sql
    assert "fund_index_links" in sql
    assert "retrievedAt" in sql
    assert "45 days" in sql
    assert "value_eligible" in sql


def test_list_value_unknown_is_incomplete_never_true():
    from backend.investment.service import fund_dto
    result = fund_dto({"instrument_id": None, "fund_code": "123456", "fund_name": "fund",
                       "fund_type": "INDEX", "trade_form": "FUND", "market": "FUND_CN",
                       "currency": "CNY"})
    assert result["valueEligible"] is False
    assert result["valueState"] == "INCOMPLETE"
