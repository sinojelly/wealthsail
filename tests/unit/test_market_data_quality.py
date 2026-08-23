"""Unit tests for market data quality states and macro ratio calculations."""

from decimal import Decimal
import pytest

from modules.market_data.providers import MarketDataProvider, DataQualityStatus, MarketObservation


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


def test_market_observation_quality_defaults() -> None:
    """Tests default properties of a MarketObservation."""
    obs = MarketObservation(
        subject_code="600519",
        field_code="CLOSE",
        value=Decimal("1420.00"),
        quality_status=DataQualityStatus.FRESH,
    )
    assert obs.subject_code == "600519"
    assert obs.field_code == "CLOSE"
    assert obs.value == Decimal("1420.00")
    assert obs.quality_status == DataQualityStatus.FRESH
    assert obs.currency == "CNY"
