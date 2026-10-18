"""Real fund identities remain discoverable before instrument registration."""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_search_resolves_unregistered_fund_by_its_actual_code_and_market(
    monkeypatch,
):
    from backend.investment import repository, service

    monkeypatch.setattr(repository, "search_indices", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        repository,
        "screen",
        AsyncMock(
            return_value=(
                [
                    {
                        "instrument_id": None,
                        "fund_code": "000051",
                        "fund_name": "华夏沪深300ETF联接A",
                        "fund_type": "ETF联接基金",
                        "trade_form": "FUND",
                        "market": "FUND_CN",
                        "currency": "CNY",
                    }
                ],
                1,
            )
        ),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "index_id": "hs300",
        "direction_id": "a-share:broad",
        "market_id": "a-share",
        "style_id": "broad",
    }
    result = await service.search(conn, "000051")
    assert result[0]["indexId"] == "hs300"
    assert result[0]["directionId"] == "a-share:broad"
    assert result[0]["instrumentId"] is None
    assert result[0]["market"] == "FUND_CN"
    assert result[0]["latestValue"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [{}, {"fund_code": "000051"}, {"market": "FUND_CN"}])
async def test_unregistered_location_cannot_guess_a_market_or_code(scope):
    from backend.investment.repository import location

    conn = AsyncMock()
    conn.fetchrow.return_value = {"index_id": "hs300"}
    assert await location(conn, None, **scope) is None
    assert not conn.fetchrow.called
