"""Explainable 14:30 off-exchange fund NAV estimation.

The estimator is deliberately deterministic.  It never asks a language model
to invent a NAV and it abstains when the subset of disclosed holdings with a
usable intraday return is too small.  The unobserved residual is represented by
an explicit benchmark assumption and also widens the uncertainty interval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class FundComponentReturn:
    instrument_code: str
    weight: Decimal
    return_rate: Decimal


@dataclass(frozen=True)
class FundNavEstimate:
    fund_code: str
    value: Decimal | None
    lower_value: Decimal | None
    upper_value: Decimal | None
    coverage_pct: Decimal
    as_of: datetime
    quality_status: str
    method_version: str
    explanation: str


def estimate_fund_nav(
    *,
    fund_code: str,
    confirmed_nav: Decimal,
    components: tuple[FundComponentReturn, ...],
    residual_benchmark_return: Decimal,
    as_of: datetime,
    minimum_coverage: Decimal = Decimal("0.60"),
    base_uncertainty_rate: Decimal = Decimal("0.003"),
    residual_uncertainty_rate: Decimal = Decimal("0.005"),
) -> FundNavEstimate:
    """Return an interval estimate or an explicit UNAVAILABLE result.

    Weights and return rates use fractions (``0.10`` means 10%).  Only
    components whose market return is known should be supplied by the caller;
    consequently ``coverage_pct`` is both disclosure and live-price coverage.
    """
    if confirmed_nav <= 0:
        raise ValueError("confirmed NAV must be positive")
    if not Decimal("0") <= minimum_coverage <= Decimal("1"):
        raise ValueError("minimum coverage must be between zero and one")
    if base_uncertainty_rate < 0 or residual_uncertainty_rate < 0:
        raise ValueError("uncertainty rates cannot be negative")
    if any(component.weight < 0 or component.weight > 1 for component in components):
        raise ValueError("component weight must be between zero and one")

    coverage = sum((component.weight for component in components), Decimal("0"))
    if coverage > 1:
        raise ValueError("component weights cannot exceed 100% in total")
    coverage_pct = (coverage * Decimal("100")).quantize(Decimal("0.01"))
    minimum_pct = (minimum_coverage * Decimal("100")).quantize(Decimal("0.01"))
    method = "weighted-disclosed-holdings/v1"

    if coverage < minimum_coverage:
        return FundNavEstimate(
            fund_code=fund_code,
            value=None,
            lower_value=None,
            upper_value=None,
            coverage_pct=coverage_pct,
            as_of=as_of,
            quality_status="UNAVAILABLE",
            method_version=method,
            explanation=(
                f"可用披露持仓与实时行情覆盖率 {coverage_pct}%，"
                f"低于 {minimum_pct}% 的估值门槛，系统已弃权。"
            ),
        )

    disclosed_return = sum(
        (component.weight * component.return_rate for component in components),
        Decimal("0"),
    )
    residual_weight = Decimal("1") - coverage
    estimated_return = (
        disclosed_return + residual_weight * residual_benchmark_return
    )
    value = (confirmed_nav * (Decimal("1") + estimated_return)).quantize(
        Decimal("0.0001")
    )
    uncertainty = base_uncertainty_rate + residual_weight * residual_uncertainty_rate
    lower = (value * (Decimal("1") - uncertainty)).quantize(Decimal("0.0001"))
    upper = (value * (Decimal("1") + uncertainty)).quantize(Decimal("0.0001"))
    residual_pct = (residual_weight * Decimal("100")).quantize(Decimal("0.01"))
    benchmark_pct = (residual_benchmark_return * Decimal("100")).quantize(
        Decimal("0.01")
    )
    return FundNavEstimate(
        fund_code=fund_code,
        value=value,
        lower_value=lower,
        upper_value=upper,
        coverage_pct=coverage_pct,
        as_of=as_of,
        quality_status="ESTIMATED",
        method_version=method,
        explanation=(
            f"可用披露持仓与实时行情覆盖率 {coverage_pct}%；"
            f"剩余 {residual_pct}% 使用显式基准收益 {benchmark_pct}%；"
            f"估值区间已计入基础误差和未观测资产不确定性。"
        ),
    )
