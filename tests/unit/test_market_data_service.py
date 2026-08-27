"""modules/market_data 领域服务单元测试（monkeypatch 外部 I/O，无需联网）。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

import modules.market_data.service as service
from modules.market_data.service import (
    MarketDataService,
    Observation,
    price_limit_pct,
    _daily_quality,
    _parse_ak_spot,
    _sentiment_score,
)


# ---------------------------------------------------------------------------
# 假连接 / 假池
# ---------------------------------------------------------------------------
class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.fetchrow_results: list[Any] = []
        self.fetchval_results: list[Any] = []
        self.fetch_results: list[list[Any]] = []

    async def fetchrow(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        if not self.fetchrow_results:
            return None
        return self.fetchrow_results.pop(0)

    async def fetch(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        if not self.fetch_results:
            return []
        return self.fetch_results.pop(0)

    async def fetchval(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        if not self.fetchval_results:
            return None
        return self.fetchval_results.pop(0)

    async def execute(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        return "OK"

    async def executemany(self, sql, args) -> Any:
        self.calls.append((sql, tuple(args)))
        return "OK"

    def transaction(self):
        class _Tx:
            def __init__(self, conn):
                self._conn = conn

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, *_args):
                return None

        return _Tx(self)


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def acquire(self):
        class _Acquire:
            def __init__(self, conn):
                self._conn = conn

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, *_args):
                return None

        return _Acquire(self.conn)


def _service(conn: FakeConn | None = None) -> MarketDataService:
    return MarketDataService(FakePool(conn or FakeConn()))


def _sqls(conn: FakeConn) -> list[str]:
    return [c[0] for c in conn.calls]


def _obs(symbol: str, *, quality: str = "FRESH", field: str = "CLOSE") -> Observation:
    return Observation(
        symbol=symbol,
        subject_type="INSTRUMENT",
        field_code=field,
        value=Decimal("10.00"),
        as_of=service._now(),
        quality_status=quality,
        method_version="akshare/spot-em",
    )


# ---------------------------------------------------------------------------
# 纯函数：质量标记 / 涨跌停 / 情绪评分
# ---------------------------------------------------------------------------
def test_daily_quality_marks_today_fresh_else_stale() -> None:
    assert _daily_quality(date.today()) == "FRESH"
    assert _daily_quality(date.today() - timedelta(days=1)) == "STALE"


def test_price_limit_pct_per_board() -> None:
    assert price_limit_pct("600519", "贵州茅台") == Decimal("10")
    assert price_limit_pct("000001", "*ST平安") == Decimal("5")
    assert price_limit_pct("830799", "北交所股票") == Decimal("30")
    assert price_limit_pct("300308", "中际旭创") == Decimal("20")
    assert price_limit_pct("688981", "中芯国际") == Decimal("20")


def test_sentiment_score_bounds_and_limit_moves() -> None:
    assert _sentiment_score(1000, 1000, 0, 0) == Decimal("50.00")
    assert _sentiment_score(2000, 0, 0, 0) == Decimal("100.00")
    assert _sentiment_score(0, 2000, 0, 0) == Decimal("0.00")
    # 涨停加分（0.5/只，封顶 20 只）、跌停减分（1.0/只，封顶 20 只）
    assert _sentiment_score(1000, 1000, 20, 0) == Decimal("60.00")
    assert _sentiment_score(1000, 1000, 0, 20) == Decimal("30.00")
    assert _sentiment_score(1000, 1000, 100, 100) == Decimal("40.00")


def test_parse_ak_spot_extracts_requested_symbols() -> None:
    df = pd.DataFrame(
        [
            {"代码": "600519", "名称": "贵州茅台", "最新价": 1700.5, "涨跌幅": 1.23},
            {"代码": "000001", "名称": "平安银行", "最新价": 10.2, "涨跌幅": -0.5},
            {"代码": "300308", "名称": "中际旭创", "最新价": None, "涨跌幅": None},
        ]
    )
    observations = _parse_ak_spot(df, ["600519", "300308"], "A股")
    by_symbol = {o.symbol: o for o in observations}
    assert by_symbol["600519"].value == Decimal("1700.5")
    assert by_symbol["600519"].return_rate == Decimal("0.0123")
    assert by_symbol["600519"].quality_status == "FRESH"
    # 无价格 → UNAVAILABLE（fail closed）
    assert by_symbol["300308"].quality_status == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 抓取降级链
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_equity_quotes_degrades_to_sina_on_akshare_failure(monkeypatch) -> None:
    async def _boom() -> Any:
        raise RuntimeError("eastmoney IP 风控")

    sina_observations = [_obs("600519", quality="STALE", field="CLOSE")]

    async def _sina(symbols, subject_type="INSTRUMENT", field_code="CLOSE"):
        return sina_observations

    monkeypatch.setattr(service, "_fetch_ak_spot_df", _boom)
    monkeypatch.setattr(service, "_fetch_sina_quotes", _sina)

    service_ = _service()
    observations = await service_.fetch_equity_quotes(["600519"])
    assert observations == sina_observations


@pytest.mark.asyncio
async def test_fund_observations_marks_stale_for_yesterday_nav(monkeypatch) -> None:
    fetched: list[str] = []

    async def _fake_nav(client, symbol: str) -> Observation | None:
        published = date.today() - timedelta(days=1)  # 昨日披露 → STALE
        fetched.append(symbol)
        return Observation(
            symbol=symbol,
            subject_type="FUND",
            field_code="NAV",
            value=Decimal("1.2345"),
            as_of=service._now(),
            quality_status=_daily_quality(published),
            unit="元/份",
            method_version="eastmoney/f10-lsjz",
        )

    monkeypatch.setattr(service, "_fetch_fund_nav", _fake_nav)
    service_ = _service()
    observations = await service_.fetch_fund_observations(["110011", "005827"])
    assert fetched == ["110011", "005827"]
    assert all(o.quality_status == "STALE" for o in observations)


@pytest.mark.asyncio
async def test_get_quote_falls_back_to_sina(monkeypatch) -> None:
    async def _boom() -> Any:
        raise RuntimeError("akshare down")

    monkeypatch.setattr(service, "_fetch_ak_spot_df", _boom)

    async def _sina_quote(code: str) -> dict:
        return {
            "symbol": code, "name": "贵州茅台", "price": 1700.5,
            "change_pct": 1.23, "quality": "FRESH", "source": "sina/hq",
        }

    monkeypatch.setattr(service, "_fetch_sina_single_quote", _sina_quote)
    service_ = _service()
    quote = await service_.get_quote("600519")
    assert quote["symbol"] == "600519"
    assert quote["quality"] == "FRESH"
    assert quote["source"] == "sina/hq"

    empty = await service_.get_quote("")
    assert empty["quality"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_observations_writes_sources_snapshot_and_rows() -> None:
    conn = FakeConn()
    conn.fetchval_results = ["src-1", "src-1"]
    service_ = _service(conn)

    result = await service_.persist_observations(
        [_obs("600519", quality="FRESH"), _obs("110011", quality="STALE", field="NAV")],
        session="INTRADAY",
    )

    sqls = _sqls(conn)
    assert any("INSERT INTO reference.data_sources" in s for s in sqls)
    assert any(
        "sha256:" in str(arg)
        for c in conn.calls
        if "INSERT INTO market.source_snapshots" in c[0]
        for arg in c[1]
    )
    obs_call = next(c for c in conn.calls if "INSERT INTO market.market_observations" in c[0])
    rows = obs_call[1]  # executemany 的参数即行元组
    assert len(rows) == 2
    assert any("ON CONFLICT" in c[0] for c in conn.calls if "market_observations" in c[0])
    assert result["observations"] == 2

    # 无观测 → 直接返回，不写库
    conn2 = FakeConn()
    empty = await _service(conn2).persist_observations([], session="INTRADAY")
    assert empty == {"snapshots": 0, "observations": 0}
    assert not _sqls(conn2)


# ---------------------------------------------------------------------------
# 交易日历
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_is_trading_day_uses_calendar_with_workday_fallback() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [{"is_trading_day": True}, {"is_trading_day": False}, None]
    service_ = _service(conn)

    assert await service_.is_trading_day(conn, date(2026, 8, 20)) is True  # type: ignore[arg-type]
    assert await service_.is_trading_day(conn, date(2026, 8, 20)) is False  # type: ignore[arg-type]
    # 无记录 → 工作日兜底（2026-08-22 是周六 → False；8-24 周一 → True）
    assert await service_.is_trading_day(conn, date(2026, 8, 22)) is False  # type: ignore[arg-type]
    assert await service_.is_trading_day(conn, date(2026, 8, 24)) is True  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_previous_trading_day_falls_back_to_workdays() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [{"calendar_date": date(2026, 8, 20)}]
    service_ = _service(conn)
    assert await service_.previous_trading_day(conn, date(2026, 8, 21)) == date(2026, 8, 20)  # type: ignore[arg-type]

    conn2 = FakeConn()  # 无记录 → 回退找工作日
    service2 = _service(conn2)
    assert await service2.previous_trading_day(conn2, date(2026, 8, 24)) == date(2026, 8, 21)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fill_trading_calendar_bulk_upsert(monkeypatch) -> None:
    class AK:
        def tool_trade_date_hist_sina(self):
            return pd.DataFrame({"trade_date": ["2026-08-20", "2026-08-21"]})

    conn = FakeConn()
    monkeypatch.setattr(service, "_ak_import", lambda: AK())
    service_ = _service(conn)

    count = await service_.fill_trading_calendar()

    assert count > 1000  # 2024-2026 全覆盖
    call = next(c for c in conn.calls if "INSERT INTO reference.trading_calendars" in c[0])
    rows = call[1]  # executemany 的参数即行元组
    assert any(r[0] == date(2026, 8, 20) and r[1] is True for r in rows)
    assert any(r[0] == date(2026, 8, 22) and r[1] is False for r in rows)  # 周六


# ---------------------------------------------------------------------------
# 市场情绪
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refresh_market_sentiment_persists_governance_metadata(monkeypatch) -> None:
    async def _sentiment() -> dict:
        return {
            "trade_date": "2026-08-20",
            "total_up": 3200,
            "total_down": 1400,
            "total_flat": 200,
            "limit_up": 45,
            "limit_down": 3,
            "turnover": Decimal("9800.50"),
            "sentiment_score": Decimal("62.50"),
            "total": 4800,
            "source_name": "AkShare-OpenSource",
            "source_as_of": service._now(),
            "retrieved_at": service._now(),
            "quality_status": "FRESH",
            "coverage_pct": Decimal("99.80"),
            "method_version": "sentiment/v2",
        }

    async def _north() -> Decimal | None:
        return None

    monkeypatch.setattr(service, "_fetch_sentiment_from_ak", _sentiment)
    monkeypatch.setattr(service, "_fetch_north_flow", _north)
    conn = FakeConn()
    service_ = _service(conn)

    result = await service_.refresh_market_sentiment()

    assert result == {"status": "COMPLETED", "tradeDate": "2026-08-20"}
    upsert = next(c for c in conn.calls if "INSERT INTO market.market_sentiment" in c[0])
    assert upsert[1][1] == 3200
    assert upsert[1][2] == 1400
    assert upsert[1][9] == "AkShare-OpenSource"
    assert upsert[1][14] == "sentiment/v2"
    assert upsert[1][7] is None  # 北向可选字段留空


@pytest.mark.asyncio
async def test_refresh_market_sentiment_abstains_when_providers_down(monkeypatch) -> None:
    async def _empty() -> dict:
        return {}

    monkeypatch.setattr(service, "_fetch_sentiment_from_ak", _empty)
    monkeypatch.setattr(service, "_fetch_sina_market_sentiment", _empty)
    conn = FakeConn()
    result = await _service(conn).refresh_market_sentiment()
    assert result == {"status": "ABSTAINED", "reason": "PROVIDER_UNAVAILABLE"}
    assert not _sqls(conn)


@pytest.mark.asyncio
async def test_get_market_sentiment_degrades_cross_day_to_stale() -> None:
    today = service._now().date()
    conn = FakeConn()
    conn.fetchrow_results = [
        {
            "trade_date": today - timedelta(days=1),  # 昨日 FRESH → 读侧降级 STALE
            "total_up": 3200,
            "total_down": 1400,
            "total_flat": 200,
            "limit_up": 45,
            "limit_down": 3,
            "turnover": Decimal("9800.50"),
            "north_flow": None,
            "sentiment_score": Decimal("62.50"),
            "source_name": "AkShare-OpenSource",
            "source_as_of": service._now(),
            "retrieved_at": service._now(),
            "quality_status": "FRESH",
            "coverage_pct": Decimal("99.80"),
            "method_version": "sentiment/v2",
            "updated_at": service._now(),
        }
    ]
    result = await _service(conn).get_market_sentiment(conn)  # type: ignore[arg-type]
    assert result["qualityStatus"] == "STALE"
    assert result["totalUp"] == 3200
    assert result["tradeDate"] == (today - timedelta(days=1)).isoformat()

    conn2 = FakeConn()  # 无记录 → UNAVAILABLE 空快照
    empty = await _service(conn2).get_market_sentiment(conn2)  # type: ignore[arg-type]
    assert empty["qualityStatus"] == "UNAVAILABLE"
    assert empty["totalUp"] is None
