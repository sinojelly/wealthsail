"""Instrument search and manual-initialization boundary tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.common.tenancy import TenantContext
from backend.errors import ForbiddenError, ValidationError_
from backend.portfolio import service


def test_normalize_symbol_accepts_known_suffixes_but_rejects_trailing_junk() -> None:
    assert service.normalize_symbol("600519") == "600519"
    assert service.normalize_symbol("600519.SH") == "600519"
    assert service.normalize_symbol("000001.SZ") == "000001"
    assert service.normalize_symbol("920001.BJ") == "920001"

    for invalid in ("600519junk", "600519.SZ.more", "12345", "茅台"):
        with pytest.raises(ValidationError_):
            service.normalize_symbol(invalid)


def test_primary_market_maps_beijing_symbols_without_colliding_with_chinext() -> None:
    assert service._primary_market("CN_STOCK", "600519") == "SSE"
    assert service._primary_market("CN_STOCK", "300750") == "SZSE"
    assert service._primary_market("CN_STOCK", "920001") == "BSE"
    assert service._primary_market("OFF_EXCHANGE_FUND", "110022") == "FUND_CN"


@asynccontextmanager
async def _fake_tx(_pool, _user_id, _tenant_id):
    yield MagicMock()


@pytest.mark.asyncio
async def test_search_merges_local_and_live_results_by_market_symbol_and_type() -> None:
    ctx = TenantContext("user-1", "tenant-1", tenant_role="VIEWER")
    local_row = {
        "ticker": "510300",
        "canonical_name": "沪深300ETF",
        "primary_market": "SSE",
        "asset_type": "EXCHANGE_FUND",
    }
    with (
        patch("backend.portfolio.service.tenant_transaction", new=_fake_tx),
        patch(
            "backend.portfolio.service.repository.search_instruments_local",
            AsyncMock(return_value=[local_row]),
        ),
        patch(
            "backend.portfolio.service._ak_search",
            AsyncMock(
                return_value=[
                    {
                        "symbol": "510300",
                        "name": "供应商名称",
                        "market": "SSE",
                        "type": "ETF",
                    },
                    {
                        "symbol": "000300",
                        "name": "沪深300",
                        "market": "SZSE",
                        "type": "STOCK",
                    },
                ]
            ),
        ),
    ):
        data = await service.search_instruments(MagicMock(), ctx, "沪深300")

    assert data["total"] == 2
    by_symbol = {item["symbol"]: item for item in data["items"]}
    assert by_symbol["510300"]["name"] == "沪深300ETF"
    assert by_symbol["000300"]["type"] == "STOCK"


@pytest.mark.asyncio
async def test_live_search_dataset_is_singleflight_cached_across_keywords() -> None:
    import pandas as pd

    service._SEARCH_DATASET_CACHE.clear()
    calls = 0

    def fetcher():
        nonlocal calls
        calls += 1
        return pd.DataFrame({"代码": ["600519"], "名称": ["贵州茅台"]})

    first = await service._load_search_dataset("stocks-test", fetcher)
    second = await service._load_search_dataset("stocks-test", fetcher)

    assert first == second == [{"symbol": "600519", "name": "贵州茅台"}]
    assert calls == 1


@pytest.mark.asyncio
async def test_fund_master_verification_accepts_fund_specific_provider_columns() -> (
    None
):
    import pandas as pd

    service._SEARCH_DATASET_CACHE.pop("fund_name_em", None)
    provider = SimpleNamespace(
        fund_name_em=lambda: pd.DataFrame(
            {
                "基金代码": ["110022", "000001"],
                "基金简称": ["权威基金一", "权威基金二"],
            }
        )
    )
    with patch.dict(sys.modules, {"akshare": provider}):
        verified = await service.verify_fund_master_names(["110022", "999999"])

    assert verified == {"110022": "权威基金一"}


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["create", "confirm"])
async def test_viewer_cannot_mutate_portfolio_imports(command: str) -> None:
    ctx = TenantContext("viewer-1", "tenant-1", tenant_role="VIEWER")
    payload = SimpleNamespace(
        symbol="600519",
        name="贵州茅台",
        assetType="STOCK",
        quantity="100",
        avgCost="1500.00",
        buyDate=None,
        accountId=None,
    )

    with pytest.raises(ForbiddenError):
        if command == "create":
            await service.create_position_import(
                MagicMock(), ctx, payload, idempotency_key="create-1"
            )
        else:
            await service.confirm_position_import(
                MagicMock(), ctx, "batch-1", idempotency_key="confirm-1"
            )
