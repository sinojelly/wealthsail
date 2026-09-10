"""天天基金结构化申赎状态适配器的行为契约。"""

from __future__ import annotations

import pytest

from backend.integrations.fund_subscription_status import (
    fetch_fund_subscription_status,
)


class _Frame:
    empty = False
    columns = ["基金代码", "基金简称", "申购状态", "赎回状态", "下一开放日"]

    def iterrows(self):
        yield 0, {
            "基金代码": "110022",
            "基金简称": "易方达消费行业",
            "申购状态": "限制大额申购（单日限购 100000 元）",
            "赎回状态": "开放赎回",
            "下一开放日": "",
        }
        yield 1, {
            "基金代码": "012733",
            "基金简称": "易方达人工智能",
            "申购状态": "开放申购",
            "赎回状态": "开放赎回",
            "下一开放日": None,
        }
        yield 2, {
            "基金代码": "001632",
            "基金简称": "天弘食品饮料",
            "申购状态": "待确认",
            "赎回状态": "开放赎回",
            "下一开放日": None,
        }
        yield 3, {
            "基金代码": "000001",
            "基金简称": "无关基金",
            "申购状态": "暂停申购",
            "赎回状态": "暂停赎回",
            "下一开放日": "2026-10-01",
        }


class _Provider:
    def fund_purchase_em(self) -> _Frame:
        return _Frame()


@pytest.mark.asyncio
async def test_fetch_filters_held_symbols_and_maps_verified_statuses() -> None:
    """状态列表只输出请求基金，未知状态绝不能伪装成开放。"""
    statuses = await fetch_fund_subscription_status(
        ["110022", "012733", "001632"], provider=_Provider()
    )

    assert set(statuses) == {"110022", "012733"}
    assert statuses["110022"].purchase_status == "LIMITED"
    assert statuses["110022"].redemption_status == "OPEN"
    assert statuses["012733"].purchase_status == "OPEN"
    assert statuses["012733"].redemption_status == "OPEN"
    assert statuses["110022"].source_ref == "akshare/fund_purchase_em"
    assert statuses["110022"].metadata == {
        "fundName": "易方达消费行业",
        "purchaseStatusRaw": "限制大额申购（单日限购 100000 元）",
        "redemptionStatusRaw": "开放赎回",
        "nextOpenDate": None,
        "dailyPurchaseLimit": "100000",
    }


@pytest.mark.asyncio
async def test_fetch_returns_empty_mapping_for_missing_or_malformed_source() -> None:
    """接口异常或列结构改变必须降级为空，不能制造基金状态。"""

    class _MalformedProvider:
        def fund_purchase_em(self):
            return type("Frame", (), {"empty": False, "columns": ["基金代码"]})()

    statuses = await fetch_fund_subscription_status(
        ["110022"], provider=_MalformedProvider()
    )

    assert statuses == {}
