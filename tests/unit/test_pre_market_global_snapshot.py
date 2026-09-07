"""Unit tests for the pre-market global overnight snapshot (P0: US + A50).

All external providers are mocked so the suite runs offline and never hits the
network. The contract is fail-closed: any outage, timeout or parse error must
degrade to empty data (rendered as 暂无), never to fabricated numbers.
"""

from __future__ import annotations

from datetime import datetime
import re
import types
import unittest.mock as mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backend.integrations import market_data


def _boom(msg: str):
    """Helper that raises on call (for simulating AkShare macro outages)."""
    raise RuntimeError(msg)


def _fake_us_sina_df(prev: float, latest: float) -> pd.DataFrame:
    """``index_us_stock_sina`` 返回形态：date/open/high/low/close/volume/amount。"""
    return pd.DataFrame(
        [
            {
                "date": "2026-08-31",
                "open": 0,
                "high": 0,
                "low": 0,
                "close": prev,
                "volume": 0,
                "amount": 0,
            },
            {
                "date": "2026-09-01",
                "open": 0,
                "high": 0,
                "low": 0,
                "close": latest,
                "volume": 0,
                "amount": 0,
            },
        ]
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    market_data._reset_pre_market_global_cache()
    yield
    market_data._reset_pre_market_global_cache()


@pytest.mark.asyncio
async def test_fetch_us_indices_returns_latest_change_pct() -> None:
    # index_us_stock_sina 接受 symbol 参数，这里忽略 symbol 返回同一份样例。
    # 前收 100 -> 最新 101.23，涨跌幅应为 (101.23-100)/100*100 = 1.23%。
    fake_ak = types.SimpleNamespace(
        index_us_stock_sina=lambda symbol: _fake_us_sina_df(100.0, 101.23)
    )
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = await market_data.fetch_us_overnight_indices()
    names = {item["name"] for item in items}
    assert names == {"道琼斯", "纳斯达克", "标普500"}
    for item in items:
        assert item["changePct"] == pytest.approx(1.23)
        assert item["price"] == pytest.approx(101.23)
        assert item["sourceAsOf"] == "2026-09-01"


@pytest.mark.asyncio
async def test_fetch_us_indices_handles_negative_change() -> None:
    # 前收 100 -> 最新 99.5，跌 0.5%。
    fake_ak = types.SimpleNamespace(
        index_us_stock_sina=lambda symbol: _fake_us_sina_df(100.0, 99.5)
    )
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = await market_data.fetch_us_overnight_indices()
    assert len(items) == 3
    assert all(item["changePct"] == pytest.approx(-0.5) for item in items)


@pytest.mark.asyncio
async def test_fetch_us_indices_degrades_on_akshare_error() -> None:
    with mock.patch.object(
        market_data, "_ak_import", side_effect=RuntimeError("akshare down")
    ):
        items = await market_data.fetch_us_overnight_indices()
    assert items == []


@pytest.mark.asyncio
async def test_fetch_us_indices_degrades_on_unexpected_columns() -> None:
    fake_ak = types.SimpleNamespace(
        index_us_stock_sina=lambda symbol: pd.DataFrame([{"foo": 1}])
    )
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = await market_data.fetch_us_overnight_indices()
    assert items == []


def _make_sina_client(payload):
    """Build a stand-in for httpx.AsyncClient that returns/raises ``payload``.

    ``payload`` is either a GBK string body (success) or a real exception
    instance (e.g. ``httpx.HTTPError``) to raise on ``.get``. Only the
    ``AsyncClient`` attribute is mocked; the real ``httpx`` module (and thus
    ``httpx.HTTPError``) stays intact.
    """

    class _Resp:
        def __init__(self, text: str):
            self.content = text.encode("gbk", errors="replace")

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            if isinstance(payload, Exception):
                raise payload
            return _Resp(payload)

    return lambda *args, **kwargs: _Client()


# 新浪 hf_CHA50CFD 真实报文（GBK 解码后），字段布局见 _fetch_sina_a50 文档字符串。
_SINA_A50_RAW = (
    'var hq_str_hf_CHA50CFD="14534.910,,14534.000,14536.000,14568.000,14534.000,'
    '18:09:19,14567.000,14563.000,820298,52,33,2026-09-02,富时中国A50期货,7422";'
)


@pytest.mark.asyncio
async def test_fetch_sina_a50_parses_change_pct() -> None:
    with mock.patch.object(
        market_data.httpx, "AsyncClient", new=_make_sina_client(_SINA_A50_RAW)
    ):
        result = await market_data._fetch_sina_a50()
    assert result is not None
    assert result["name"] == "富时中国A50期货"
    # 涨跌幅以昨结算价（fields[7]=14567.0）为基准。
    assert result["changePct"] == pytest.approx(
        (14534.910 - 14567.000) / 14567.000 * 100, rel=1e-6
    )
    assert result["price"] == pytest.approx(14534.910)
    assert result["source"] == "sina/a50"
    assert result["sourceAsOf"] == "2026-09-02T18:09:19+08:00"


@pytest.mark.asyncio
async def test_fetch_sina_a50_degrades_on_empty_payload() -> None:
    # 旧 nfSA0 接口已失效（返回空串）；同等空响应必须降级而非报错。
    raw = 'var hq_str_hf_CHA50CFD="";'
    with mock.patch.object(
        market_data.httpx, "AsyncClient", new=_make_sina_client(raw)
    ):
        result = await market_data._fetch_sina_a50()
    assert result is None


@pytest.mark.asyncio
async def test_fetch_sina_a50_degrades_on_short_payload() -> None:
    raw = 'var hq_str_hf_CHA50CFD="富时中国A50";'  # only one field -> unusable
    with mock.patch.object(
        market_data.httpx, "AsyncClient", new=_make_sina_client(raw)
    ):
        result = await market_data._fetch_sina_a50()
    assert result is None


@pytest.mark.asyncio
async def test_fetch_sina_a50_degrades_on_http_error() -> None:
    with mock.patch.object(
        market_data.httpx,
        "AsyncClient",
        new=_make_sina_client(market_data.httpx.HTTPError("timeout")),
    ):
        result = await market_data._fetch_sina_a50()
    assert result is None


def _em_spot_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=[
            "序号",
            "代码",
            "名称",
            "最新价",
            "涨跌额",
            "涨跌幅",
            "今开",
            "最高",
            "最低",
            "昨结",
            "成交量",
            "买盘",
            "卖盘",
            "持仓量",
        ],
    )


@pytest.mark.asyncio
async def test_fetch_a50_via_em_parses_cn00y() -> None:
    df = _em_spot_df(
        [
            {"代码": "HG00Y", "名称": "COMEX铜", "最新价": 4.5, "涨跌幅": 0.3},
            {
                "代码": "CN00Y",
                "名称": "A50期指当月连续",
                "最新价": 14537.0,
                "涨跌幅": -0.21,
            },
        ]
    )
    fake_ak = types.SimpleNamespace(futures_global_spot_em=lambda: df)
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        result = await market_data._fetch_a50_via_em()
    assert result is not None
    assert result["name"] == "A50期指当月连续"
    assert result["changePct"] == pytest.approx(-0.21)
    assert result["price"] == pytest.approx(14537.0)
    assert result["source"] == "akshare/a50-em"


@pytest.mark.asyncio
async def test_fetch_a50_via_em_degrades_when_symbol_missing() -> None:
    df = _em_spot_df([{"代码": "HG00Y", "名称": "COMEX铜", "涨跌幅": 0.3}])
    fake_ak = types.SimpleNamespace(futures_global_spot_em=lambda: df)
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        result = await market_data._fetch_a50_via_em()
    assert result is None


@pytest.mark.asyncio
async def test_fetch_a50_via_em_degrades_on_error() -> None:
    fake_ak = types.SimpleNamespace(futures_global_spot_em=lambda: _boom("em down"))
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        result = await market_data._fetch_a50_via_em()
    assert result is None


@pytest.mark.asyncio
async def test_fetch_a50_future_falls_back_to_em_when_sina_empty() -> None:
    """新浪空报文 -> 自动降级东财 CN00Y。"""
    df = _em_spot_df(
        [
            {
                "代码": "CN00Y",
                "名称": "A50期指当月连续",
                "最新价": 14537.0,
                "涨跌幅": -0.21,
            }
        ]
    )
    fake_ak = types.SimpleNamespace(futures_global_spot_em=lambda: df)
    with (
        mock.patch.object(
            market_data.httpx,
            "AsyncClient",
            new=_make_sina_client('var hq_str_hf_CHA50CFD="";'),
        ),
        mock.patch.object(market_data, "_ak_import", return_value=fake_ak),
    ):
        result = await market_data.fetch_a50_future()
    assert result is not None
    assert result["source"] == "akshare/a50-em"
    assert result["changePct"] == pytest.approx(-0.21)


@pytest.mark.asyncio
async def test_snapshot_populates_us_and_a50() -> None:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    us = [
        {
            "name": "道琼斯",
            "changePct": 1.0,
            "price": 35000.0,
            "sourceAsOf": today,
        }
    ]
    a50 = {
        "name": "富时中国A50期货",
        "changePct": 0.5,
        "price": 13000.0,
        "source": "sina/a50",
        "sourceAsOf": f"{today}T08:30:00+08:00",
    }
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(return_value=us),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=a50)
        ),
        mock.patch.object(
            market_data, "fetch_economic_calendar", new=mock.AsyncMock(return_value=[])
        ),
    ):
        snap = await market_data.get_pre_market_global_snapshot()
    assert snap["usIndices"] == [{**us[0], "qualityStatus": "FRESH"}]
    assert snap["a50Future"] == {**a50, "qualityStatus": "FRESH"}
    assert snap["qualityStatus"] == "FRESH"
    assert "AkShare-OpenSource" in snap["sourceName"]
    assert snap["economicEvents"] == []  # P1 not yet wired in this test


@pytest.mark.asyncio
async def test_snapshot_quality_uses_actual_market_source_date(monkeypatch) -> None:
    """A quote older than the prior trading day must not be labelled fresh."""

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = cls(2026, 9, 8, 0, 30, tzinfo=ZoneInfo("UTC"))
            return current.astimezone(tz) if tz is not None else current

    us = [
        {
            "name": "道琼斯",
            "changePct": 1.0,
            "price": 35000.0,
            "sourceAsOf": "2026-09-04",
        }
    ]
    monkeypatch.setattr(market_data, "datetime", FixedDatetime)
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(return_value=us),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=None)
        ),
        mock.patch.object(
            market_data, "fetch_economic_calendar", new=mock.AsyncMock(return_value=[])
        ),
    ):
        snap = await market_data.get_pre_market_global_snapshot()

    assert snap["usIndices"][0]["qualityStatus"] == "STALE"
    assert snap["qualityStatus"] == "STALE"
    assert snap["sourceAsOf"] == "2026-09-04"


@pytest.mark.asyncio
async def test_snapshot_caches_within_ttl() -> None:
    us = [{"name": "道琼斯", "changePct": 1.0, "price": 1.0}]
    fetch_us = mock.AsyncMock(return_value=us)
    fetch_a50 = mock.AsyncMock(return_value=None)
    with (
        mock.patch.object(market_data, "fetch_us_overnight_indices", new=fetch_us),
        mock.patch.object(market_data, "fetch_a50_future", new=fetch_a50),
        mock.patch.object(
            market_data, "fetch_economic_calendar", new=mock.AsyncMock(return_value=[])
        ),
    ):
        first = await market_data.get_pre_market_global_snapshot()
        second = await market_data.get_pre_market_global_snapshot()
    assert first == second
    assert first is not second  # callers cannot mutate the cache's baseline object
    assert fetch_us.await_count == 1  # fetched only once


@pytest.mark.asyncio
async def test_snapshot_degrades_to_unavailable_when_all_empty() -> None:
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(return_value=[]),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=None)
        ),
        mock.patch.object(
            market_data, "fetch_economic_calendar", new=mock.AsyncMock(return_value=[])
        ),
    ):
        snap = await market_data.get_pre_market_global_snapshot()
    assert snap["usIndices"] == []
    assert snap["a50Future"] is None
    assert snap["qualityStatus"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_snapshot_degrades_to_unavailable_on_fetch_exception() -> None:
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(side_effect=RuntimeError("boom")),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=None)
        ),
        mock.patch.object(
            market_data, "fetch_economic_calendar", new=mock.AsyncMock(return_value=[])
        ),
    ):
        snap = await market_data.get_pre_market_global_snapshot()
    assert snap["qualityStatus"] == "UNAVAILABLE"
    assert snap["usIndices"] == []


@pytest.mark.asyncio
async def test_snapshot_falls_back_to_stale_cache_when_expired() -> None:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    good_us = [
        {
            "name": "道琼斯",
            "changePct": 1.0,
            "price": 1.0,
            "sourceAsOf": today,
        }
    ]
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(return_value=good_us),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=None)
        ),
        mock.patch.object(
            market_data, "fetch_economic_calendar", new=mock.AsyncMock(return_value=[])
        ),
    ):
        cached = await market_data.get_pre_market_global_snapshot()
        assert cached["qualityStatus"] == "FRESH"
    # Expire the cache, then make every live fetch fail: should degrade to the
    # previously cached snapshot, relabelled STALE.
    market_data._pre_market_global_cache["ts"] = 0.0
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(side_effect=RuntimeError("down")),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=None)
        ),
        mock.patch.object(
            market_data, "fetch_economic_calendar", new=mock.AsyncMock(return_value=[])
        ),
    ):
        degraded = await market_data.get_pre_market_global_snapshot()
    assert degraded["usIndices"] == [{**good_us[0], "qualityStatus": "FRESH"}]
    assert degraded["qualityStatus"] == "STALE"


@pytest.mark.asyncio
async def test_expired_cache_reclassifies_quotes_older_than_one_trading_day(
    monkeypatch,
) -> None:
    """Fallback must not retain an item's historical FRESH classification."""

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = cls(2026, 9, 8, 0, 30, tzinfo=ZoneInfo("UTC"))
            return current.astimezone(tz) if tz is not None else current

    market_data._pre_market_global_cache.update(
        ts=0.0,
        snapshot={
            "usIndices": [
                {
                    "name": "道琼斯",
                    "changePct": 0.5,
                    "price": 100,
                    "sourceAsOf": "2026-09-04",
                    "qualityStatus": "FRESH",
                }
            ],
            "a50Future": None,
            "economicEvents": [],
            "globalEvents": [],
            "qualityStatus": "FRESH",
            "sourceName": "AkShare-OpenSource",
            "sourceAsOf": "2026-09-04",
            "retrievedAt": "2026-09-04T00:30:00+00:00",
            "methodVersion": market_data.PRE_MARKET_GLOBAL_METHOD_VERSION,
        },
    )
    monkeypatch.setattr(market_data, "datetime", FixedDatetime)
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(return_value=[]),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=None)
        ),
        mock.patch.object(
            market_data, "fetch_economic_calendar", new=mock.AsyncMock(return_value=[])
        ),
    ):
        degraded = await market_data.get_pre_market_global_snapshot()

    assert degraded["usIndices"][0]["qualityStatus"] == "STALE"
    assert degraded["qualityStatus"] == "STALE"
    assert degraded["sourceAsOf"] == "2026-09-04"
    assert degraded["fallbackAt"] == "2026-09-08T00:30:00+00:00"


def test_market_source_quality_handles_weekend_boundary() -> None:
    monday = datetime(2026, 9, 7, 0, 30, tzinfo=ZoneInfo("UTC"))
    tuesday = datetime(2026, 9, 8, 0, 30, tzinfo=ZoneInfo("UTC"))

    assert market_data._market_source_quality("2026-09-04", monday) == "FRESH"
    assert market_data._market_source_quality("2026-09-04", tuesday) == "STALE"


@pytest.mark.asyncio
async def test_ttl_cache_reclassifies_quality_when_it_crosses_trading_day_boundary(
    monkeypatch,
) -> None:
    """A TTL hit returns a classified copy, never yesterday's mutable object."""

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = cls(2026, 9, 8, 0, 10, tzinfo=ZoneInfo("UTC"))
            return current.astimezone(tz) if tz is not None else current

    original = {
        "usIndices": [
            {
                "name": "道琼斯",
                "changePct": 0.5,
                "price": 100,
                "sourceAsOf": "2026-09-04",
                "qualityStatus": "FRESH",
            }
        ],
        "a50Future": None,
        "economicEvents": [],
        "globalEvents": [],
        "qualityStatus": "FRESH",
        "sourceName": "AkShare-OpenSource",
        "sourceAsOf": "2026-09-04",
        "retrievedAt": "2026-09-07T15:50:00+00:00",
        "methodVersion": market_data.PRE_MARKET_GLOBAL_METHOD_VERSION,
    }
    now = FixedDatetime.now(ZoneInfo("UTC"))
    market_data._pre_market_global_cache.update(
        ts=now.timestamp() - 20 * 60,
        snapshot=original,
    )
    monkeypatch.setattr(market_data, "datetime", FixedDatetime)

    cached = await market_data.get_pre_market_global_snapshot()

    assert cached is not original
    assert cached["usIndices"][0]["qualityStatus"] == "STALE"
    assert cached["qualityStatus"] == "STALE"
    assert original["usIndices"][0]["qualityStatus"] == "FRESH"
    assert original["qualityStatus"] == "FRESH"


@pytest.mark.asyncio
async def test_fetch_economic_calendar_parses_latest_point() -> None:
    # AkShare 宏观接口返回 DataFrame，最新一期在首行（月份降序）。
    cpi_df = pd.DataFrame(
        [
            {"月份": "2026年07月份", "全国-同比增长": 0.3, "全国-环比增长": -0.1},
            {"月份": "2026年06月份", "全国-同比增长": 1.0, "全国-环比增长": -0.3},
        ]
    )
    pmi_df = pd.DataFrame(
        [
            {"月份": "2026年08月份", "制造业-指数": 49.5},
            {"月份": "2026年07月份", "制造业-指数": 49.2},
        ]
    )
    fake_ak = types.SimpleNamespace(
        macro_china_cpi=lambda: cpi_df, macro_china_pmi=lambda: pmi_df
    )
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = await market_data.fetch_economic_calendar()
    assert len(items) == 2
    summaries = " ".join(i["summary"] for i in items)
    assert "中国CPI同比（2026年07月份）：0.3%" in summaries
    assert "中国制造业PMI（2026年08月份）：49.5" in summaries


@pytest.mark.asyncio
async def test_fetch_economic_calendar_skips_failed_indicator() -> None:
    cpi_df = pd.DataFrame([{"月份": "2026年07月份", "全国-同比增长": 0.3}])
    fake_ak = types.SimpleNamespace(
        macro_china_cpi=lambda: cpi_df,
        macro_china_pmi=lambda: _boom("down"),
    )
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = await market_data.fetch_economic_calendar()
    assert len(items) == 1
    assert "中国CPI同比" in items[0]["summary"]


@pytest.mark.asyncio
async def test_fetch_economic_calendar_degrades_to_empty_on_all_fail() -> None:
    fake_ak = types.SimpleNamespace(
        macro_china_cpi=lambda: _boom("x"),
        macro_china_pmi=lambda: _boom("y"),
    )
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = await market_data.fetch_economic_calendar()
    assert items == []


@pytest.mark.asyncio
async def test_fetch_pre_market_global_snapshot_maps_all_three() -> None:
    us = [
        {"name": "道琼斯", "changePct": 1.234, "price": 35000.0},
        {"name": "纳斯达克", "changePct": -0.5, "price": 14000.0},
        {"name": "标普500", "changePct": 2.0, "price": 4500.0},
    ]
    a50 = {
        "name": "富时中国A50期货",
        "changePct": 0.28,
        "price": 13000.0,
        "source": "akshare/a50",
    }
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(return_value=us),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=a50)
        ),
    ):
        snap = await market_data.fetch_pre_market_global_snapshot()
    assert snap["us_markets"] == {"dow": 1.23, "nasdaq": -0.5, "sp500": 2.0}
    assert snap["a50"] == 0.28
    assert snap["source"] == "akshare"
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", snap["as_of"])


@pytest.mark.asyncio
async def test_fetch_pre_market_global_snapshot_partial_and_a50_none() -> None:
    us = [{"name": "道琼斯", "changePct": 0.4, "price": 1.0}]
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(return_value=us),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=None)
        ),
    ):
        snap = await market_data.fetch_pre_market_global_snapshot()
    assert snap["us_markets"] == {"dow": 0.4}  # 仅道琼斯有数据
    assert snap["a50"] is None
    assert snap["source"] == "akshare"


@pytest.mark.asyncio
async def test_fetch_pre_market_global_snapshot_a50_sina_fallback() -> None:
    # 美股抓取失败（空），A50 降级新浪。
    a50 = {
        "name": "富时中国A50",
        "changePct": 0.15,
        "price": 13000.0,
        "source": "sina/a50",
    }
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(return_value=[]),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=a50)
        ),
    ):
        snap = await market_data.fetch_pre_market_global_snapshot()
    assert snap["us_markets"] == {}
    assert snap["a50"] == 0.15
    assert snap["source"] == "sina"


@pytest.mark.asyncio
async def test_fetch_pre_market_global_snapshot_all_unavailable() -> None:
    with (
        mock.patch.object(
            market_data,
            "fetch_us_overnight_indices",
            new=mock.AsyncMock(return_value=[]),
        ),
        mock.patch.object(
            market_data, "fetch_a50_future", new=mock.AsyncMock(return_value=None)
        ),
    ):
        snap = await market_data.fetch_pre_market_global_snapshot()
    assert snap["us_markets"] == {}
    assert snap["a50"] is None
    assert snap["source"] == "unavailable"


# --------------------------------------------------------------------------- #
# 今日公司公告（巨潮 cninfo 披露）
# --------------------------------------------------------------------------- #


def _fake_disclosure_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["代码", "简称", "公告标题", "公告时间", "公告链接"],
    )


def _fake_disclosure_rows() -> list[dict]:
    return [
        {
            "代码": "600519",
            "简称": "贵州茅台",
            "公告标题": "贵州茅台2026年半年度报告",
            "公告时间": "2026-09-02",
        },
        {
            "代码": "600519",
            "简称": "贵州茅台",
            "公告标题": "贵州茅台关于日常关联交易的公告",
            "公告时间": "2026-09-02",  # 无关注关键词 → 排除
        },
        {
            "代码": "600036",
            "简称": "招商银行",
            "公告标题": "招商银行关于回购股份的进展公告",
            "公告时间": "2026-09-01",  # 非今日 → 排除
        },
    ]


@pytest.mark.asyncio
async def test_fetch_today_announcements_filters_date_and_keywords() -> None:
    fake_ak = types.SimpleNamespace(
        stock_zh_a_disclosure_report_cninfo=lambda symbol,
        market,
        start_date,
        end_date: (
            _fake_disclosure_df(
                [row for row in _fake_disclosure_rows() if row["代码"] == symbol]
            )
        )
    )
    market_data._reset_company_announcements_cache()
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = await market_data.fetch_today_company_announcements(
            ["600519", "600036"], "2026-09-02"
        )

    assert [item["symbol"] for item in items] == ["600519"]
    assert items[0]["title"] == "贵州茅台2026年半年度报告"


@pytest.mark.asyncio
async def test_fetch_today_announcements_degrades_on_error_and_empty_input() -> None:
    market_data._reset_company_announcements_cache()
    with mock.patch.object(
        market_data,
        "_ak_import",
        side_effect=RuntimeError("cninfo down"),
    ):
        items = await market_data.fetch_today_company_announcements(
            ["600519"], "2026-09-02"
        )
    assert items == []
    assert await market_data.fetch_today_company_announcements([], "2026-09-02") == []
