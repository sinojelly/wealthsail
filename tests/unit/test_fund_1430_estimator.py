"""Deterministic, fail-closed tests for the MVP 14:30 fund estimate."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.market.fund_estimator import FundComponentReturn, estimate_fund_nav
from backend.integrations import market_data as md
from backend.market.service import _build_fund_estimates


def test_estimate_fund_nav_exposes_coverage_interval_and_method() -> None:
    estimate = estimate_fund_nav(
        fund_code="110011",
        confirmed_nav=Decimal("1.0000"),
        components=(
            FundComponentReturn("600519", Decimal("0.40"), Decimal("0.02")),
            FundComponentReturn("000858", Decimal("0.30"), Decimal("-0.01")),
        ),
        residual_benchmark_return=Decimal("0"),
        as_of=datetime(2026, 8, 26, 6, 30, tzinfo=timezone.utc),
        base_uncertainty_rate=Decimal("0.002"),
    )

    assert estimate.value == Decimal("1.0050")
    assert estimate.lower_value == Decimal("1.0015")
    assert estimate.upper_value == Decimal("1.0085")
    assert estimate.coverage_pct == Decimal("70.00")
    assert estimate.quality_status == "ESTIMATED"
    assert estimate.method_version == "weighted-disclosed-holdings/v1"
    assert "剩余 30.00%" in estimate.explanation


def test_estimate_fund_nav_abstains_below_disclosure_coverage() -> None:
    estimate = estimate_fund_nav(
        fund_code="110011",
        confirmed_nav=Decimal("1.0000"),
        components=(
            FundComponentReturn("600519", Decimal("0.20"), Decimal("0.02")),
        ),
        residual_benchmark_return=Decimal("0"),
        as_of=datetime.now(timezone.utc),
    )

    assert estimate.value is None
    assert estimate.lower_value is None
    assert estimate.upper_value is None
    assert estimate.coverage_pct == Decimal("20.00")
    assert estimate.quality_status == "UNAVAILABLE"
    assert "低于 60.00%" in estimate.explanation


@pytest.mark.parametrize(
    ("nav", "components"),
    [
        (Decimal("0"), ()),
        (
            Decimal("1"),
            (FundComponentReturn("600519", Decimal("1.01"), Decimal("0")),),
        ),
    ],
)
def test_estimate_fund_nav_rejects_impossible_inputs(nav, components) -> None:
    with pytest.raises(ValueError):
        estimate_fund_nav(
            fund_code="110011",
            confirmed_nav=nav,
            components=components,
            residual_benchmark_return=Decimal("0"),
            as_of=datetime.now(timezone.utc),
        )


def test_pipeline_uses_only_fresh_component_returns_and_keeps_disclosure_date() -> None:
    as_of = datetime(2026, 8, 26, 6, 30, tzinfo=timezone.utc)
    observations = [
        md.Observation(
            symbol="110011",
            subject_type="FUND",
            field_code=md.F_NAV,
            value=Decimal("1"),
            as_of=as_of,
            quality_status=md.Q_STALE,
        ),
        md.Observation(
            symbol="600519",
            subject_type="INSTRUMENT",
            field_code=md.F_CLOSE,
            value=Decimal("1500"),
            return_rate=Decimal("0.02"),
            as_of=as_of,
            quality_status=md.Q_FRESH,
        ),
        md.Observation(
            symbol="000858",
            subject_type="INSTRUMENT",
            field_code=md.F_CLOSE,
            value=Decimal("100"),
            return_rate=Decimal("0.01"),
            as_of=as_of,
            quality_status=md.Q_STALE,
        ),
    ]
    estimates = _build_fund_estimates(
        observations=observations,
        disclosures=[
            {
                "fund_instrument_id": "fund-id",
                "component_code": "600519",
                "weight": Decimal("0.65"),
                "disclosed_on": datetime(2026, 6, 30).date(),
                "disclosure_label": "2026年2季度股票投资明细",
            },
            {
                "fund_instrument_id": "fund-id",
                "component_code": "000858",
                "weight": Decimal("0.20"),
                "disclosed_on": datetime(2026, 6, 30).date(),
                "disclosure_label": "2026年2季度股票投资明细",
            },
        ],
        fund_instrument_ids={"fund-id"},
        by_id={"fund-id": {"ticker": "110011"}},
        as_of=as_of,
    )

    assert len(estimates) == 1
    estimate = estimates[0]
    assert estimate.quality_status == md.Q_ESTIMATED
    assert estimate.metadata["coveragePct"] == "65.00"
    assert estimate.metadata["disclosedOn"] == "2026-06-30"
