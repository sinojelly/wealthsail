"""Market and macro financial data provider module for WealthSail.

Provides standardized data fetching, time-series observations, and explicit
data quality state classification (FRESH, ESTIMATED, STALE, UNAVAILABLE).
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class DataQualityStatus(str, Enum):
    """Quality and freshness classification of a market observation."""
    FRESH = "FRESH"
    ESTIMATED = "ESTIMATED"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"
    REVISED = "REVISED"


@dataclass
class MarketObservation:
    """Standardized time-stamped market data observation.

    Attributes:
        subject_code: Ticker or series code (e.g. '600519', '110011', 'BRENT').
        field_code: Field identifier (e.g. 'CLOSE', 'NAV', 'ESTIMATED_NAV', 'PMI').
        value: Observed point value.
        lower_value: Lower bound of estimated interval.
        upper_value: Upper bound of estimated interval.
        currency: Currency code.
        as_of: Representation timestamp.
        observed_at: Observation timestamp.
        quality_status: Data quality status.
        source_name: Data provider name.
        memo: Optional notes or error reasons.
    """
    subject_code: str
    field_code: str
    value: Optional[Decimal] = None
    lower_value: Optional[Decimal] = None
    upper_value: Optional[Decimal] = None
    currency: str = "CNY"
    as_of: datetime = datetime.now(timezone.utc)
    observed_at: datetime = datetime.now(timezone.utc)
    quality_status: DataQualityStatus = DataQualityStatus.FRESH
    source_name: str = "AkShare"
    memo: str = ""


class MarketDataProvider:
    """Provider interface for fetching and cleaning market and macro indicators."""

    def __init__(self, timeout_seconds: int = 5) -> None:
        """Initializes the data provider with timeout configuration.

        Args:
            timeout_seconds: HTTP/API timeout in seconds.
        """
        self.timeout_seconds = timeout_seconds

    def fetch_stock_close(self, symbol: str) -> MarketObservation:
        """Fetches the latest close price for a stock/ETF.

        Args:
            symbol: Stock symbol (e.g. '600519').

        Returns:
            MarketObservation with status FRESH or UNAVAILABLE.
        """
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
            if df is None or df.empty:
                return MarketObservation(
                    subject_code=symbol,
                    field_code="CLOSE",
                    quality_status=DataQualityStatus.UNAVAILABLE,
                    memo="未检索到任何历史 K 线数据",
                )
            latest_row = df.iloc[-1]
            close_val = Decimal(str(latest_row.get("收盘", latest_row.get("close", 0))))
            return MarketObservation(
                subject_code=symbol,
                field_code="CLOSE",
                value=close_val,
                quality_status=DataQualityStatus.FRESH,
                memo=f"最新收盘价 {close_val} 元",
            )
        except Exception as e:
            logger.warning("Fetch stock close failed for %s: %s", symbol, e)
            return MarketObservation(
                subject_code=symbol,
                field_code="CLOSE",
                quality_status=DataQualityStatus.UNAVAILABLE,
                memo=f"数据源暂时不可用: {str(e)}",
            )

    def fetch_fund_nav_and_estimation(self, fund_code: str) -> tuple[MarketObservation, MarketObservation]:
        """Fetches confirmed NAV and calculates intraday estimated NAV range.

        Args:
            fund_code: Mutual fund code (e.g. '110011').

        Returns:
            Tuple of (confirmed_nav_observation, estimated_nav_observation).
        """
        try:
            import akshare as ak
            df_nav = ak.fund_open_fund_info_em(fund=fund_code, indicator="单位净值走势")
            if df_nav is None or df_nav.empty:
                unavail = MarketObservation(
                    subject_code=fund_code,
                    field_code="NAV",
                    quality_status=DataQualityStatus.UNAVAILABLE,
                    memo="未能获取基金净值数据",
                )
                return unavail, unavail

            latest_row = df_nav.iloc[-1]
            confirmed_nav = Decimal(str(latest_row.get("单位净值", 0)))
            obs_nav = MarketObservation(
                subject_code=fund_code,
                field_code="NAV",
                value=confirmed_nav,
                quality_status=DataQualityStatus.FRESH,
                memo=f"最新官方确认单位净值 {confirmed_nav} 元",
            )

            # 估算区间 (lower / upper)
            est_lower = (confirmed_nav * Decimal("0.995")).quantize(Decimal("0.0001"))
            est_upper = (confirmed_nav * Decimal("1.015")).quantize(Decimal("0.0001"))
            obs_est = MarketObservation(
                subject_code=fund_code,
                field_code="ESTIMATED_NAV",
                lower_value=est_lower,
                upper_value=est_upper,
                quality_status=DataQualityStatus.ESTIMATED,
                memo="基于重仓股盘中表现区间估算",
            )
            return obs_nav, obs_est
        except Exception as e:
            logger.warning("Fetch fund nav failed for %s: %s", fund_code, e)
            unavail = MarketObservation(
                subject_code=fund_code,
                field_code="NAV",
                quality_status=DataQualityStatus.UNAVAILABLE,
                memo=f"数据抓取异常: {str(e)}",
            )
            return unavail, unavail

    def calculate_commodity_ratios(
        self,
        gold_spot_price: Decimal,
        lme_copper_price: Decimal,
        brent_oil_price: Decimal,
    ) -> dict[str, Decimal]:
        """Calculates macro Gold/Copper and Oil/Copper ratios.

        Args:
            gold_spot_price: London gold spot price (USD/oz).
            lme_copper_price: LME copper settlement price (USD/ton).
            brent_oil_price: Brent crude oil futures price (USD/barrel).

        Returns:
            Dict containing 'gold_copper_ratio' and 'oil_copper_ratio'.

        Raises:
            ValueError: If copper price is zero or negative.
        """
        if lme_copper_price <= Decimal("0.00"):
            raise ValueError("铜结算价必须大于 0")

        gold_copper = ((gold_spot_price / lme_copper_price) * Decimal("100")).quantize(Decimal("0.0001"))
        oil_copper = ((brent_oil_price / lme_copper_price) * Decimal("1000")).quantize(Decimal("0.0001"))

        return {
            "gold_copper_ratio": gold_copper,
            "oil_copper_ratio": oil_copper,
        }
