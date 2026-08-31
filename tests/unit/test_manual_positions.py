"""Instrument search and manual-initialization boundary tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.common.tenancy import TenantContext
from backend.config import Settings
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


def test_market_master_timeout_has_a_safe_configurable_default() -> None:
    settings = Settings(_env_file=None)
    overridden = Settings(_env_file=None, market_master_timeout_seconds=45)

    assert settings.market_master_timeout_seconds == 30.0
    assert overridden.market_master_timeout_seconds == 45.0


@pytest.mark.asyncio
async def test_live_search_dataset_uses_the_configured_timeout() -> None:
    import pandas as pd

    service._SEARCH_DATASET_CACHE.pop("timeout-config-test", None)
    observed_timeouts: list[float] = []

    async def capture_timeout(awaitable, timeout):
        observed_timeouts.append(timeout)
        return await awaitable

    with patch("backend.portfolio.service.asyncio.wait_for", new=capture_timeout):
        rows = await service._load_search_dataset(
            "timeout-config-test",
            lambda: pd.DataFrame({"代码": ["510300"], "名称": ["沪深300ETF"]}),
            timeout_seconds=30.0,
        )

    assert rows == [{"symbol": "510300", "name": "沪深300ETF"}]
    assert observed_timeouts == [30.0]


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
@pytest.mark.parametrize(
    ("asset_type", "symbol", "provider_name", "expected_market"),
    [
        ("CN_STOCK", "600036", "招商银行", "SSE"),
        ("EXCHANGE_FUND", "510300", "沪深300ETF华泰柏瑞", "SSE"),
    ],
)
async def test_targeted_master_fallback_verifies_listed_instruments_via_sina(
    asset_type: str,
    symbol: str,
    provider_name: str,
    expected_market: str,
) -> None:
    identity = {
        "symbol": symbol,
        "name": provider_name,
        "type": service.INTERNAL_ASSET_TYPE[asset_type],
        "source": "sina/suggest",
    }
    with patch(
        "backend.portfolio.service.market_data_adapter.get_sina_instrument_identity",
        AsyncMock(return_value=identity),
    ) as get_identity:
        verified = await service._verify_authoritative_instrument(
            symbol,
            asset_type,
            timeout_seconds=5.0,
        )

    assert verified == {
        "symbol": symbol,
        "name": provider_name,
        "market": expected_market,
        "type": service.INTERNAL_ASSET_TYPE[asset_type],
        "source": "sina/suggest",
    }
    get_identity.assert_awaited_once_with(
        symbol, service.INTERNAL_ASSET_TYPE[asset_type]
    )


@pytest.mark.asyncio
async def test_targeted_master_fallback_rejects_exchange_asset_type_mismatch() -> None:
    with patch(
        "backend.portfolio.service.market_data_adapter.get_sina_instrument_identity",
        AsyncMock(),
    ) as get_identity:
        verified = await service._verify_authoritative_instrument(
            "510300",
            "CN_STOCK",
            timeout_seconds=5.0,
        )

    assert verified is None
    get_identity.assert_not_awaited()


@pytest.mark.asyncio
async def test_targeted_master_rejects_convertible_bond_submitted_as_etf() -> None:
    with patch(
        "backend.portfolio.service.market_data_adapter.get_sina_instrument_identity",
        AsyncMock(return_value=None),
    ):
        verified = await service._verify_authoritative_instrument(
            "123001",
            "EXCHANGE_FUND",
            timeout_seconds=5.0,
        )

    assert verified is None


@pytest.mark.asyncio
async def test_targeted_master_fallback_verifies_fund_via_direct_provider() -> None:
    import pandas as pd

    provider = SimpleNamespace(
        fund_individual_basic_info_xq=lambda symbol, timeout: pd.DataFrame(
            {
                "item": ["基金代码", "基金名称"],
                "value": [symbol, "易方达消费行业"],
            }
        )
    )
    with patch.dict(sys.modules, {"akshare": provider}):
        verified = await service._verify_authoritative_instrument(
            "110022",
            "OFF_EXCHANGE_FUND",
            timeout_seconds=5.0,
        )

    assert verified == {
        "symbol": "110022",
        "name": "易方达消费行业",
        "market": "FUND_CN",
        "type": "FUND",
        "source": "danjuan/fund-basic",
    }


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
