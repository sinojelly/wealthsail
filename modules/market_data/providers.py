"""Market and macro financial data provider module for WealthSail.

Provides standardized data fetching, time-series observations, and explicit
data quality state classification (FRESH, ESTIMATED, STALE, UNAVAILABLE).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)
SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


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
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    quality_status: DataQualityStatus = DataQualityStatus.UNAVAILABLE
    source_name: str = ""
    memo: str = ""


@dataclass(frozen=True)
class FundComponentReturn:
    """Disclosed fund component weight and its current signed return rate."""

    weight: Decimal
    return_rate: Decimal


class MarketDataProvider:
    """Provider interface for fetching and cleaning market and macro indicators."""

    def __init__(self, timeout_seconds: int = 5) -> None:
        """Initializes the data provider with timeout configuration.

        Args:
            timeout_seconds: HTTP/API timeout in seconds.
        """
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _as_market_datetime(value: object) -> datetime:
        """Normalizes a provider date value to an Asia/Shanghai timestamp."""
        if isinstance(value, datetime):
            normalized = value
        elif isinstance(value, date):
            normalized = datetime.combine(value, datetime.min.time())
        else:
            normalized = datetime.fromisoformat(str(value))
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=SHANGHAI_TZ)
        return normalized

    @staticmethod
    def _daily_quality(as_of: datetime, observed_at: datetime) -> DataQualityStatus:
        """Conservatively classifies daily data without pretending it is live."""
        age_days = (observed_at.astimezone(SHANGHAI_TZ).date() - as_of.astimezone(SHANGHAI_TZ).date()).days
        return DataQualityStatus.FRESH if 0 <= age_days <= 4 else DataQualityStatus.STALE

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
            if close_val <= Decimal("0"):
                raise ValueError("行情缺少有效的正数收盘价")
            source_date = latest_row.get("日期", latest_row.get("date"))
            if source_date is None:
                raise ValueError("行情缺少来源日期，无法判断新鲜度")
            as_of = self._as_market_datetime(source_date)
            observed_at = datetime.now(timezone.utc)
            return MarketObservation(
                subject_code=symbol,
                field_code="CLOSE",
                value=close_val,
                as_of=as_of,
                observed_at=observed_at,
                quality_status=self._daily_quality(as_of, observed_at),
                source_name="AkShare",
                memo=f"来源日期 {as_of.date().isoformat()} 的确认收盘价 {close_val} 元",
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
            if confirmed_nav <= Decimal("0"):
                raise ValueError("基金数据缺少有效的正数单位净值")
            source_date = latest_row.get("净值日期", latest_row.get("日期", latest_row.get("date")))
            if source_date is None:
                raise ValueError("基金净值缺少来源日期，无法判断新鲜度")
            as_of = self._as_market_datetime(source_date)
            observed_at = datetime.now(timezone.utc)
            obs_nav = MarketObservation(
                subject_code=fund_code,
                field_code="NAV",
                value=confirmed_nav,
                as_of=as_of,
                observed_at=observed_at,
                quality_status=self._daily_quality(as_of, observed_at),
                source_name="AkShare",
                memo=f"来源日期 {as_of.date().isoformat()} 的官方确认单位净值 {confirmed_nav} 元",
            )

            # AkShare NAV history alone is insufficient for an intraday estimate.
            # A caller must provide disclosed holdings, component returns, and a
            # residual benchmark to estimate_fund_nav_range().
            obs_est = MarketObservation(
                subject_code=fund_code,
                field_code="ESTIMATED_NAV",
                as_of=as_of,
                observed_at=observed_at,
                quality_status=DataQualityStatus.UNAVAILABLE,
                source_name="WealthSailFundEstimator",
                memo="缺少持仓权重、成分行情或剩余资产基准，无法生成盘中估值",
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

    def estimate_fund_nav_range(
        self,
        fund_code: str,
        confirmed_nav: Decimal,
        components: tuple[FundComponentReturn, ...],
        residual_benchmark_return: Decimal,
        uncertainty_rate: Decimal = Decimal("0.003"),
        minimum_holding_coverage: Decimal = Decimal("0.60"),
        as_of: Optional[datetime] = None,
    ) -> MarketObservation:
        """Estimates an intraday NAV from explicit disclosed components.

        The calculation is deterministic. It abstains when disclosed holding
        coverage is too low and always exposes the coverage and uncertainty
        assumptions to downstream reports.
        """
        observed_at = datetime.now(timezone.utc)
        estimate_as_of = as_of or observed_at
        if confirmed_nav <= Decimal("0"):
            raise ValueError("确认净值必须大于 0")
        if uncertainty_rate < Decimal("0"):
            raise ValueError("估值不确定性不能为负数")
        if not Decimal("0") <= minimum_holding_coverage <= Decimal("1"):
            raise ValueError("最低持仓覆盖率必须在 0 到 1 之间")
        if any(component.weight < Decimal("0") or component.weight > Decimal("1") for component in components):
            raise ValueError("持仓权重必须在 0 到 1 之间")

        coverage = sum((component.weight for component in components), Decimal("0"))
        if coverage > Decimal("1"):
            raise ValueError("持仓权重合计不能超过 1")
        coverage_text = f"{(coverage * Decimal('100')).quantize(Decimal('0.01'))}%"
        if coverage < minimum_holding_coverage:
            return MarketObservation(
                subject_code=fund_code,
                field_code="ESTIMATED_NAV",
                as_of=estimate_as_of,
                observed_at=observed_at,
                quality_status=DataQualityStatus.UNAVAILABLE,
                source_name="WealthSailFundEstimator",
                memo=f"披露持仓覆盖率不足：{coverage_text}，低于最低阈值",
            )

        disclosed_contribution = sum(
            (component.weight * component.return_rate for component in components),
            Decimal("0"),
        )
        residual_weight = Decimal("1") - coverage
        estimated_return = disclosed_contribution + residual_weight * residual_benchmark_return
        estimated_nav = (confirmed_nav * (Decimal("1") + estimated_return)).quantize(Decimal("0.0001"))
        lower = (estimated_nav * (Decimal("1") - uncertainty_rate)).quantize(Decimal("0.0001"))
        upper = (estimated_nav * (Decimal("1") + uncertainty_rate)).quantize(Decimal("0.0001"))

        return MarketObservation(
            subject_code=fund_code,
            field_code="ESTIMATED_NAV",
            value=estimated_nav,
            lower_value=lower,
            upper_value=upper,
            as_of=estimate_as_of,
            observed_at=observed_at,
            quality_status=DataQualityStatus.ESTIMATED,
            source_name="WealthSailFundEstimator",
            memo=(
                f"method=weighted_holdings_v1；披露持仓覆盖率 {coverage_text}；"
                f"剩余资产使用显式基准收益；不确定性 ±{uncertainty_rate}"
            ),
        )

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
