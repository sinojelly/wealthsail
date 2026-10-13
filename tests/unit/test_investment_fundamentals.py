from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest


def subject(**overrides):
    config = {
        "provider": "LEGULEGU",
        "symbol": "沪深300",
        "sourceUri": "https://legulegu.com/stockdata/hs300-ttm-lyr",
        "verified": True,
        "publishHour": 18,
    }
    config.update(overrides)
    return {"valuation": config}


def provider(*, pe=None, pb=None, fees=None):
    def result(frame, *, fund=False):
        def fetch(*, symbol, indicator=None):
            if symbol != ("015641" if fund else "沪深300"):
                raise ValueError("unexpected provider symbol")
            if fund and indicator != "运作费用":
                raise ValueError("expected operating fee section")
            if isinstance(frame, Exception):
                raise frame
            return frame

        return fetch

    return SimpleNamespace(
        stock_index_pe_lg=result(
            pe
            if pe is not None
            else pd.DataFrame(
                [
                    {
                        "日期": date(2026, 10, 12),
                        "滚动市盈率": 15,
                        "等权滚动市盈率": 99,
                    },
                    {"日期": date(2026, 10, 9), "滚动市盈率": 16, "等权滚动市盈率": 98},
                ]
            )
        ),
        stock_index_pb_lg=result(
            pb
            if pb is not None
            else pd.DataFrame(
                [
                    {"日期": date(2026, 10, 12), "市净率": 1.5, "等权市净率": 8},
                    {"日期": date(2026, 10, 9), "市净率": 1.6, "等权市净率": 9},
                ]
            )
        ),
        fund_fee_em=result(
            fees
            if fees is not None
            else pd.DataFrame(
                [
                    [
                        "管理费率",
                        "0.50%（每年）",
                        "托管费率",
                        "0.10%（每年）",
                        "销售服务费率",
                        "0.00%（每年）",
                    ]
                ]
            ),
            fund=True,
        ),
    )


@pytest.mark.asyncio
async def test_registered_valuation_uses_exact_pe_pb_columns_and_real_dates():
    from backend.integrations.investment_fundamentals import fetch_index_valuation

    result = await fetch_index_valuation(
        subject(), date(2026, 10, 1), date(2026, 10, 12), provider=provider()
    )

    assert result["state"] == "READY"
    assert result["pe"]["metric"] == "PE_TTM"
    assert result["pe"]["points"] == [
        {"date": "2026-10-09", "value": 16.0},
        {"date": "2026-10-12", "value": 15.0},
    ]
    assert result["pb"]["points"][-1]["value"] == 1.5
    assert result["pe"]["sampleCount"] == 2
    assert result["requestedEnd"] == "2026-10-12"


@pytest.mark.asyncio
async def test_operating_fee_table_uses_named_labels_and_decimal_rates():
    from backend.integrations.investment_fundamentals import fetch_fund_costs

    result = await fetch_fund_costs("015641", provider=provider())

    assert result["state"] == "READY"
    assert result["managementRate"] == 0.005
    assert result["custodyRate"] == 0.001
    assert result["salesServiceRate"] == 0
    assert result["totalRate"] == 0.006
    assert result["sourceUri"] == "https://fundf10.eastmoney.com/jjfl_015641.html"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"verified": False},
        {"verified": "true"},
        {"provider": "OTHER"},
        {"symbol": "中证医药"},
        {"symbol": "000300"},
        {"sourceUri": ""},
        {"sourceUri": True},
        {"sourceUri": 123},
        {"symbol": []},
        {"publishHour": None},
        {"publishHour": True},
        {"publishHour": 24},
    ],
)
async def test_unregistered_or_unsupported_valuation_never_fetches(overrides):
    from backend.integrations.investment_fundamentals import fetch_index_valuation

    def unexpected(**kwargs):
        pytest.fail("unverified configuration must not reach provider")

    fake = SimpleNamespace(stock_index_pe_lg=unexpected, stock_index_pb_lg=unexpected)
    result = await fetch_index_valuation(
        subject(**overrides), date(2026, 10, 1), date(2026, 10, 12), provider=fake
    )

    assert result["state"] in {"UNVERIFIED", "UNSUPPORTED"}
    assert result["pe"]["points"] == result["pb"]["points"] == []
    assert result["reasons"]


@pytest.mark.asyncio
async def test_no_valuation_registration_returns_unverified():
    from backend.integrations.investment_fundamentals import fetch_index_valuation

    result = await fetch_index_valuation(
        {}, date(2026, 10, 1), date(2026, 10, 12), provider=provider()
    )
    assert result["state"] == "UNVERIFIED"


@pytest.mark.asyncio
async def test_pe_failure_does_not_erase_real_pb_history():
    from backend.integrations.investment_fundamentals import fetch_index_valuation

    result = await fetch_index_valuation(
        subject(),
        date(2026, 10, 1),
        date(2026, 10, 12),
        provider=provider(pe=RuntimeError("provider down")),
    )

    assert result["pe"]["state"] == "UNAVAILABLE"
    assert result["pe"]["points"] == []
    assert result["pb"]["state"] == "READY"
    assert result["pb"]["points"][-1]["value"] == 1.5
    assert result["state"] == "PARTIAL"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", [-1, 0, float("nan"), float("inf"), "---", True])
async def test_invalid_pe_must_not_be_dropped_to_improve_percentile(bad_value):
    from backend.integrations.investment_fundamentals import fetch_index_valuation

    pe = pd.DataFrame(
        [
            {"日期": date(2026, 10, 9), "滚动市盈率": 16},
            {"日期": date(2026, 10, 12), "滚动市盈率": bad_value},
        ]
    )
    result = await fetch_index_valuation(
        subject(), date(2026, 10, 1), date(2026, 10, 12), provider=provider(pe=pe)
    )

    assert result["pe"]["state"] == "UNAVAILABLE"
    assert result["pe"]["points"] == []
    assert result["pb"]["state"] == "READY"


@pytest.mark.asyncio
async def test_short_csindex_pe1_schema_is_not_substituted_for_registered_ttm():
    from backend.integrations.investment_fundamentals import fetch_index_valuation

    pe = pd.DataFrame([{"日期": date(2026, 10, 12), "市盈率1": 15}])
    result = await fetch_index_valuation(
        subject(), date(2026, 10, 1), date(2026, 10, 12), provider=provider(pe=pe)
    )
    assert result["pe"]["state"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_latest_valuation_must_reach_requested_published_session():
    from backend.integrations.investment_fundamentals import fetch_index_valuation

    result = await fetch_index_valuation(
        subject(), date(2026, 10, 1), date(2026, 10, 13), provider=provider()
    )
    assert result["state"] == result["pe"]["state"] == result["pb"]["state"] == "STALE"
    assert result["pe"]["historyEnd"] == "2026-10-12"


@pytest.mark.asyncio
@pytest.mark.parametrize("fee", ["---", "不收取", "0.1", float("nan"), None, "-0.1%"])
async def test_undisclosed_fee_is_null_and_never_imputed_as_zero(fee):
    from backend.integrations.investment_fundamentals import fetch_fund_costs

    frame = pd.DataFrame(
        [
            [
                "管理费率",
                "0.50%（每年）",
                "托管费率",
                "0.10%（每年）",
                "销售服务费率",
                fee,
            ]
        ]
    )
    result = await fetch_fund_costs("015641", provider=provider(fees=frame))

    assert result["state"] == "UNAVAILABLE"
    assert result["managementRate"] == 0.005
    assert result["salesServiceRate"] is None
    assert result["totalRate"] is None


@pytest.mark.asyncio
async def test_named_fee_columns_are_read_explicitly():
    from backend.integrations.investment_fundamentals import fetch_fund_costs

    frame = pd.DataFrame(
        [{"管理费率": "0.50%", "托管费率": "0.10%", "销售服务费率": "0.20%"}]
    )
    result = await fetch_fund_costs("015641", provider=provider(fees=frame))
    assert result["totalRate"] == 0.008
    assert result["state"] == "READY"


@pytest.mark.asyncio
async def test_external_fee_failure_returns_unavailable_evidence():
    from backend.integrations.investment_fundamentals import fetch_fund_costs

    result = await fetch_fund_costs(
        "015641", provider=provider(fees=RuntimeError("down"))
    )
    assert result["state"] == "UNAVAILABLE"
    assert result["managementRate"] is None
    assert result["totalRate"] is None


@pytest.mark.asyncio
async def test_json_metadata_registration_is_used_without_guessing_symbols():
    import json
    from backend.integrations.investment_fundamentals import fetch_index_valuation

    registered = {
        "metadata": json.dumps({"research": {"valuation": subject()["valuation"]}})
    }
    result = await fetch_index_valuation(
        registered, date(2026, 10, 1), date(2026, 10, 12), provider=provider()
    )
    assert result["state"] == "READY"
    assert result["symbol"] == "沪深300"


@pytest.mark.asyncio
async def test_duplicate_valuation_dates_are_not_counted_twice():
    from backend.integrations.investment_fundamentals import fetch_index_valuation

    pe = pd.DataFrame([{"日期": date(2026, 10, 12), "滚动市盈率": 15}] * 2)
    result = await fetch_index_valuation(
        subject(), date(2026, 10, 1), date(2026, 10, 12), provider=provider(pe=pe)
    )
    assert result["pe"]["state"] == "UNAVAILABLE"
    assert result["pe"]["sampleCount"] == 0


@pytest.mark.asyncio
async def test_conflicting_disclosed_fees_are_unknown_not_last_row_wins():
    from backend.integrations.investment_fundamentals import fetch_fund_costs

    frame = pd.DataFrame(
        [
            {"管理费率": "0.50%", "托管费率": "0.10%", "销售服务费率": "0.00%"},
            {"管理费率": "0.90%", "托管费率": "0.10%", "销售服务费率": "0.00%"},
        ]
    )
    result = await fetch_fund_costs("015641", provider=provider(fees=frame))
    assert result["state"] == "UNAVAILABLE"
    assert result["managementRate"] is None
    assert result["custodyRate"] == 0.001
    assert result["totalRate"] is None


@pytest.mark.asyncio
async def test_unreasonably_large_fee_cannot_create_infinite_result():
    from backend.integrations.investment_fundamentals import fetch_fund_costs

    frame = pd.DataFrame(
        [["管理费率", "9" * 400 + "%", "托管费率", "0.10%", "销售服务费率", "0%"]]
    )
    result = await fetch_fund_costs("015641", provider=provider(fees=frame))
    assert result["state"] == "UNAVAILABLE"
    assert result["managementRate"] is None
    assert result["totalRate"] is None
