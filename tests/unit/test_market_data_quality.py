"""Unit tests for market data quality states and macro ratio calculations."""

from decimal import Decimal
import pytest

from modules.market_data.providers import (
    DataQualityStatus,
    FundComponentReturn,
    MarketDataProvider,
    MarketObservation,
)


def test_commodity_ratio_calculation() -> None:
    """Tests Gold/Copper and Oil/Copper ratio calculations."""
    provider = MarketDataProvider()
    ratios = provider.calculate_commodity_ratios(
        gold_spot_price=Decimal("2500.00"),
        lme_copper_price=Decimal("9200.00"),
        brent_oil_price=Decimal("82.50"),
    )

    # Gold/Copper = (2500 / 9200) * 100 = 27.1739
    assert ratios["gold_copper_ratio"] == Decimal("27.1739")
    # Oil/Copper = (82.50 / 9200) * 1000 = 8.9674
    assert ratios["oil_copper_ratio"] == Decimal("8.9674")


def test_commodity_ratio_zero_copper_error() -> None:
    """Tests that non-positive copper price raises ValueError."""
    provider = MarketDataProvider()
    with pytest.raises(ValueError, match="铜结算价必须大于 0"):
        provider.calculate_commodity_ratios(
            gold_spot_price=Decimal("2500.00"),
            lme_copper_price=Decimal("0.00"),
            brent_oil_price=Decimal("82.50"),
        )


def test_market_observation_defaults_fail_closed_and_use_fresh_timestamps() -> None:
    """Unclassified observations are unavailable and do not share import-time timestamps."""
    first = MarketObservation(
        subject_code="600519",
        field_code="CLOSE",
        value=Decimal("1420.00"),
    )
    second = MarketObservation(subject_code="510300", field_code="CLOSE")

    assert first.quality_status is DataQualityStatus.UNAVAILABLE
    assert first.observed_at is not second.observed_at
    assert first.currency == "CNY"


def test_fund_estimate_uses_component_weights_and_explicit_uncertainty() -> None:
    """The estimated NAV must be derived from supplied holdings, not fixed multipliers."""
    provider = MarketDataProvider()
    estimate = provider.estimate_fund_nav_range(
        fund_code="110011",
        confirmed_nav=Decimal("1.0000"),
        components=(
            FundComponentReturn(weight=Decimal("0.40"), return_rate=Decimal("0.02")),
            FundComponentReturn(weight=Decimal("0.30"), return_rate=Decimal("-0.01")),
        ),
        residual_benchmark_return=Decimal("0.00"),
        uncertainty_rate=Decimal("0.002"),
    )

    assert estimate.quality_status is DataQualityStatus.ESTIMATED
    assert estimate.value == Decimal("1.0050")
    assert estimate.lower_value == Decimal("1.0030")
    assert estimate.upper_value == Decimal("1.0070")
    assert "70.00%" in estimate.memo


def test_fund_estimate_abstains_when_holding_coverage_is_too_low() -> None:
    """Sparse disclosures must not be converted into a confident intraday estimate."""
    provider = MarketDataProvider()
    estimate = provider.estimate_fund_nav_range(
        fund_code="110011",
        confirmed_nav=Decimal("1.0000"),
        components=(
            FundComponentReturn(weight=Decimal("0.20"), return_rate=Decimal("0.02")),
        ),
        residual_benchmark_return=Decimal("0.00"),
        minimum_holding_coverage=Decimal("0.60"),
    )

    assert estimate.quality_status is DataQualityStatus.UNAVAILABLE
    assert estimate.value is None
    assert "覆盖率不足" in estimate.memo
