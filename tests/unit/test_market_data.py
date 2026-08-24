"""Unit tests for the market data adapter (parsing/quality, no network)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from backend.integrations.market_data import (
    F_CLOSE,
    F_NAV,
    Q_FRESH,
    Q_STALE,
    Q_UNAVAILABLE,
    _change_pct,
    _decimal,
    _match_ak_quote,
    _parse_ak_spot,
    _quote_unavailable,
    _rank_ak_spot,
    _sina_prefix,
    _split_symbol,
)
from backend.market import service


def test_decimal_never_via_float() -> None:
    assert _decimal("1308.60") == Decimal("1308.60")
    assert _decimal(4.61) == Decimal("4.61")
    assert _decimal(Decimal("1.23")) == Decimal("1.23")


def test_sina_prefix_mapping() -> None:
    assert _sina_prefix("600519") == "sh"
    assert _sina_prefix("510300") == "sh"
    assert _sina_prefix("688001") == "sh"
    assert _sina_prefix("000001") == "sz"
    assert _sina_prefix("159915") == "sz"


def test_parse_ak_spot_filters_and_quality() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {
            "代码": ["600519", "000001", "999999"],
            "名称": ["贵州茅台", "平安银行", "不存在"],
            "最新价": [1308.6, 11.2, None],
        }
    )
    obs = _parse_ak_spot(df, ["600519", "000001"], "test")
    by_symbol = {o.symbol: o for o in obs}
    assert by_symbol["600519"].value == Decimal("1308.6")
    assert by_symbol["600519"].quality_status == Q_FRESH
    assert by_symbol["000001"].quality_status == Q_FRESH
    assert "999999" not in by_symbol  # 不在请求列表 → 不返回


def test_quality_to_snapshot_mapping() -> None:
    assert service._quality_to_snapshot("FRESH") == "FRESH"
    assert service._quality_to_snapshot("STALE") == "STALE"
    assert service._quality_to_snapshot("UNAVAILABLE") == "UNAVAILABLE"
    assert service._quality_to_snapshot("WEIRD") == "UNAVAILABLE"  # fail closed


# ---------------------------------------------------------------------------
# get_quote — 单标的行情契约
# ---------------------------------------------------------------------------
def test_split_symbol_accepts_code_suffix_and_name() -> None:
    assert _split_symbol("300308") == ("300308", None)
    assert _split_symbol("300308.SZ") == ("300308", "SZ")
    assert _split_symbol("600519.sh") == ("600519", "SH")
    assert _split_symbol("中际旭创") == (None, None)
    assert _split_symbol("") == (None, None)


def test_change_pct_guards_division_by_zero() -> None:
    assert _change_pct(11.5, 11.0) == pytest.approx(4.545454)
    assert _change_pct(None, 11.0) is None
    assert _change_pct(11.5, None) is None
    assert _change_pct(11.5, 0) is None


def test_quote_unavailable_contract() -> None:
    q = _quote_unavailable("300308", "sina/hq-error")
    assert q["quality"] == Q_UNAVAILABLE
    assert q["symbol"] == "300308"
    assert q["price"] is None and q["change_pct"] is None
    assert q["volume"] is None and q["turnover"] is None


def test_match_ak_quote_by_code_fresh() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {
            "代码": ["300308", "600519"],
            "名称": ["中际旭创", "贵州茅台"],
            "最新价": [45.67, 1304.6],
            "涨跌幅": [2.3, -0.5],
            "成交量": [12345678, 48439],
            "成交额": [5.6e9, 6.3e9],
            "最高": [46.0, 1313.8],
            "最低": [45.0, 1270.3],
            "今开": [45.2, 1271.0],
            "昨收": [44.64, 1304.6],
        }
    )
    q = _match_ak_quote(df, code="300308", name=None)
    assert q is not None
    assert q["symbol"] == "300308"
    assert q["name"] == "中际旭创"
    assert q["price"] == pytest.approx(45.67)
    assert q["change_pct"] == pytest.approx(2.3073, abs=0.01)
    assert q["quality"] == Q_FRESH
    assert q["source"] == "akshare/spot-em"


def test_match_ak_quote_by_name_and_miss() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {
            "代码": ["300308"],
            "名称": ["中际旭创"],
            "最新价": [45.67],
            "昨收": [44.64],
        }
    )
    q = _match_ak_quote(df, code=None, name="旭创")
    assert q is not None and q["symbol"] == "300308"
    assert _match_ak_quote(df, code="999999", name=None) is None
    assert _match_ak_quote(df, code=None, name="不存在") is None


def test_match_ak_quote_no_price_falls_back_stale() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {
            "代码": ["600519"],
            "名称": ["贵州茅台"],
            "最新价": [float("nan")],
            "昨收": [1300.0],
        }
    )
    q = _match_ak_quote(df, code="600519", name=None)
    assert q is not None
    assert q["quality"] == Q_STALE  # 停牌 → 昨收兜底
    assert q["price"] == pytest.approx(1300.0)


def test_rank_ak_spot_sorts_by_change_desc_and_limits() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {
            "代码": ["000001", "600519", "300308", "000858"],
            "名称": ["平安银行", "贵州茅台", "中际旭创", "五粮液"],
            "最新价": [11.5, 1304.6, 870.2, 71.5],
            "涨跌幅": [1.0, 2.5, -7.7, 0.4],
            "成交量": [1199024, 48439, 389092, 212574],
            "成交额": [1.3e9, 6.3e9, 3.4e10, 1.5e9],
        }
    )
    rows = _rank_ak_spot(df, limit=3)
    assert [r["symbol"] for r in rows] == ["600519", "000001", "000858"]
    assert rows[0]["change_pct"] == pytest.approx(2.5)
    assert rows[0]["name"] == "贵州茅台"
    assert len(rows) == 3  # limit 生效
    # 缺涨跌幅的行被跳过
    df2 = df.copy()
    df2.loc[0, "涨跌幅"] = None
    rows2 = _rank_ak_spot(df2, limit=10)
    assert all(r["change_pct"] is not None for r in rows2)


def test_observation_payload_serialization() -> None:
    from backend.integrations.market_data import Observation

    obs = Observation(
        symbol="110011",
        subject_type="FUND",
        field_code=F_NAV,
        value=Decimal("4.2552"),
        as_of=datetime(2026, 8, 24, 13, 0),
        quality_status=Q_STALE,
        unit="元/份",
    )
    payload = obs.to_payload()
    assert payload["value"] == "4.2552"
    assert payload["field_code"] == F_NAV
    assert payload["quality_status"] == Q_STALE
