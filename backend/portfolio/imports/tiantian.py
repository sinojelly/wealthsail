"""Validated source models for ``wealthsail-tiantian-import/v2`` packages."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class SourceModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


def _decimal_string(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a decimal string")
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} is not a valid decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _masked_account(value: str) -> str:
    """Accept an empty value or a display label ending in exactly four visible digits."""
    if value and not re.fullmatch(r"[^0-9]*\*{4}\d{4}", value):
        raise ValueError("账号必须为空或仅保留末四位")
    return value


class ManifestFile(SourceModel):
    name: str = Field(min_length=1, max_length=80)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: int = Field(ge=0)


class ManifestCounts(SourceModel):
    current_positions: int = Field(ge=0, le=1000)
    fund_transactions: int = Field(ge=0, le=20000)
    review_required: int = Field(ge=0, le=20000)
    cash_management_excluded: int = Field(ge=0)
    non_posting_excluded: int = Field(ge=0)


class ManifestSecurity(SourceModel):
    credentials_stored: Literal[False]
    access_token_stored: Literal[False]
    account_numbers_masked: Literal[True]


class TiantianManifest(SourceModel):
    schema_version: Literal["wealthsail-tiantian-import/v2"]
    collector_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    provider: Literal["TIANTIAN_FUND"]
    external_account_key: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    source_window_start: date
    source_window_end: date
    positions_as_of: datetime
    generated_at: datetime
    completeness: Literal["COMPLETE", "PARTIAL"]
    counts: ManifestCounts
    files: tuple[ManifestFile, ...]
    security: ManifestSecurity

    @model_validator(mode="after")
    def validate_dates(self) -> "TiantianManifest":
        if self.source_window_end < self.source_window_start:
            raise ValueError("source window end precedes start")
        if self.positions_as_of.tzinfo is None or self.generated_at.tzinfo is None:
            raise ValueError("manifest datetimes must include timezone offsets")
        return self


class TiantianPosition(SourceModel):
    source_provider: Literal["TIANTIAN_FUND"]
    source_record_id: str = Field(min_length=1, max_length=160)
    product_code: str = Field(pattern=r"^\d{6}$")
    product_name: str = Field(min_length=1, max_length=160)
    quantity: Decimal
    available_quantity: Decimal | None = None
    average_cost: Decimal
    cost_amount: Decimal
    market_value: Decimal
    holding_profit: Decimal | None = None
    holding_profit_rate: Decimal | None = None
    latest_nav: Decimal | None = None
    latest_nav_date: date | None = None
    account_mask: str = Field(default="", max_length=80)
    captured_at: datetime

    @field_validator(
        "quantity",
        "available_quantity",
        "average_cost",
        "cost_amount",
        "market_value",
        "holding_profit",
        "holding_profit_rate",
        "latest_nav",
        mode="before",
    )
    @classmethod
    def parse_decimals(cls, value: object, info) -> Decimal | None:
        if value is None:
            return None
        return _decimal_string(value, info.field_name)

    @field_validator("account_mask")
    @classmethod
    def validate_account_mask(cls, value: str) -> str:
        return _masked_account(value)

    @model_validator(mode="after")
    def validate_amounts(self) -> "TiantianPosition":
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.available_quantity is not None and self.available_quantity < 0:
            raise ValueError("available quantity cannot be negative")
        if self.average_cost < 0 or self.cost_amount < 0 or self.market_value < 0:
            raise ValueError("cost and market value cannot be negative")
        if self.latest_nav is not None and self.latest_nav <= 0:
            raise ValueError("latest NAV must be positive")
        if self.captured_at.tzinfo is None:
            raise ValueError("capturedAt must include timezone offset")
        return self


class TiantianTransaction(SourceModel):
    source_provider: Literal["TIANTIAN_FUND"]
    source_id: str = Field(min_length=1, max_length=160)
    trace_no: str = Field(default="", max_length=160)
    occurred_at: str = Field(min_length=10, max_length=40)
    product_code: str = Field(pattern=r"^\d{6}$")
    product_name: str = Field(min_length=1, max_length=160)
    business_code: str = Field(default="", max_length=40)
    business_type: str = Field(min_length=1, max_length=80)
    normalized_business_type: Literal[
        "PURCHASE",
        "REDEEM",
        "CONVERSION_IN",
        "CONVERSION_OUT",
        "DIVIDEND_CASH",
        "DIVIDEND_REINVEST",
        "FORCED_ADJUSTMENT",
        "NON_POSITION",
        "UNKNOWN",
    ]
    application_value: Decimal | None = None
    application_unit: str = Field(default="", max_length=20)
    confirmation_value: Decimal | None = None
    confirmation_unit: str = Field(default="", max_length=20)
    account_mask: str = Field(default="", max_length=80)
    status: str = Field(max_length=40)
    category: Literal["FUND_TRADE"]
    classification_reason: str = Field(max_length=80)
    detail_ref: str = Field(max_length=500)
    source_window_start: date
    source_window_end: date
    source_page: int = Field(ge=1)

    @field_validator("application_value", "confirmation_value", mode="before")
    @classmethod
    def parse_values(cls, value: object, info) -> Decimal | None:
        if value is None or value == "":
            return None
        return _decimal_string(value, info.field_name)

    @field_validator("account_mask")
    @classmethod
    def validate_account_mask(cls, value: str) -> str:
        return _masked_account(value)


class ExcludedSummary(SourceModel):
    cash_management: int = Field(ge=0)
    non_posting: int = Field(ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    completeness_reasons: tuple[str, ...] = ()


class TiantianPackage(SourceModel):
    manifest: TiantianManifest
    positions: tuple[TiantianPosition, ...]
    transactions: tuple[TiantianTransaction, ...]
    review_required: tuple[dict[str, Any], ...]
    excluded_summary: ExcludedSummary
