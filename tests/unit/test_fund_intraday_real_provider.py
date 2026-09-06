"""Real-provider contracts for the 14:30 off-exchange fund estimate path."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backend.integrations import market_data as md
from backend.market.service import (
    _select_fund_estimates,
    _source_name,
    _source_snapshot_quality,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_sina_fund_estimate_parser_keeps_real_value_base_nav_and_source_time() -> None:
    payload = (
        'var hq_str_fu_012733="易方达中证人工智能主题ETF联接A,14:30:00,'
        '1.9689,1.9978,1.9978,0,-1.4488,2026-09-07,1.9668,-1.5525";'
    )

    observations = md.parse_sina_fund_estimates(
        payload,
        ["012733"],
        retrieved_at=datetime(2026, 9, 7, 14, 31, tzinfo=SHANGHAI),
    )

    assert len(observations) == 1
    estimate = observations[0]
    assert estimate.symbol == "012733"
    assert estimate.field_code == md.F_ESTIMATED_NAV
    assert estimate.value == Decimal("1.9689")
    assert estimate.as_of == datetime(2026, 9, 7, 14, 30, tzinfo=SHANGHAI)
    assert estimate.quality_status == md.Q_ESTIMATED
    assert estimate.method_version == "sina/fund-estimate/v1"
    assert estimate.metadata == {
        "estimateSource": md.SINA_FUND_ESTIMATE_SOURCE_NAME,
        "estimateSourceKind": "EXTERNAL_PROVIDER",
        "confirmedNav": "1.9978",
        "providerEstimatedChangePct": "-1.4488",
        "explanation": "新浪基金估值提供的盘中参考值，不是基金公司正式净值。",
    }


def test_sina_fund_estimate_parser_marks_previous_trading_day_as_stale() -> None:
    payload = (
        'var hq_str_fu_161726="招商国证生物医药指数(LOF)A,16:04:00,'
        '0.3980,0.4031,0.9644,0,-1.2676,2026-09-04,0.3975,-1.3816";'
    )

    observations = md.parse_sina_fund_estimates(
        payload,
        ["161726"],
        retrieved_at=datetime(2026, 9, 7, 10, 0, tzinfo=SHANGHAI),
    )

    assert observations[0].value == Decimal("0.3980")
    assert observations[0].quality_status == md.Q_STALE


def test_sina_fund_estimate_parser_fails_closed_for_missing_or_non_finite_rows() -> None:
    payload = (
        'var hq_str_fu_005275="中欧创新成长灵活配置混合A,14:30:00,'
        'NaN,1.9289,1.9289,0,NaN,2026-09-07,1.9073,-1.119";'
    )

    observations = md.parse_sina_fund_estimates(
        payload,
        ["005275", "008552"],
        retrieved_at=datetime(2026, 9, 7, 14, 31, tzinfo=SHANGHAI),
    )

    assert [item.symbol for item in observations] == ["005275", "008552"]
    assert all(item.value is None for item in observations)
    assert all(item.quality_status == md.Q_UNAVAILABLE for item in observations)


def test_disclosure_parser_skips_nan_weight_instead_of_aborting_fund(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = pd.DataFrame(
        [
            {
                "股票代码": "600519",
                "股票名称": "贵州茅台",
                "占净值比例": "12.50",
                "季度": "2026年1季度股票投资明细",
            },
            {
                "股票代码": "000858",
                "股票名称": "五粮液",
                "占净值比例": float("nan"),
                "季度": "2026年1季度股票投资明细",
            },
        ]
    )
    monkeypatch.setattr(
        md,
        "_ak_import",
        lambda: SimpleNamespace(fund_portfolio_hold_em=lambda **_kwargs: frame),
    )

    rows = md._fetch_one_fund_disclosure("005275")

    assert len(rows) == 1
    assert rows[0].component_symbol == "600519"
    assert rows[0].weight == Decimal("0.125")


@pytest.mark.asyncio
async def test_disclosure_batch_isolates_one_fund_parser_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = md.FundDisclosure(
        fund_symbol="012733",
        component_symbol="600519",
        component_name="贵州茅台",
        weight=Decimal("0.10"),
        disclosed_on=date(2026, 6, 30),
        disclosure_label="2026年2季度股票投资明细",
    )

    def fetch_one(symbol: str) -> list[md.FundDisclosure]:
        if symbol == "005275":
            raise RuntimeError("provider row changed")
        return [good]

    monkeypatch.setattr(md, "_fetch_one_fund_disclosure", fetch_one)

    assert await md.fetch_fund_disclosures(["005275", "012733"]) == [good]


def _estimate(
    symbol: str,
    *,
    value: str | None,
    quality: str,
    method: str,
) -> md.Observation:
    return md.Observation(
        symbol=symbol,
        subject_type="FUND",
        field_code=md.F_ESTIMATED_NAV,
        value=Decimal(value) if value is not None else None,
        as_of=datetime(2026, 9, 7, 14, 30, tzinfo=SHANGHAI),
        quality_status=quality,
        method_version=method,
        metadata={"confirmedNav": "1.0000"},
    )


def test_fund_estimate_selection_prefers_current_real_provider_and_keeps_internal_fallback() -> None:
    provider = [
        _estimate(
            "012733",
            value="1.0200",
            quality=md.Q_ESTIMATED,
            method="sina/fund-estimate/v1",
        ),
        _estimate(
            "161726",
            value="0.9900",
            quality=md.Q_STALE,
            method="sina/fund-estimate/v1",
        ),
    ]
    internal = [
        _estimate(
            "012733",
            value="1.0100",
            quality=md.Q_ESTIMATED,
            method="weighted-disclosed-holdings/v1",
        ),
        _estimate(
            "161726",
            value="1.0300",
            quality=md.Q_ESTIMATED,
            method="weighted-disclosed-holdings/v1",
        ),
    ]

    selected = _select_fund_estimates(
        provider_estimates=provider,
        internal_estimates=internal,
        fund_symbols={"012733", "161726"},
    )

    assert [(item.symbol, item.value, item.method_version) for item in selected] == [
        ("012733", Decimal("1.0200"), "sina/fund-estimate/v1"),
        ("161726", Decimal("1.0300"), "weighted-disclosed-holdings/v1"),
    ]
    assert _source_name(selected[0]) == md.SINA_FUND_ESTIMATE_SOURCE_NAME


def test_estimated_only_source_snapshot_is_not_mislabeled_unavailable() -> None:
    observations = [
        _estimate(
            "012733",
            value="1.0200",
            quality=md.Q_ESTIMATED,
            method="sina/fund-estimate/v1",
        )
    ]

    assert _source_snapshot_quality(observations) == md.Q_ESTIMATED
