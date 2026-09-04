"""Portfolio service: position/NAV aggregation and PnL computation.

Financial precision invariant: every amount is Decimal; the API layer
serializes them as decimal strings. No float is used anywhere in this module.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import asyncpg

from backend.common.database import tenant_transaction
from backend.common.formatting import fmt_money, fmt_qty, merge_quality, pct
from backend.dependencies import SHANGHAI_TZ, now_shanghai, write_audit
from backend.errors import (
    DataUnavailableError,
    IdempotencyConflictError,
    NotFoundError,
    ValidationError_,
    VersionConflictError,
)
from backend.integrations import market_data as market_data_adapter
from backend.market import repository as market_repository
from backend.portfolio import repository
from backend.portfolio.scope import AssetScope

# Asset type → frontend allocation category (A股 / 场内ETF / 场外基金 / 现金).
ASSET_CATEGORY: dict[str, str] = {
    "CN_STOCK": "A股",
    "EXCHANGE_FUND": "场内ETF",
    "OFF_EXCHANGE_FUND": "场外基金",
    "BOND": "债券",
    "GOLD_SPOT": "黄金",
}

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
_CSV_IMPORT_MAX_ROWS = 200
_CSV_IMPORT_MAX_CHARS = 100_000
_CSV_IMPORT_VERIFY_CONCURRENCY = 8
_CSV_IMPORT_MAX_PROVIDER_SECONDS = 45.0

logger = logging.getLogger("wealthsail.portfolio")


@dataclass(frozen=True)
class CsvPositionRow:
    row_number: int
    asset_type: str
    symbol: str
    display_name: str
    quantity: Decimal
    unit_cost: Decimal


def _csv_decimal(value: str, *, row_number: int, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except Exception as exc:  # Decimal exposes several syntax exceptions.
        raise ValidationError_(
            "CSV 包含无效字段",
            field_errors=[
                {"field": f"rows[{row_number}].{field}", "message": "必须是十进制数"}
            ],
        ) from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValidationError_(
            "CSV 包含无效字段",
            field_errors=[
                {"field": f"rows[{row_number}].{field}", "message": "必须大于 0"}
            ],
        )
    _sign, _digits, exponent = parsed.as_tuple()
    if exponent < -10 or parsed >= Decimal("1000000000000000000"):
        raise ValidationError_(
            "CSV 包含无效字段",
            field_errors=[
                {
                    "field": f"rows[{row_number}].{field}",
                    "message": "整数最多 18 位且小数不超过 10 位",
                }
            ],
        )
    return parsed


def parse_csv_position_rows(csv_data: str) -> list[CsvPositionRow]:
    """Parse the bounded five-column CSV contract without retaining raw input."""
    if not csv_data.strip():
        raise ValidationError_(
            "CSV 数据不能为空",
            field_errors=[{"field": "csvData", "message": "请输入至少一行持仓数据"}],
        )
    if len(csv_data) > _CSV_IMPORT_MAX_CHARS:
        raise ValidationError_(
            "CSV 数据过大",
            field_errors=[
                {
                    "field": "csvData",
                    "message": f"不能超过 {_CSV_IMPORT_MAX_CHARS} 个字符",
                }
            ],
        )
    try:
        parsed_rows = list(
            csv.reader(io.StringIO(csv_data.lstrip("\ufeff")), strict=True)
        )
    except csv.Error as exc:
        raise ValidationError_(
            "CSV 格式无效",
            field_errors=[{"field": "csvData", "message": str(exc)}],
        ) from exc
    non_empty = [row for row in parsed_rows if any(cell.strip() for cell in row)]
    if len(non_empty) > _CSV_IMPORT_MAX_ROWS:
        raise ValidationError_(
            "CSV 行数超过限制",
            field_errors=[
                {
                    "field": "csvData",
                    "message": f"每批最多 {_CSV_IMPORT_MAX_ROWS} 行",
                }
            ],
        )

    result: list[CsvPositionRow] = []
    for row_number, raw in enumerate(non_empty, start=1):
        if len(raw) != 5:
            raise ValidationError_(
                "CSV 每行必须包含 5 列",
                field_errors=[
                    {
                        "field": f"rows[{row_number}]",
                        "message": "格式应为资产类型,代码,名称,份额,成本价",
                    }
                ],
            )
        asset_type, raw_symbol, display_name, raw_quantity, raw_cost = (
            value.strip() for value in raw
        )
        asset_type = asset_type.upper()
        if asset_type not in PUBLIC_ASSET_TYPE:
            raise ValidationError_(
                "CSV 包含不支持的资产类型",
                field_errors=[
                    {
                        "field": f"rows[{row_number}].assetType",
                        "message": "仅支持 STOCK / ETF / FUND",
                    }
                ],
            )
        if not re.fullmatch(r"\d{6}", raw_symbol):
            raise ValidationError_(
                "CSV 包含无效证券代码",
                field_errors=[
                    {
                        "field": f"rows[{row_number}].symbol",
                        "message": "须为 6 位数字代码",
                    }
                ],
            )
        if not display_name or len(display_name) > 200:
            raise ValidationError_(
                "CSV 包含无效名称",
                field_errors=[
                    {
                        "field": f"rows[{row_number}].name",
                        "message": "名称必填且不能超过 200 个字符",
                    }
                ],
            )
        result.append(
            CsvPositionRow(
                row_number=row_number,
                asset_type=asset_type,
                symbol=raw_symbol,
                display_name=display_name,
                quantity=_csv_decimal(
                    raw_quantity, row_number=row_number, field="quantity"
                ),
                unit_cost=_csv_decimal(
                    raw_cost, row_number=row_number, field="avgCost"
                ),
            )
        )
    return result


def _d(value: object) -> Decimal:
    """Coerce a NUMERIC/None value to Decimal, never through float."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(value)
    raise TypeError(f"unexpected numeric type: {type(value).__name__}")


def _row_value(row: asyncpg.Record | dict, key: str, default=None):
    return row.get(key, default)


def _aggregate_holding_pnl(
    positions: list[asyncpg.Record] | list[dict],
) -> tuple[Decimal | None, Decimal | None]:
    """Aggregate unrealized PnL from current value and imported cost basis.

    This is intentionally a current-position metric, not lifetime performance:
    ``sum(market value - quantity * average cost)``. A partial portfolio must
    not be presented as a complete aggregate, so one missing valuation makes
    both results unavailable.
    """
    if not positions:
        return ZERO, None
    if any(_row_value(row, "market_value") is None for row in positions):
        return None, None

    total_market_value = sum(
        (_d(_row_value(row, "market_value")) for row in positions), ZERO
    )
    total_cost = sum(
        (
            _d(_row_value(row, "quantity")) * _d(_row_value(row, "avg_cost"))
            for row in positions
        ),
        ZERO,
    )
    holding_pnl = total_market_value - total_cost
    holding_pnl_rate = pct(holding_pnl, total_cost) if total_cost else None
    return holding_pnl, holding_pnl_rate


def _aggregate_cumulative_pnl(
    navs: list[asyncpg.Record] | list[dict],
) -> tuple[Decimal | None, str | None, bool]:
    """Aggregate account performance without inventing pre-baseline history.

    Per account: ``initial historical PnL + current NAV - baseline NAV - net
    external inflow``.  A missing current valuation or baseline invalidates the
    portfolio aggregate; publishing a partial cumulative result would be more
    misleading than returning no value.
    """
    if not navs:
        return None, None, False
    required = (
        "total_nav",
        "performance_baseline_as_of",
        "performance_baseline_total_asset",
        "initial_cumulative_pnl",
    )
    if any(any(_row_value(row, field) is None for field in required) for row in navs):
        return None, None, False

    result = sum(
        (
            _d(_row_value(row, "initial_cumulative_pnl"))
            + _d(_row_value(row, "total_nav"))
            - _d(_row_value(row, "performance_baseline_total_asset"))
            - _d(_row_value(row, "net_external_flow"))
            for row in navs
        ),
        ZERO,
    )
    baseline_dates = {_row_value(row, "performance_baseline_as_of") for row in navs}
    earliest = min(baseline_dates)
    return result, _date_text(earliest), len(baseline_dates) > 1


def _position_display_quality(row: asyncpg.Record | dict) -> str:
    """Apply trading-day freshness to the current fund-position read model.

    Historical observations retain their persisted provenance quality.  A
    Friday official NAV, however, is still the current disclosed valuation on
    a weekend or exchange holiday and must not be shown to users as expired.
    """
    persisted = str(_row_value(row, "data_quality_status", "UNAVAILABLE"))
    if persisted != "STALE" or _row_value(row, "asset_type") != "OFF_EXCHANGE_FUND":
        return persisted
    current_nav_date = _row_value(row, "current_nav_date")
    latest_trading_date = _row_value(row, "latest_trading_date")
    if (
        current_nav_date is not None
        and latest_trading_date is not None
        and current_nav_date == latest_trading_date
    ):
        return "FRESH"
    return persisted


def _date_text(value) -> str | None:
    return value.isoformat() if value is not None else None


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return format(value if isinstance(value, Decimal) else Decimal(str(value)), "f")


def _json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _intraday_estimate_dto(row: asyncpg.Record | dict) -> dict | None:
    """Map the latest persisted FUND_1430 observation without relabeling NAV."""
    if _row_value(row, "asset_type") != "OFF_EXCHANGE_FUND":
        return None
    as_of = _row_value(row, "estimated_nav_as_of")
    if as_of is None:
        return None
    metadata = _json_object(_row_value(row, "estimated_nav_metadata"))
    value = _row_value(row, "estimated_nav")
    confirmed_nav_raw = metadata.get("confirmedNav")
    confirmed_nav = None
    try:
        if confirmed_nav_raw is not None:
            confirmed_nav = Decimal(str(confirmed_nav_raw))
    except (ArithmeticError, ValueError):
        confirmed_nav = None
    change = None
    if value is not None and confirmed_nav is not None and confirmed_nav > 0:
        change = pct(_d(value) - confirmed_nav, confirmed_nav)
    disclosed_on = metadata.get("disclosedOn")
    return {
        "value": _decimal_text(value),
        "changePct": _decimal_text(change),
        "lower": _decimal_text(_row_value(row, "estimated_nav_lower")),
        "upper": _decimal_text(_row_value(row, "estimated_nav_upper")),
        "coveragePct": (
            str(metadata["coveragePct"])
            if metadata.get("coveragePct") is not None
            else None
        ),
        "disclosedOn": (
            None
            if disclosed_on is None or str(disclosed_on) == "None"
            else str(disclosed_on)
        ),
        "confirmedNav": _decimal_text(confirmed_nav),
        "confirmedNavAsOf": metadata.get("confirmedNavAsOf"),
        "asOf": _date_text(as_of),
        "quality": merge_quality(
            [str(_row_value(row, "estimated_nav_quality", "UNAVAILABLE"))]
        ),
        "methodVersion": _row_value(row, "estimated_nav_method"),
        "explanation": metadata.get("explanation"),
    }


def _position_period_dto(row: asyncpg.Record | dict) -> tuple[dict | None, dict | None]:
    """Map persisted metric facts without recalculating financial percentages."""
    if row["asset_type"] != "OFF_EXCHANGE_FUND":
        return None, None
    calculation_status = _row_value(row, "calculation_status")
    if calculation_status is None:
        return (
            {
                "startedOn": None,
                "days": None,
                "method": "INSUFFICIENT_DATA",
                "confidence": "NONE",
                "canConfirm": False,
            },
            {
                "high": None,
                "highDate": None,
                "low": None,
                "lowDate": None,
                "fromHighPct": None,
                "fromLowPct": None,
                "rangePositionPct": None,
                "status": "PENDING",
                "qualityReasons": ["METRICS_NOT_READY"],
                "asOf": None,
            },
        )
    holding_started_on = _row_value(row, "holding_started_on")
    confirmed_started_on = _row_value(row, "confirmed_holding_started_on")
    confirmation_matches_metric = (
        confirmed_started_on is not None and confirmed_started_on == holding_started_on
    )
    quality_reasons = list(_row_value(row, "quality_reasons", []) or [])
    if confirmation_matches_metric:
        quality_reasons = [
            reason for reason in quality_reasons if reason != "PARTIAL_HOLDING_HISTORY"
        ]
    effective_status = str(calculation_status)
    if (
        confirmation_matches_metric
        and effective_status == "PARTIAL"
        and not quality_reasons
    ):
        effective_status = "READY"
    return (
        {
            "startedOn": _date_text(holding_started_on),
            "days": _row_value(row, "holding_days"),
            "method": (
                "USER_CONFIRMED"
                if confirmation_matches_metric
                else _row_value(row, "holding_start_method")
            ),
            "confidence": (
                "HIGH"
                if confirmation_matches_metric
                else _row_value(row, "holding_start_confidence", "NONE")
            ),
            "canConfirm": _row_value(row, "holding_source_binding_id") is not None,
        },
        {
            "high": _decimal_text(_row_value(row, "period_high_nav")),
            "highDate": _date_text(_row_value(row, "period_high_date")),
            "low": _decimal_text(_row_value(row, "period_low_nav")),
            "lowDate": _date_text(_row_value(row, "period_low_date")),
            "fromHighPct": _decimal_text(_row_value(row, "from_high_pct")),
            "fromLowPct": _decimal_text(_row_value(row, "from_low_pct")),
            "rangePositionPct": _decimal_text(_row_value(row, "range_position_pct")),
            "status": effective_status,
            "qualityReasons": quality_reasons,
            # This is the official current-NAV disclosure date, not the API
            # request time and never an intraday/realtime label.
            "asOf": _date_text(_row_value(row, "current_nav_date")),
        },
    )


async def get_summary(
    conn: asyncpg.Connection,
    tenant_id: str,
    as_of: datetime,
    *,
    scope: AssetScope | None = None,
) -> dict:
    """Aggregate the latest per-account NAV snapshots into the dashboard summary."""
    owner_user_id = scope.owner_user_id if scope is not None else None
    navs = (
        await repository.latest_nav_per_account(
            conn, tenant_id, owner_user_id=owner_user_id
        )
        if owner_user_id is not None
        else await repository.latest_nav_per_account(conn, tenant_id)
    )
    if not navs:
        # A newly created tenant has no observed portfolio facts yet. Preserve
        # that distinction with null valuations instead of fabricating zeros or
        # reporting the valid onboarding state as a service outage.
        return {
            "totalAsset": None,
            "knownValue": "0.00",
            "valuationCoveragePct": "0.00",
            "unpricedPositionCount": 0,
            "valuationStatus": "UNAVAILABLE",
            "valuationPendingCount": 0,
            "currency": "CNY",
            "cashBalance": "0.00",
            "positionsMarketValue": None,
            "dailyPnl": None,
            "dailyPnlRate": None,
            "holdingPnl": None,
            "holdingPnlRate": None,
            "cumulativePnl": None,
            "cumulativePnlRate": None,
            "performanceBaselineAsOf": None,
            "performanceBaselineMixed": False,
            "allocation": [],
            "qualityStatus": "MISSING",
            "asOf": None,
            "positionCount": 0,
        }

    complete = all(r["total_nav"] is not None for r in navs)
    total_asset = sum((_d(r["total_nav"]) for r in navs), ZERO) if complete else None
    known_value = sum(
        (
            _d(r.get("known_value") if hasattr(r, "get") else r["known_value"])
            for r in navs
        ),
        ZERO,
    )
    cash = sum((_d(r["cash_balance"]) for r in navs), ZERO)
    positions_value = (
        sum((_d(r["positions_market_value"]) for r in navs), ZERO)
        if all(r["positions_market_value"] is not None for r in navs)
        else None
    )
    daily_pnl = (
        sum((_d(r["daily_pnl"]) for r in navs), ZERO)
        if all(r["daily_pnl"] is not None for r in navs)
        else None
    )
    quality = merge_quality(r["data_quality_status"] for r in navs)
    coverage = min(_d(r.get("valuation_coverage_pct", Decimal("100"))) for r in navs)
    unpriced_count = sum(int(r.get("unpriced_position_count", 0) or 0) for r in navs)

    positions = (
        await repository.latest_positions(conn, tenant_id, owner_user_id=owner_user_id)
        if owner_user_id is not None
        else await repository.latest_positions(conn, tenant_id)
    )
    holding_pnl, holding_pnl_rate = _aggregate_holding_pnl(positions)
    cumulative_pnl, baseline_as_of, baseline_mixed = _aggregate_cumulative_pnl(navs)
    allocation = _build_allocation(positions, cash, total_asset)
    valuation_pending_count = sum(
        1
        for row in positions
        if (
            _row_value(row, "market_price") is None
            or _row_value(row, "market_value") is None
        )
        and _row_value(row, "current_nav") is not None
    )
    if total_asset is not None and unpriced_count == 0:
        valuation_status = "READY"
    elif unpriced_count > 0 and valuation_pending_count == unpriced_count:
        valuation_status = "SYNCING"
    elif known_value > 0:
        valuation_status = "PARTIAL"
    else:
        valuation_status = "UNAVAILABLE"

    return {
        "totalAsset": fmt_money(total_asset) if total_asset is not None else None,
        "knownValue": fmt_money(known_value),
        "valuationCoveragePct": fmt_money(coverage),
        "unpricedPositionCount": unpriced_count,
        "valuationStatus": valuation_status,
        "valuationPendingCount": valuation_pending_count,
        "currency": "CNY",
        "cashBalance": fmt_money(cash),
        "positionsMarketValue": (
            fmt_money(positions_value) if positions_value is not None else None
        ),
        "dailyPnl": fmt_money(daily_pnl) if daily_pnl is not None else None,
        # A reliable daily return requires cash-flow-adjusted/TWR data. Until
        # that projection is available, do not publish a fabricated ratio.
        "dailyPnlRate": None,
        "holdingPnl": fmt_money(holding_pnl) if holding_pnl is not None else None,
        "holdingPnlRate": (
            fmt_money(holding_pnl_rate) if holding_pnl_rate is not None else None
        ),
        # Lifetime history before the first WealthSail baseline is optional;
        # after that baseline, only confirmed external CASH_IN/CASH_OUT adjusts
        # performance. Internal trades must never be treated as new capital.
        "cumulativePnl": (
            fmt_money(cumulative_pnl) if cumulative_pnl is not None else None
        ),
        "cumulativePnlRate": None,
        "performanceBaselineAsOf": baseline_as_of,
        "performanceBaselineMixed": baseline_mixed,
        "allocation": allocation,
        "qualityStatus": quality,
        # A combined portfolio is only as current as its oldest account
        # snapshot. Returning the request clock would overstate freshness.
        "asOf": min(r["as_of"] for r in navs),
        "positionCount": len(positions),
    }


def _performance_baseline_dto(row: asyncpg.Record | dict) -> dict:
    return {
        "accountId": str(row["account_id"]),
        "accountName": str(_row_value(row, "account_name") or "未命名账户"),
        "baselineAsOf": _date_text(row["baseline_as_of"]),
        "baselineTotalAsset": fmt_money(_d(row["baseline_total_asset"])),
        "initialCumulativePnl": fmt_money(_d(row["initial_cumulative_pnl"])),
        "source": str(row["source"]),
        "version": int(row["version"]),
        "createdAt": _date_text(_row_value(row, "created_at")),
    }


async def list_performance_baselines(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    owner_user_id: str,
) -> list[dict]:
    rows = await repository.list_performance_baselines(
        conn, tenant_id=tenant_id, owner_user_id=owner_user_id
    )
    return [_performance_baseline_dto(row) for row in rows]


def _performance_baseline_request_hash(
    *, account_id: str, initial_cumulative_pnl: Decimal, expected_version: int
) -> str:
    canonical = json.dumps(
        {
            "accountId": account_id,
            "initialCumulativePnl": format(initial_cumulative_pnl, "f"),
            "expectedVersion": expected_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def revise_performance_baseline(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    actor_user_id: str,
    account_id: str,
    initial_cumulative_pnl: Decimal,
    expected_version: int,
    idempotency_key: str,
) -> dict:
    """Append one reviewed historical-PnL revision to an account baseline."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")
    if expected_version <= 0:
        raise ValidationError_("If-Match 必须是正整数版本")
    value = Decimal(str(initial_cumulative_pnl))
    if not value.is_finite() or abs(value) >= Decimal("100000000000000000000"):
        raise ValidationError_("跟踪前历史累计收益超出允许范围")
    value = value.quantize(CENT)
    request_hash = _performance_baseline_request_hash(
        account_id=account_id,
        initial_cumulative_pnl=value,
        expected_version=expected_version,
    )

    await repository.lock_performance_baseline_command(
        conn, tenant_id=tenant_id, idempotency_key=idempotency_key
    )

    replay = await repository.find_performance_baseline_by_idempotency(
        conn,
        tenant_id=tenant_id,
        account_id=account_id,
        owner_user_id=actor_user_id,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        if str(replay["request_hash"]) != request_hash:
            raise IdempotencyConflictError(
                "Idempotency-Key 已用于不同的累计收益基线设置"
            )
        return _performance_baseline_dto(replay)

    current = await repository.lock_latest_performance_baseline(
        conn,
        tenant_id=tenant_id,
        account_id=account_id,
        owner_user_id=actor_user_id,
    )
    if current is None:
        raise NotFoundError("账户累计收益基线不存在或当前用户无权设置")
    current_version = int(current["version"])
    if current_version != expected_version:
        raise VersionConflictError(
            f"累计收益基线已更新（当前版本 {current_version}），请刷新后重试"
        )

    inserted = await repository.insert_performance_baseline_revision(
        conn,
        tenant_id=tenant_id,
        account_id=account_id,
        baseline_as_of=current["baseline_as_of"],
        baseline_total_asset=_d(current["baseline_total_asset"]),
        initial_cumulative_pnl=value,
        version=current_version + 1,
        created_by=actor_user_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    await write_audit(
        conn,
        tenant_id=tenant_id,
        actor_type="USER",
        actor_id=actor_user_id,
        action="PERFORMANCE_BASELINE_REVISED",
        resource_type="ledger.account_performance_baselines",
        resource_id=account_id,
        correlation_id=idempotency_key,
        details={
            "version": current_version + 1,
            "baselineAsOf": current["baseline_as_of"].isoformat(),
            "initialCumulativePnl": format(value, "f"),
        },
    )
    return _performance_baseline_dto(inserted)


def _build_allocation(
    positions: list[asyncpg.Record], cash: Decimal, total_asset: Decimal | None
) -> list[dict]:
    """Allocation by asset category (A股/场内ETF/场外基金/现金 …) with weights."""
    by_category: dict[str, Decimal] = {}
    for row in positions:
        cat = ASSET_CATEGORY.get(row["asset_type"], "其他")
        by_category[cat] = by_category.get(cat, ZERO) + _d(row["market_value"])
    by_category["现金"] = cash

    return [
        {
            "category": category,
            "amount": fmt_money(amount),
            "value": fmt_money(pct(amount, total_asset))
            if total_asset is not None
            else None,
        }
        for category, amount in sorted(
            by_category.items(), key=lambda kv: kv[1], reverse=True
        )
    ]


async def get_positions(
    conn: asyncpg.Connection,
    tenant_id: str,
    *,
    scope: AssetScope | None = None,
) -> list[dict]:
    """Latest holdings with market value, weight, PnL and quality status."""
    owner_user_id = scope.owner_user_id if scope is not None else None
    if owner_user_id is None:
        rows = await repository.latest_positions(conn, tenant_id)
        navs = await repository.latest_nav_per_account(conn, tenant_id)
    else:
        rows = await repository.latest_positions(
            conn, tenant_id, owner_user_id=owner_user_id
        )
        navs = await repository.latest_nav_per_account(
            conn, tenant_id, owner_user_id=owner_user_id
        )
    total_asset = (
        sum((_d(r["total_nav"]) for r in navs), ZERO)
        if all(r["total_nav"] is not None for r in navs)
        else None
    )

    result: list[dict] = []
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            _d(row["market_value"])
            if row["market_value"] is not None
            else Decimal("-Infinity")
        ),
        reverse=True,
    )
    for row in sorted_rows:
        quantity = _d(row["quantity"])
        avg_cost = _d(row["avg_cost"])
        price = row["market_price"]
        market_value = (
            _d(row["market_value"]) if row["market_value"] is not None else None
        )
        pnl = row["unrealized_pnl"]

        if market_value is None and price is not None:
            market_value = (quantity * _d(price)).quantize(CENT)
        if pnl is None and market_value is not None:
            pnl = market_value - (quantity * avg_cost)
        if price is not None and market_value is not None:
            valuation_status = "READY"
        elif _row_value(row, "current_nav") is not None:
            valuation_status = "SYNCING"
        else:
            valuation_status = "UNAVAILABLE"
        cost_value = quantity * avg_cost
        pnl_rate = pct(_d(pnl), cost_value) if pnl is not None and cost_value else None
        holding_period, range_metrics = _position_period_dto(row)
        owner_ids = list(_row_value(row, "owner_user_ids", []) or [])
        owner_names = list(_row_value(row, "owner_names", []) or [])

        result.append(
            {
                "instrumentId": str(row["instrument_id"]),
                "symbol": row["ticker"],
                "name": row["canonical_name"],
                "type": ASSET_CATEGORY.get(row["asset_type"], row["asset_type"]),
                "quantity": fmt_qty(quantity),
                "avgCost": fmt_money(avg_cost),
                "price": fmt_money(_d(price)) if price is not None else None,
                "marketValue": fmt_money(market_value)
                if market_value is not None
                else None,
                "weight": (
                    fmt_money(pct(market_value, total_asset))
                    if market_value is not None and total_asset is not None
                    else None
                ),
                "pnl": fmt_money(_d(pnl)) if pnl is not None else None,
                "pnlRate": fmt_money(pnl_rate) if pnl_rate is not None else None,
                "quality": merge_quality([_position_display_quality(row)]),
                "valuationDate": _date_text(_row_value(row, "current_nav_date")),
                "valuationStatus": valuation_status,
                "accountId": str(row["account_id"]),
                "account": {
                    "id": str(row["account_id"]),
                    "name": _row_value(row, "account_name"),
                    "ownershipScope": _row_value(row, "ownership_scope", "UNASSIGNED"),
                    "owners": [
                        {
                            "userId": str(owner_id),
                            "name": owner_names[index]
                            if index < len(owner_names)
                            else None,
                        }
                        for index, owner_id in enumerate(owner_ids)
                    ],
                },
                "holdingPeriod": holding_period,
                "rangeMetrics": range_metrics,
                "intradayEstimate": _intraday_estimate_dto(row),
            }
        )
    return result


async def get_nav_trend(
    conn: asyncpg.Connection,
    tenant_id: str,
    days: int = 60,
    *,
    scope: AssetScope | None = None,
) -> dict:
    """Daily NAV trend (points) — benchmark left null until index data exists."""
    owner_user_id = scope.owner_user_id if scope is not None else None
    rows = (
        await repository.nav_history(
            conn, tenant_id, limit=days, owner_user_id=owner_user_id
        )
        if owner_user_id is not None
        else await repository.nav_history(conn, tenant_id, limit=days)
    )
    points = [
        {
            "date": r["as_of"].isoformat(),
            "totalNav": (
                fmt_money(_d(r["total_nav"])) if r["total_nav"] is not None else None
            ),
            "dailyPnl": (
                fmt_money(_d(r["daily_pnl"])) if r["daily_pnl"] is not None else None
            ),
            "cashBalance": fmt_money(_d(r["cash_balance"])),
        }
        for r in rows
    ]
    return {
        "points": points,
        "benchmark": None,
        "currency": "CNY",
        "timezone": str(SHANGHAI_TZ),
    }


# ---------------------------------------------------------------------------
# 期初组合初始化：标的搜索 / 两阶段导入 / 投影重算
# ---------------------------------------------------------------------------
# 对外资产类型（前端）→ 内部 asset_type
PUBLIC_ASSET_TYPE: dict[str, str] = {
    "STOCK": "CN_STOCK",
    "ETF": "EXCHANGE_FUND",
    "FUND": "OFF_EXCHANGE_FUND",
}
INTERNAL_ASSET_TYPE: dict[str, str] = {
    value: key for key, value in PUBLIC_ASSET_TYPE.items()
}

_SYMBOL_RE = re.compile(r"^(\d{6})(?:\.(?:SH|SZ|BJ))?$")
_SEARCH_DATASET_TTL_SECONDS = 300.0
_DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS = 30.0
_SEARCH_DATASET_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
_SEARCH_DATASET_LOCKS: dict[str, asyncio.Lock] = {}


def normalize_symbol(symbol: str) -> str:
    """Strip exchange suffixes and validate the 6-digit code."""
    match = _SYMBOL_RE.match(symbol.strip().upper())
    if not match:
        raise ValidationError_(
            "证券代码须为 6 位数字（如 600519 / 510300 / 110022）",
            field_errors=[{"field": "symbol", "message": "须为 6 位数字代码"}],
        )
    return match.group(1)


def _primary_market(asset_type: str, symbol: str) -> str:
    if asset_type == "OFF_EXCHANGE_FUND":
        return "FUND_CN"
    # 北交所现行代码以 92/8 开头，兼容历史 4 开头代码；创业板 30/31
    # 仍属于深交所，不能按首位 3 误判为北交所。
    if symbol.startswith(("4", "8", "92")):
        return "BSE"
    if symbol[0] in "569":
        return "SSE"
    return "SZSE"


async def _load_search_dataset(
    cache_key: str,
    fetcher,
    *,
    code_column: str = "代码",
    name_column: str = "名称",
    timeout_seconds: float = _DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS,
) -> list[dict[str, str]]:
    """Load one Provider master dataset with per-key TTL and single-flight.

    Search terms are applied after this cache boundary. Thus ten keystrokes do
    not download the same three whole-market tables ten times.
    """
    loop = asyncio.get_running_loop()
    cached = _SEARCH_DATASET_CACHE.get(cache_key)
    if cached is not None and cached[0] > loop.time():
        return cached[1]
    lock = _SEARCH_DATASET_LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = _SEARCH_DATASET_CACHE.get(cache_key)
        if cached is not None and cached[0] > loop.time():
            return cached[1]
        df = await asyncio.wait_for(asyncio.to_thread(fetcher), timeout=timeout_seconds)
        if (
            df is None
            or df.empty
            or not {code_column, name_column}.issubset(df.columns)
        ):
            return []
        rows = [
            {
                "symbol": str(row[code_column]).strip().zfill(6),
                "name": str(row[name_column]).strip(),
            }
            for _, row in df.iterrows()
            if str(row[code_column]).strip()
        ]
        _SEARCH_DATASET_CACHE[cache_key] = (
            loop.time() + _SEARCH_DATASET_TTL_SECONDS,
            rows,
        )
        return rows


async def _ak_search(
    keyword: str,
    *,
    timeout_seconds: float = _DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS,
) -> list[dict]:
    """Real-time instrument search via AkShare (A股/ETF/场外基金), fail-safe.

    Any vendor failure silently degrades to the local instrument master —
    the search must never block the add-position flow.
    """
    try:
        import akshare as ak  # noqa: PLC0415
    except ImportError:
        return []

    kw = keyword.strip()
    results: dict[str, dict] = {}

    def _market(code: str) -> str:
        return _primary_market("CN_STOCK", code)

    async def _run(
        cache_key: str,
        fetcher,
        asset_type: str,
        market_fn,
        *,
        code_column: str = "代码",
        name_column: str = "名称",
    ) -> None:
        try:
            rows = await _load_search_dataset(
                cache_key,
                fetcher,
                code_column=code_column,
                name_column=name_column,
                timeout_seconds=timeout_seconds,
            )
            matched = [
                row
                for row in rows
                if kw in row["symbol"] or kw.casefold() in row["name"].casefold()
            ][:10]
            for row in matched:
                code = row["symbol"]
                results.setdefault(
                    f"{market_fn(code)}:{code}:{asset_type}",
                    {
                        "symbol": code,
                        "name": row["name"],
                        "market": market_fn(code),
                        "type": asset_type,
                        "source": "live",
                    },
                )
        except Exception as exc:  # noqa: BLE001 — 搜索失败降级到其他权威源
            logger.warning(
                "instrument master dataset unavailable source=%s error=%s",
                cache_key,
                exc,
            )
            return

    await asyncio.gather(
        _run("stock_zh_a_spot_em", lambda: ak.stock_zh_a_spot_em(), "STOCK", _market),
        _run("fund_etf_spot_em", lambda: ak.fund_etf_spot_em(), "ETF", _market),
        _run(
            "fund_name_em",
            lambda: ak.fund_name_em(),
            "FUND",
            lambda _code: "FUND_CN",
            code_column="基金代码",
            name_column="基金简称",
        ),
    )
    return list(results.values())


async def instrument_live_candidates(
    symbol: str,
    *,
    timeout_seconds: float = _DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS,
) -> list[dict]:
    """Live canonical candidates for one exact code (fund/ETF/stock datasets).

    Returns ``[{symbol, name, market, type}]`` (type ∈ STOCK/ETF/FUND). Used by
    the watchlist add path to auto-register a symbol missing from the local
    master when the client did not carry an asset type; vendor failures degrade
    per dataset and never raise.  An ambiguous code (e.g. 000001 is both a stock
    and an OTC fund) yields more than one candidate for the caller to disambiguate.
    """
    target = str(symbol).strip().upper().zfill(6)
    out: dict[str, dict] = {}
    try:
        import akshare as ak  # noqa: PLC0415
    except ImportError:
        return []

    async def _collect(
        cache_key: str,
        fetcher,
        *,
        code_column: str,
        name_column: str,
        asset_type: str,
        market_fn,
    ) -> None:
        try:
            rows = await _load_search_dataset(
                cache_key,
                fetcher,
                code_column=code_column,
                name_column=name_column,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — 数据集缺失仅降级该类型
            logger.warning(
                "watchlist live candidate unavailable source=%s error=%s",
                cache_key,
                exc,
            )
            return
        for row in rows:
            if row["symbol"] != target:
                continue
            market = market_fn(row["symbol"])
            out.setdefault(
                f"{market}:{row['symbol']}:{asset_type}",
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "market": market,
                    "type": asset_type,
                    "source": "live",
                },
            )

    await asyncio.gather(
        _collect(
            "fund_name_em",
            lambda: ak.fund_name_em(),
            code_column="基金代码",
            name_column="基金简称",
            asset_type="FUND",
            market_fn=lambda _code: "FUND_CN",
        ),
        _collect(
            "fund_etf_spot_em",
            lambda: ak.fund_etf_spot_em(),
            code_column="代码",
            name_column="名称",
            asset_type="ETF",
            market_fn=lambda code: _primary_market("CN_STOCK", code),
        ),
        _collect(
            "stock_zh_a_spot_em",
            lambda: ak.stock_zh_a_spot_em(),
            code_column="代码",
            name_column="名称",
            asset_type="STOCK",
            market_fn=lambda code: _primary_market("CN_STOCK", code),
        ),
    )
    return sorted(out.values(), key=lambda item: (item["type"], item["symbol"]))


def _listed_symbol_matches_asset_type(symbol: str, asset_type: str) -> bool:
    """Reject obvious stock/ETF type mismatches before quote-only lookup."""
    if asset_type == "EXCHANGE_FUND":
        return symbol.startswith(("1", "5"))
    if asset_type != "CN_STOCK":
        return False
    return symbol.startswith(
        (
            "000",
            "001",
            "002",
            "003",
            "300",
            "301",
            "4",
            "8",
            "92",
            "600",
            "601",
            "603",
            "605",
            "688",
            "689",
        )
    )


async def _verify_authoritative_instrument(
    symbol: str,
    asset_type: str,
    *,
    timeout_seconds: float = _DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS,
) -> dict | None:
    """Verify one exact code without trusting a user-supplied display name."""
    public_type = INTERNAL_ASSET_TYPE[asset_type]
    market = _primary_market(asset_type, symbol)

    if asset_type in {"CN_STOCK", "EXCHANGE_FUND"}:
        # The Sina quote route used here has no reliable BSE addressing mode.
        if market == "BSE" or not _listed_symbol_matches_asset_type(symbol, asset_type):
            return None
        try:
            identity = await asyncio.wait_for(
                market_data_adapter.get_sina_instrument_identity(symbol, public_type),
                timeout=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — broad master may still work
            logger.warning(
                "targeted instrument verification unavailable source=sina symbol=%s error=%s",
                symbol,
                exc,
            )
            return None
        if identity is None or identity.get("symbol") != symbol:
            return None
        return {
            "symbol": symbol,
            "name": str(identity["name"]).strip(),
            "market": market,
            "type": public_type,
            "source": str(identity.get("source") or "sina/suggest"),
        }

    if asset_type != "OFF_EXCHANGE_FUND":
        return None
    try:
        import akshare as ak  # noqa: PLC0415

        def _load_fund_basic_info():
            return ak.fund_individual_basic_info_xq(
                symbol=symbol,
                timeout=timeout_seconds,
            )

        df = await asyncio.wait_for(
            asyncio.to_thread(_load_fund_basic_info),
            timeout=timeout_seconds,
        )
        if df is None or df.empty or len(df.columns) < 2:
            return None
        fields = {
            str(row.iloc[0]).strip(): str(row.iloc[1]).strip()
            for _, row in df.iterrows()
        }
    except Exception as exc:  # noqa: BLE001 — broad master may still work
        logger.warning(
            "targeted instrument verification unavailable source=danjuan symbol=%s error=%s",
            symbol,
            exc,
        )
        return None
    provider_symbol = fields.get("基金代码", "").zfill(6)
    provider_name = fields.get("基金名称", "")
    if provider_symbol != symbol or not provider_name:
        return None
    return {
        "symbol": symbol,
        "name": provider_name,
        "market": "FUND_CN",
        "type": public_type,
        "source": "danjuan/fund-basic",
    }


async def verify_fund_master_names(
    product_codes: list[str],
    *,
    timeout_seconds: float = _DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Return Provider-verified names for exact off-exchange fund codes.

    Missing entries represent unavailable or unknown Provider data. Callers
    can keep those source positions visible as blocking conflicts without
    trusting an uploaded display name or failing the whole preview request.
    """
    codes = set(product_codes)
    if not codes:
        return {}
    try:
        import akshare as ak  # noqa: PLC0415

        rows = await _load_search_dataset(
            "fund_name_em",
            lambda: ak.fund_name_em(),
            code_column="基金代码",
            name_column="基金简称",
            timeout_seconds=timeout_seconds,
        )
    except Exception:  # noqa: BLE001 - unresolved codes stay visible and blocking
        return {}
    return {
        row["symbol"]: row["name"]
        for row in rows
        if row["symbol"] in codes and row["name"]
    }


async def search_instruments(
    pool: asyncpg.Pool,
    ctx,
    keyword: str,
    *,
    provider_timeout_seconds: float = _DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS,
) -> dict:
    """Search stocks/ETFs/funds by Chinese name or 6-digit code.

    Local instrument master first, then AkShare realtime enrichment
    (degradation-safe); identity is market + symbol + asset type.
    """
    kw = keyword.strip()
    if not kw:
        raise ValidationError_(
            "搜索关键词不能为空",
            field_errors=[{"field": "keyword", "message": "不能为空"}],
        )
    merged: dict[str, dict] = {}
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        rows = await repository.search_instruments_local(conn, kw)
        for row in rows:
            if not row["ticker"]:
                continue
            identity = (
                f"{row['primary_market']}:{row['ticker']}:"
                f"{INTERNAL_ASSET_TYPE.get(row['asset_type'], 'STOCK')}"
            )
            merged[identity] = {
                "symbol": row["ticker"],
                "name": row["canonical_name"],
                "market": row["primary_market"],
                "type": INTERNAL_ASSET_TYPE.get(row["asset_type"], "STOCK"),
                "source": "local",
            }
    for item in await _ak_search(kw, timeout_seconds=provider_timeout_seconds):
        identity = f"{item['market']}:{item['symbol']}:{item['type']}"
        merged.setdefault(identity, item)
    items = sorted(merged.values(), key=lambda x: (x["type"], x["symbol"]))[:20]
    return {"items": items, "total": len(items)}


async def recompute_projections(
    conn: asyncpg.Connection, *, tenant_id: str, extra_quotes: dict | None = None
) -> None:
    """Recompute today's position + NAV projections for the tenant.

    quotes cover every held instrument (persisted observations + extra live
    quotes for the instrument being mutated), so no other position degrades
    to UNAVAILABLE during the replay.
    """
    held_ids = await repository.holding_instrument_ids(conn, tenant_id)
    quotes = await repository.latest_observation_quotes(conn, held_ids)
    if extra_quotes:
        quotes.update(extra_quotes)
    as_of_date = now_shanghai().date()
    await market_repository.refresh_positions(
        conn, tenant_id=tenant_id, quotes=quotes, as_of_date=as_of_date
    )
    await market_repository.refresh_account_nav(
        conn, tenant_id=tenant_id, as_of_date=as_of_date
    )


async def _resolve_verified_instrument(
    pool: asyncpg.Pool,
    ctx,
    symbol: str,
    asset_type: str,
    *,
    provider_timeout_seconds: float = _DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS,
) -> dict:
    """Resolve canonical instrument data without trusting form display fields."""
    expected_market = _primary_market(asset_type, symbol)
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        existing = await repository.find_instrument_by_ticker(
            conn,
            symbol,
            primary_market=expected_market,
            asset_type=None,
        )
    if existing is not None:
        if existing["asset_type"] != asset_type:
            raise ValidationError_(
                f"标的 {symbol} 的资产类型与主数据不一致",
                field_errors=[{"field": "assetType", "message": "与主数据不一致"}],
            )
        return {
            "id": str(existing["id"]),
            "symbol": symbol,
            "name": existing["canonical_name"],
            "asset_type": asset_type,
            "market": existing["primary_market"],
        }

    expected_public_type = INTERNAL_ASSET_TYPE[asset_type]
    exact = await _verify_authoritative_instrument(
        symbol,
        asset_type,
        timeout_seconds=provider_timeout_seconds,
    )
    if exact is None:
        candidates = await _ak_search(symbol, timeout_seconds=provider_timeout_seconds)
        exact = next(
            (
                candidate
                for candidate in candidates
                if candidate["symbol"] == symbol
                and candidate["type"] == expected_public_type
            ),
            None,
        )
    if exact is None:
        raise ValidationError_(
            "暂时无法从行情源验证该标的，请稍后重试或等待主数据同步",
            field_errors=[{"field": "symbol", "message": "未通过权威数据源验证"}],
        )
    return {
        "id": None,
        "symbol": symbol,
        "name": exact["name"],
        "asset_type": asset_type,
        "market": exact["market"],
    }


def _position_import_content_hash(
    *,
    symbol: str,
    asset_type: str,
    quantity: Decimal,
    unit_cost: Decimal,
    trade_date,
    account_id: str | None,
) -> str:
    """Hash every command field that may alter the resulting economic baseline."""
    canonical = json.dumps(
        {
            "symbol": symbol,
            "assetType": asset_type,
            "quantity": str(quantity),
            "unitCost": str(unit_cost),
            "tradeDate": trade_date.isoformat(),
            "accountId": account_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _position_import_replay_response(row) -> dict:
    """Reconstruct the original DRAFT creation response from persisted input."""
    public_type = INTERNAL_ASSET_TYPE.get(str(row["asset_type"]))
    if public_type is None:
        raise ValidationError_("历史初始化草稿包含不支持的资产类型")
    return {
        "batchId": str(row["batch_id"]),
        "status": "DRAFT",
        "version": 1,
        "item": {
            "itemId": str(row["item_id"]),
            "symbol": str(row["ticker"]),
            "name": str(row["canonical_name"]),
            "assetType": public_type,
            "quantity": fmt_qty(Decimal(str(row["quantity"]))),
            "unitCost": fmt_money(Decimal(str(row["unit_cost"]))),
            "tradeDate": row["trade_date"].isoformat(),
            "accountId": str(row["account_id"]),
            "validationStatus": str(row["validation_status"]),
        },
    }


def _assert_same_position_import(row, source_hash: str) -> None:
    if str(row["source_content_hash"]) != source_hash:
        raise IdempotencyConflictError(
            "Idempotency-Key 已用于不同的期初持仓内容，请刷新后重试"
        )


def _csv_import_content_hash(rows: list[CsvPositionRow], *, baseline_date: date) -> str:
    """Hash normalized economic fields; uploaded names are display-only input."""
    canonical = json.dumps(
        {
            "baselineDate": baseline_date.isoformat(),
            "positions": [
                {
                    "row": row.row_number,
                    "assetType": row.asset_type,
                    "symbol": row.symbol,
                    "quantity": format(row.quantity, "f"),
                    "unitCost": format(row.unit_cost, "f"),
                }
                for row in rows
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _csv_import_replay_response(row, *, replayed: bool) -> dict:
    if str(row.get("source_type", "")) != "CSV":
        raise IdempotencyConflictError(
            "Idempotency-Key 已用于其他类型的持仓导入，请刷新后重试"
        )
    if str(row["status"]) != "CONFIRMED":
        raise IdempotencyConflictError("此前 CSV 导入尚未完成，请刷新后重试")
    return {
        "batchId": str(row["batch_id"]),
        "status": "CONFIRMED",
        "version": int(row["version"]),
        "importedCount": int(row["position_count"]),
        "eventIds": [str(event_id) for event_id in row.get("event_ids", [])],
        "replayed": replayed,
    }


def _assert_same_csv_import(row, source_hash: str) -> None:
    if (
        str(row.get("source_type", "")) != "CSV"
        or str(row["source_content_hash"]) != source_hash
    ):
        raise IdempotencyConflictError(
            "Idempotency-Key 已用于不同的 CSV 导入内容，请刷新后重试"
        )


async def _resolve_csv_instruments(
    pool: asyncpg.Pool,
    ctx,
    rows: list[CsvPositionRow],
    *,
    provider_timeout_seconds: float,
    batch_timeout_seconds: float | None = None,
) -> list[tuple[CsvPositionRow, dict]]:
    """Deduplicate and resolve one batch under bounded concurrency/deadline."""
    keys = list(dict.fromkeys((row.asset_type, row.symbol) for row in rows))
    semaphore = asyncio.Semaphore(_CSV_IMPORT_VERIFY_CONCURRENCY)

    async def resolve(key: tuple[str, str]) -> dict:
        asset_type, symbol = key
        async with semaphore:
            return await _resolve_verified_instrument(
                pool,
                ctx,
                symbol,
                PUBLIC_ASSET_TYPE[asset_type],
                provider_timeout_seconds=provider_timeout_seconds,
            )

    deadline = batch_timeout_seconds
    if deadline is None:
        deadline = min(
            max(provider_timeout_seconds * 2, 10.0),
            _CSV_IMPORT_MAX_PROVIDER_SECONDS,
        )
    tasks = [asyncio.create_task(resolve(key)) for key in keys]
    try:
        try:
            resolved = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=deadline,
            )
        except TimeoutError as exc:
            raise DataUnavailableError(
                "行情主数据核验超时，请稍后使用同一批次重新提交"
            ) from exc
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    by_key = dict(zip(keys, resolved, strict=True))
    return [(row, by_key[(row.asset_type, row.symbol)]) for row in rows]


async def import_csv_positions(
    pool: asyncpg.Pool,
    ctx,
    csv_data: str,
    *,
    baseline_date: date | None,
    idempotency_key: str,
    provider_timeout_seconds: float = _DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS,
) -> dict:
    """Validate and atomically confirm one mixed-asset CSV opening baseline."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")
    rows = parse_csv_position_rows(csv_data)
    trade_date = baseline_date or now_shanghai().date()
    source_hash = _csv_import_content_hash(rows, baseline_date=trade_date)

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        previous = await repository.find_csv_import_by_idempotency_key(
            conn,
            tenant_id=ctx.tenant_id,
            idempotency_key=idempotency_key,
        )
    if previous is not None:
        _assert_same_csv_import(previous, source_hash)
        return _csv_import_replay_response(previous, replayed=True)

    verified_rows = await _resolve_csv_instruments(
        pool,
        ctx,
        rows,
        provider_timeout_seconds=provider_timeout_seconds,
    )

    try:
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            previous = await repository.find_csv_import_by_idempotency_key(
                conn,
                tenant_id=ctx.tenant_id,
                idempotency_key=idempotency_key,
            )
            if previous is not None:
                _assert_same_csv_import(previous, source_hash)
                return _csv_import_replay_response(previous, replayed=True)

            account_id = await repository.pick_or_create_manual_account(
                conn,
                tenant_id=ctx.tenant_id,
                owner_user_id=ctx.user_id,
            )
            items: list[dict] = []
            for row, verified in verified_rows:
                internal_type = PUBLIC_ASSET_TYPE[row.asset_type]
                current = await repository.find_instrument_by_ticker(
                    conn,
                    row.symbol,
                    primary_market=verified["market"],
                    asset_type=None,
                )
                if current is not None and current["asset_type"] != internal_type:
                    raise ValidationError_(
                        f"标的 {row.symbol} 的资产类型与主数据不一致",
                        field_errors=[
                            {
                                "field": f"rows[{row.row_number}].assetType",
                                "message": "与主数据不一致",
                            }
                        ],
                    )
                instrument_id = (
                    str(current["id"])
                    if current is not None
                    else await repository.upsert_instrument(
                        conn,
                        ticker=row.symbol,
                        name=verified["name"],
                        asset_type=internal_type,
                        primary_market=verified["market"],
                    )
                )
                items.append(
                    {
                        "row_number": row.row_number,
                        "instrument_id": instrument_id,
                        "quantity": row.quantity,
                        "unit_cost": row.unit_cost,
                        "trade_date": trade_date,
                    }
                )
            batch_id = await repository.create_csv_import_draft(
                conn,
                tenant_id=ctx.tenant_id,
                account_id=account_id,
                created_by=ctx.user_id,
                idempotency_key=idempotency_key,
                source_content_hash=source_hash,
                items=items,
            )
            confirmed = await repository.confirm_import_batch(
                conn,
                tenant_id=ctx.tenant_id,
                batch_id=batch_id,
                idempotency_key=f"{idempotency_key}:confirm",
            )
            if not confirmed or confirmed.get("status") in {"INVALID", "CANCELLED"}:
                raise ValidationError_("CSV 批次未通过确认校验，未写入任何持仓")
            await recompute_projections(conn, tenant_id=ctx.tenant_id)
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="PORTFOLIO_CSV_IMPORT_CONFIRMED",
                resource_type="ledger.portfolio_import_batches",
                resource_id=batch_id,
                details={
                    "positionCount": len(items),
                    "eventIds": confirmed.get("event_ids", []),
                },
            )
    except asyncpg.exceptions.UniqueViolationError:
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            previous = await repository.find_csv_import_by_idempotency_key(
                conn,
                tenant_id=ctx.tenant_id,
                idempotency_key=idempotency_key,
            )
        if previous is None:
            raise
        _assert_same_csv_import(previous, source_hash)
        return _csv_import_replay_response(previous, replayed=True)

    return {
        "batchId": batch_id,
        "status": "CONFIRMED",
        "version": int(confirmed["version"]),
        "importedCount": len(items),
        "eventIds": [str(event_id) for event_id in confirmed.get("event_ids", [])],
        "replayed": False,
    }


async def create_position_import(
    pool: asyncpg.Pool,
    ctx,
    payload,
    *,
    idempotency_key: str,
    provider_timeout_seconds: float = _DEFAULT_MARKET_MASTER_TIMEOUT_SECONDS,
) -> dict:
    """Create a reviewable position-import draft; do not publish ledger facts."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")
    symbol = normalize_symbol(payload.symbol)
    internal_type = PUBLIC_ASSET_TYPE.get(payload.assetType)
    if internal_type is None:
        raise ValidationError_("仅支持 STOCK / ETF / FUND")
    quantity = Decimal(str(payload.quantity))
    unit_cost = Decimal(str(payload.avgCost))
    if quantity <= 0 or unit_cost < 0:
        raise ValidationError_("数量必须大于 0，成本价不能为负")
    trade_date = payload.buyDate or now_shanghai().date()
    source_hash = _position_import_content_hash(
        symbol=symbol,
        asset_type=payload.assetType,
        quantity=quantity,
        unit_cost=unit_cost,
        trade_date=trade_date,
        account_id=payload.accountId,
    )

    # Check before any Provider call or write: a transport retry should work
    # even when the external market-data source has subsequently degraded.
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        previous = await repository.find_position_import_by_idempotency_key(
            conn,
            tenant_id=ctx.tenant_id,
            idempotency_key=idempotency_key,
        )
    if previous is not None:
        _assert_same_position_import(previous, source_hash)
        return _position_import_replay_response(previous)

    verified = await _resolve_verified_instrument(
        pool,
        ctx,
        symbol,
        internal_type,
        provider_timeout_seconds=provider_timeout_seconds,
    )

    try:
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            current = await repository.find_instrument_by_ticker(
                conn,
                symbol,
                primary_market=verified["market"],
                asset_type=None,
            )
            if current is not None and current["asset_type"] != internal_type:
                raise ValidationError_(
                    f"标的 {symbol} 的资产类型与主数据不一致",
                    field_errors=[{"field": "assetType", "message": "与主数据不一致"}],
                )
            instrument_id = (
                str(current["id"])
                if current is not None
                else await repository.upsert_instrument(
                    conn,
                    ticker=symbol,
                    name=verified["name"],
                    asset_type=internal_type,
                    primary_market=verified["market"],
                )
            )
            if payload.accountId:
                account_id = await repository.find_writable_account(
                    conn,
                    tenant_id=ctx.tenant_id,
                    account_id=payload.accountId,
                    user_id=ctx.user_id,
                )
                if account_id is None:
                    raise ValidationError_("账户不存在、不可用或当前用户没有写入权限")
            else:
                account_id = await repository.pick_or_create_account(
                    conn,
                    tenant_id=ctx.tenant_id,
                    asset_type=internal_type,
                    owner_user_id=ctx.user_id,
                )
            draft = await repository.create_import_draft(
                conn,
                tenant_id=ctx.tenant_id,
                account_id=account_id,
                created_by=ctx.user_id,
                instrument_id=instrument_id,
                quantity=quantity,
                unit_cost=unit_cost,
                trade_date=trade_date,
                idempotency_key=idempotency_key,
                source_content_hash=source_hash,
            )
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="PORTFOLIO_IMPORT_DRAFT_CREATED",
                resource_type="ledger.portfolio_import_batches",
                resource_id=draft["batch_id"],
                details={"itemId": draft["item_id"], "symbol": symbol},
            )
    except asyncpg.exceptions.UniqueViolationError:
        # A concurrent retry may win the unique key after our early read.
        # Re-read outside the aborted transaction and replay only if the
        # content hash proves that this is the same command.
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            previous = await repository.find_position_import_by_idempotency_key(
                conn,
                tenant_id=ctx.tenant_id,
                idempotency_key=idempotency_key,
            )
        if previous is None:
            raise
        _assert_same_position_import(previous, source_hash)
        return _position_import_replay_response(previous)

    return {
        "batchId": draft["batch_id"],
        "status": "DRAFT",
        "version": 1,
        "item": {
            "itemId": draft["item_id"],
            "symbol": symbol,
            "name": verified["name"],
            "assetType": payload.assetType,
            "quantity": fmt_qty(quantity),
            "unitCost": fmt_money(unit_cost),
            "tradeDate": trade_date.isoformat(),
            "accountId": account_id,
            "validationStatus": "VALID",
        },
    }


async def confirm_position_import(
    pool: asyncpg.Pool,
    ctx,
    batch_id: str,
    *,
    idempotency_key: str,
) -> dict:
    """Confirm a reviewed draft and rebuild projections in one transaction."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        confirmed = await repository.confirm_import_batch(
            conn,
            tenant_id=ctx.tenant_id,
            batch_id=batch_id,
            idempotency_key=idempotency_key,
        )
        if not confirmed:
            raise NotFoundError("初始化草稿不存在")
        if confirmed.get("status") == "INVALID":
            raise ValidationError_("草稿包含未通过校验的项目，不能确认")
        if confirmed.get("status") not in (None, "CONFIRMED"):
            raise ValidationError_("初始化草稿当前状态不允许确认")
        if not confirmed.get("already_confirmed"):
            await recompute_projections(conn, tenant_id=ctx.tenant_id)
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="PORTFOLIO_IMPORT_CONFIRMED",
                resource_type="ledger.portfolio_import_batches",
                resource_id=batch_id,
                details={"eventIds": confirmed.get("event_ids", [])},
            )
    return {
        "batchId": batch_id,
        "status": "CONFIRMED",
        "version": confirmed["version"],
        "eventId": confirmed.get("event_id"),
    }
