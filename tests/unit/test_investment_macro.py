import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest


def test_macro_import_preserves_period_and_does_not_invent_release_time():
    from backend.jobs.investment_macro import normalize_macro

    rows = normalize_macro(
        [
            {
                "name": "中国CPI同比",
                "date": "2026-09",
                "value": "-0.2",
                "unit": "%",
                "source": "AkShare/macro",
            }
        ]
    )
    assert rows[0]["fieldCode"] == "CPI_YOY"
    assert rows[0]["value"] == Decimal("-0.2")
    assert rows[0]["publishedAt"] is None
    assert rows[0]["quality"] == "STALE"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "unknown"])
def test_bad_macro_values_are_not_imported(value):
    from backend.jobs.investment_macro import normalize_macro

    assert (
        normalize_macro([{"name": "中国制造业PMI", "date": "2026-09", "value": value}])
        == []
    )


@pytest.mark.parametrize("period", ["2026-09", "202609", "2026年09月份"])
def test_provider_period_formats_retain_month_without_inventing_publication(period):
    from backend.jobs.investment_macro import normalize_macro

    rows = normalize_macro([{"name": "中国CPI同比", "date": period, "value": "-0.2"}])
    assert len(rows) == 1
    assert rows[0]["period"] == date(2026, 9, 1)
    assert rows[0]["publishedAt"] is None


def _provider(**overrides):
    frames = {
        "macro_china_cpi": pd.DataFrame(
            [
                {"月份": "2026年09月份", "全国-同比增长": -0.2},
                {"月份": "2026年08月份", "全国-同比增长": 0.1},
            ]
        ),
        "macro_china_pmi": pd.DataFrame(
            [
                {"月份": "202609", "制造业-指数": 49.8},
                {"月份": "202608", "制造业-指数": 49.5},
            ]
        ),
        "macro_china_ppi": pd.DataFrame(
            [
                {"月份": "2026-09", "当月同比增长": -2.1},
                {"月份": "2026-08", "当月同比增长": -2.4},
            ]
        ),
        "macro_china_money_supply": pd.DataFrame(
            [
                {"月份": "2026年09月份", "货币和准货币(M2)-同比增长": 8.6},
                {"月份": "2026年08月份", "货币和准货币(M2)-同比增长": 8.4},
            ]
        ),
    }
    frames.update(overrides)
    return SimpleNamespace(
        **{
            name: value if callable(value) else lambda frame=value: frame
            for name, value in frames.items()
        }
    )


@pytest.mark.asyncio
async def test_history_fetch_retains_all_months_for_four_a_share_macro_series():
    from backend.integrations.investment_macro import fetch_macro_history

    rows = await fetch_macro_history(
        date(2026, 8, 1), date(2026, 10, 13), provider=_provider()
    )
    actual = {
        code: [
            (row["period"], row["value"]) for row in rows if row["fieldCode"] == code
        ]
        for code in ("CPI_YOY", "PMI", "PPI_YOY", "M2_YOY")
    }
    assert actual == {
        "CPI_YOY": [
            (date(2026, 8, 1), Decimal("0.1")),
            (date(2026, 9, 1), Decimal("-0.2")),
        ],
        "PMI": [
            (date(2026, 8, 1), Decimal("49.5")),
            (date(2026, 9, 1), Decimal("49.8")),
        ],
        "PPI_YOY": [
            (date(2026, 8, 1), Decimal("-2.4")),
            (date(2026, 9, 1), Decimal("-2.1")),
        ],
        "M2_YOY": [
            (date(2026, 8, 1), Decimal("8.4")),
            (date(2026, 9, 1), Decimal("8.6")),
        ],
    }
    assert {row["sourceUri"] for row in rows} == {
        "https://data.eastmoney.com/cjsj/cpi.html",
        "https://data.eastmoney.com/cjsj/pmi.html",
        "https://data.eastmoney.com/cjsj/ppi.html",
        "https://data.eastmoney.com/cjsj/hbgyl.html",
    }
    assert all(row["publishedAt"] is None and row["quality"] == "STALE" for row in rows)


@pytest.mark.asyncio
async def test_history_window_rejects_bad_values_future_periods_and_conflicting_duplicates():
    from backend.integrations.investment_macro import fetch_macro_history

    provider = _provider(
        macro_china_cpi=pd.DataFrame(
            [
                {"月份": "2026-09", "全国-同比增长": -0.2},
                {"月份": "202609", "全国-同比增长": -0.2},
                {"月份": "2026-08", "全国-同比增长": 0.1},
                {"月份": "2026年08月份", "全国-同比增长": 0.3},
                {"月份": "2026-11", "全国-同比增长": 99},
                {"月份": "2026-07", "全国-同比增长": 1},
                {"月份": "2026-10", "全国-同比增长": float("nan")},
                {"月份": "2026-10", "全国-同比增长": float("inf")},
                {"月份": "2026-13", "全国-同比增长": 1},
            ]
        )
    )
    rows = await fetch_macro_history(
        date(2026, 8, 1), date(2026, 10, 13), provider=provider
    )
    cpi = [row for row in rows if row["fieldCode"] == "CPI_YOY"]
    assert [(row["period"], row["value"]) for row in cpi] == [
        (date(2026, 9, 1), Decimal("-0.2"))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["outage", "changed_schema"])
async def test_macro_history_failure_is_isolated_to_affected_source(failure):
    from backend.integrations.investment_macro import fetch_macro_history

    def unavailable():
        raise RuntimeError("provider unavailable")

    broken = unavailable if failure == "outage" else pd.DataFrame([{"unexpected": 1}])
    rows = await fetch_macro_history(
        date(2026, 8, 1), date(2026, 10, 13), provider=_provider(macro_china_cpi=broken)
    )
    assert {row["fieldCode"] for row in rows} == {"PMI", "PPI_YOY", "M2_YOY"}
    assert len(rows) == 6


@pytest.mark.asyncio
async def test_macro_history_rejects_reversed_window():
    from backend.integrations.investment_macro import fetch_macro_history

    with pytest.raises(ValueError, match="start"):
        await fetch_macro_history(
            date(2026, 10, 13), date(2026, 8, 1), provider=_provider()
        )


@pytest.mark.asyncio
async def test_sync_persists_five_year_history_with_exact_period_and_source(
    monkeypatch,
):
    from backend.integrations.investment_macro import fetch_macro_history
    from backend.jobs import investment_macro as job

    conn = AsyncMock()
    conn.fetchval.return_value = "00000000-0000-0000-0000-000000000123"

    @asynccontextmanager
    async def transaction():
        yield

    @asynccontextmanager
    async def connection(pool):
        yield conn

    conn.transaction = MagicMock(side_effect=transaction)
    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(job.repository, "upsert_data_source", AsyncMock())
    monkeypatch.setattr(
        job.repository, "get_source_id", AsyncMock(return_value="source-id")
    )
    persisted = []

    async def persist(connection, rows):
        persisted.extend(rows)

    monkeypatch.setattr(job.repository, "insert_observations", persist)
    provider = _provider(
        macro_china_cpi=pd.DataFrame(
            [
                {"月份": "2021年10月份", "全国-同比增长": 1.5},
                {"月份": "2021年09月份", "全国-同比增长": 0.7},
                {"月份": "2026年09月份", "全国-同比增长": -0.2},
                {"月份": "2026年11月份", "全国-同比增长": 99},
            ]
        )
    )

    async def history(start, end):
        return await fetch_macro_history(start, end, provider=provider)

    monkeypatch.setattr(job, "fetch_macro_history", history)
    now = datetime(2026, 10, 13, 22, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = await job.sync_macro(object(), now)
    cpi = [row for row in persisted if row[2] == "CPI_YOY"]
    assert [(row[8].date(), row[3]) for row in cpi] == [
        (date(2021, 10, 1), Decimal("1.5")),
        (date(2026, 9, 1), Decimal("-0.2")),
    ]
    assert result["observations"] == 8
    assert all(row[9] == now and row[12] == "STALE" for row in persisted)
    assert all(json.loads(row[14])["publicationVerified"] is False for row in persisted)
    assert (
        json.loads(cpi[0][14])["sourceUri"]
        == "https://data.eastmoney.com/cjsj/cpi.html"
    )
    assert json.loads(cpi[0][14])["period"] == "2021-10"
    origins = {call.args[3] for call in conn.fetchval.await_args_list}
    assert origins == {
        "https://data.eastmoney.com/cjsj/cpi.html",
        "https://data.eastmoney.com/cjsj/pmi.html",
        "https://data.eastmoney.com/cjsj/ppi.html",
        "https://data.eastmoney.com/cjsj/hbgyl.html",
    }
