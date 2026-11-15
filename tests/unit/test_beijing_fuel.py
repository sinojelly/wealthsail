from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend.integrations.beijing_fuel import (
    BEIJING_FUEL_ARCHIVE,
    BEIJING_FUEL_CURRENT,
    BEIJING_FUEL_SOURCE,
    collect_beijing_fuel_current,
    collect_beijing_fuel_history,
    parse_beijing_fuel,
)
from backend.macro.market_repository import normalize_series, series_context

NOW = datetime(2026, 11, 15, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
ARCHIVED_NOTICE = "https://www.beijingprice.cn/c/2026-09-11/561141.shtml"
TABLE = """<table>
  <tr><th rowspan="2">品名</th><th colspan="2">最高批发价格</th><th colspan="2">最高零售价格</th></tr>
  <tr><th>配送制元/吨</th><th>非配送制元/吨</th><th>元/吨</th><th>元/升</th></tr>
  <tr><td>92号汽油</td><td>10751</td><td>10640</td><td>11051</td><td>8.37</td></tr>
  <tr><td>95号汽油</td><td>11376</td><td>11259</td><td>11676</td><td>8.91</td></tr>
  <tr><td>0号柴油</td><td>9065</td><td>8971</td><td>9365</td><td>8.11</td></tr>
</table>"""
LATEST = (
    "<h1>北京市汽、柴油批发、零售价格表</h1>"
    "<p>日期：2026-11-13　来源：北京市发展和改革委员会</p>" + TABLE
)
POLICY = (
    "<h1>本市成品油价格调整</h1><p>来源：北京市发展和改革委员会</p>"
    "<p>本市汽、柴油最高零售价格自2026年9月11日24时起上调。</p>" + TABLE
)
POLICY_WITHOUT_TABLE = (
    POLICY.removesuffix(TABLE)
    + "<p>92号汽油由每升8.09元调整为8.29元；95号汽油由每升8.61元调整为8.83元；"
    "0号柴油由每升7.81元调整为8.02元。</p>"
)


def test_beijing_ceiling_keeps_published_grade_unit_and_actual_effective_day():
    rows = parse_beijing_fuel(POLICY, ARCHIVED_NOTICE, NOW)
    assert {row["code"] for row in rows} == {
        "CN_FUEL_92",
        "CN_FUEL_95",
        "CN_FUEL_DIESEL_0",
    }
    assert all(row["referenceDate"] == "2026-09-12" for row in rows)
    assert all(row["publishedAt"] is None for row in rows)
    latest = parse_beijing_fuel(LATEST, BEIJING_FUEL_CURRENT, NOW)
    record = next(row for row in latest if row["code"] == "CN_FUEL_92")
    assert record["unit"] == "元/升"
    assert record["value"] == Decimal("8.37")
    assert record["metadata"]["retailCnyPerTonne"] == 11051
    series = next(
        row
        for row in series_context([normalize_series(record, NOW)], NOW)
        if row["code"] == "CN_FUEL_92"
    )
    assert (series["state"], series["value"], series["unit"]) == (
        "READY",
        Decimal("8.37"),
        "元/升",
    )
    assert series["history"][0]["retailCnyPerTonne"] == 11051


@pytest.mark.parametrize(
    "text",
    [
        LATEST.replace("2026-11-13", "2026-11-16"),
        LATEST.replace("最高零售价格", "其他报价"),
        LATEST.replace("8.37", "8.37 元"),
    ],
)
def test_beijing_ceiling_rejects_unverified_value_or_future_quote(text):
    with pytest.raises(ValueError):
        parse_beijing_fuel(text, BEIJING_FUEL_CURRENT, NOW)


@pytest.mark.asyncio
async def test_history_uses_source_archive_and_keeps_confirmed_quotes_on_partial_failure():
    async def text(url):
        if url == BEIJING_FUEL_ARCHIVE:
            return """
              <a href="/c/2026-09-11/561141.shtml">[北京市] 本市成品油价格调整</a>
              <a href="/c/2026-08-28/560822.shtml">[北京市] 本市成品油价格按机制上调</a>
              <a href="https://example.org/c/2026-09-20/777.shtml">本市成品油价格调整</a>
            """
        if url == ARCHIVED_NOTICE:
            return POLICY
        raise httpx.ConnectError("offline")

    result = await collect_beijing_fuel_history(text, NOW, timeout=1)
    assert len(result) == 3
    assert all(row["sourceUri"] == ARCHIVED_NOTICE for row in result)


@pytest.mark.asyncio
async def test_current_quote_can_fall_back_to_confirmed_notice_without_inventing_tonne_price():
    async def text(uri):
        if uri == BEIJING_FUEL_ARCHIVE:
            return (
                '<a href="/c/2026-09-11/561141.shtml">[北京市] 本市成品油价格调整</a>'
            )
        if uri == ARCHIVED_NOTICE:
            return POLICY_WITHOUT_TABLE
        raise httpx.ConnectError("official table unreachable")

    result = await collect_beijing_fuel_current(text, NOW)
    row = next(row for row in result if row["code"] == "CN_FUEL_92")
    assert row["value"] == Decimal("8.29")
    assert row["metadata"]["retailCnyPerTonne"] is None
    assert row["metadata"]["sourceKey"] == BEIJING_FUEL_SOURCE
