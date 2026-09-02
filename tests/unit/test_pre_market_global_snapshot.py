"""Unit tests for the pre-market global overnight snapshot (P0: US + A50).

All external providers are mocked so the suite runs offline and never hits the
network. The contract is fail-closed: any outage, timeout or parse error must
degrade to empty data (rendered as 暂无), never to fabricated numbers.
"""

from __future__ import annotations

import types
import unittest.mock as mock

import pytest

from backend.integrations import market_data


class _FakeDF:
    """Minimal pandas-less stand-in: supports empty/columns/iterrows/subscript."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.columns = list(rows[0].keys()) if rows else []
        self.empty = len(rows) == 0

    def iterrows(self):
        for i, row in enumerate(self._rows):
            yield i, row


def _fake_us_df() -> _FakeDF:
    return _FakeDF(
        [
            {"名称": "道琼斯", "最新价": 35000.0, "涨跌幅": 1.23},
            {"名称": "纳斯达克", "最新价": 14000.0, "涨跌幅": -0.5},
            {"名称": "标普500", "最新价": 4500.0, "涨跌幅": 2.0},
            {"名称": "上证指数", "最新价": 3000.0, "涨跌幅": 0.1},  # not a US index
        ]
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    market_data._reset_pre_market_global_cache()
    yield
    market_data._reset_pre_market_global_cache()


@pytest.mark.asyncio
async def test_fetch_us_indices_filters_to_major_indices() -> None:
    fake_ak = types.SimpleNamespace(index_us_spot_em=lambda: _fake_us_df())
    with mock.patch.object(market_data, "_ak_import", return_value=fake_ak):
        items = await market_data.fetch_us_overnight_indices()
    names = {item["name"] for item in items}
    assert names == {"道琼斯", "纳斯达克", "标普500"}
    by_name = {item["name"]: item for item in items}
    assert by_name["道琼斯"]["changePct"] == pytest.approx(1.23)
    assert by_name["纳斯达克"]["changePct"] == pytest.approx(-0.5)


@pytest.mark.asyncio
async def test_fetch_us_indices_degrades_on_akshare_error() -> None:
    with mock.patch.object(
        market_data, "_ak_import", side_effect=RuntimeError("akshare down")
    ):
        items = await market_data.fetch_us_overnight_indices()
    assert items == []


@pytest.mark.asyncio
async def test_fetch_us_indices_degrades_on_unexpected_columns() -> None:
    fake_ak = types.SimpleNamespace(index_us_spot_em=lambda: _FakeDF([{"foo": 1}]))
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


@pytest.mark.asyncio
async def test_fetch_sina_a50_parses_change_pct() -> None:
    # Sina futures layout: name,open,preclose,bid,ask,price,...
    raw = 'var hq_str_nfSA0="富时中国A50,13000,12900,13010,12950,13050,0,0,0,0,0,0,20260902,09:00:00";'
    with mock.patch.object(
        market_data.httpx, "AsyncClient", new=_make_sina_client(raw)
    ):
        result = await market_data._fetch_sina_a50()
    assert result is not None
    assert result["name"] == "富时中国A50"
    assert result["changePct"] == pytest.approx((13050 - 12900) / 12900 * 100, rel=1e-6)
    assert result["source"] == "sina/a50"


@pytest.mark.asyncio
async def test_fetch_sina_a50_degrades_on_short_payload() -> None:
    raw = 'var hq_str_nfSA0="富时中国A50";'  # only one field -> unusable
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


@pytest.mark.asyncio
async def test_snapshot_populates_us_and_a50() -> None:
    us = [{"name": "道琼斯", "changePct": 1.0, "price": 35000.0}]
    a50 = {
        "name": "富时中国A50期货",
        "changePct": 0.5,
        "price": 13000.0,
        "source": "sina/a50",
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
    assert snap["usIndices"] == us
    assert snap["a50Future"] == a50
    assert snap["qualityStatus"] == "FRESH"
    assert "AkShare-OpenSource" in snap["sourceName"]
    assert snap["economicEvents"] == []  # P1 not yet wired in this test


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
    assert first is second  # same cached object
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
    good_us = [{"name": "道琼斯", "changePct": 1.0, "price": 1.0}]
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
    assert degraded["usIndices"] == good_us
    assert degraded["qualityStatus"] == "STALE"


def _make_json_client(per_slug):
    """Stand-in for httpx.AsyncClient returning chinadata.live payloads by slug."""

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            slug = url.rstrip("/").split("/")[-1]
            outcome = per_slug.get(slug)
            if isinstance(outcome, Exception):
                # Simulate a network/HTTP failure (production catches httpx.HTTPError).
                raise market_data.httpx.HTTPError(str(outcome))
            return _Resp(outcome)

    return lambda *args, **kwargs: _Client()


@pytest.mark.asyncio
async def test_fetch_economic_calendar_parses_latest_point() -> None:
    cpi = {
        "success": True,
        "data": {
            "title": "CPI",
            "unit": "%",
            "source": "NBS",
            "data": [
                {"date": "2025-06", "value": 0.2},
                {"date": "2025-08", "value": 0.3},
            ],
        },
    }
    pmi = {
        "success": True,
        "data": {
            "title": "PMI",
            "unit": "%",
            "source": "NBS",
            "data": [{"date": "2025-08", "value": 49.5}],
        },
    }
    with mock.patch.object(
        market_data.httpx,
        "AsyncClient",
        new=_make_json_client({"china-cpi": cpi, "china-pmi": pmi}),
    ):
        items = await market_data.fetch_economic_calendar()
    assert len(items) == 2
    summaries = " ".join(i["summary"] for i in items)
    assert "中国CPI同比（2025-08）：0.3%" in summaries
    assert "中国制造业PMI（2025-08）：49.5%" in summaries


@pytest.mark.asyncio
async def test_fetch_economic_calendar_skips_failed_indicator() -> None:
    cpi = {
        "success": True,
        "data": {
            "title": "CPI",
            "unit": "%",
            "source": "NBS",
            "data": [{"date": "2025-08", "value": 0.3}],
        },
    }
    with mock.patch.object(
        market_data.httpx,
        "AsyncClient",
        new=_make_json_client({"china-cpi": cpi, "china-pmi": RuntimeError("down")}),
    ):
        items = await market_data.fetch_economic_calendar()
    assert len(items) == 1
    assert "中国CPI同比" in items[0]["summary"]


@pytest.mark.asyncio
async def test_fetch_economic_calendar_degrades_to_empty_on_all_fail() -> None:
    with mock.patch.object(
        market_data.httpx,
        "AsyncClient",
        new=_make_json_client(
            {"china-cpi": RuntimeError("x"), "china-pmi": RuntimeError("y")}
        ),
    ):
        items = await market_data.fetch_economic_calendar()
    assert items == []
