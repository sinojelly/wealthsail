"""Portfolio API: /api/v1/portfolio/summary · positions · nav-trend · search · add."""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Depends, File, Form, Header, Query, UploadFile
from pydantic import BaseModel, Field

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import DataUnavailableError, ValidationError_, ok
from backend.portfolio import service
from backend.portfolio.scope import resolve_asset_scope
from backend.portfolio.imports import service as tiantian_import_service
from backend.portfolio.imports.package import ImportLimits

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class PerformanceBaselineUpdateRequest(BaseModel):
    """Optional cumulative PnL known before WealthSail began tracking."""

    initialCumulativePnl: Decimal = Field(
        ...,
        max_digits=28,
        decimal_places=8,
        description="跟踪基线日之前已实现和未实现的历史累计收益，可为负数",
    )


@router.get("/summary")
async def portfolio_summary(
    scope: Literal["MINE", "TENANT", "MEMBER"] = "MINE",
    memberId: str | None = None,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """资产看板总览：总资产、现金、持仓市值、当日/累计收益、配置与质量状态。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        resolved_scope = await resolve_asset_scope(
            conn,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            kind=scope,
            member_id=memberId,
        )
        data = await service.get_summary(
            conn, ctx.tenant_id, as_of, scope=resolved_scope
        )
    return ok(
        data,
        request_id=request_id,
        as_of=as_of,
        quality_status=data["qualityStatus"],
        metadata={
            "scope": resolved_scope.kind,
            "memberId": resolved_scope.owner_user_id
            if resolved_scope.kind == "MEMBER"
            else None,
            "tenantId": ctx.tenant_id,
        },
    )


@router.get("/performance-baselines")
async def portfolio_performance_baselines(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Return the actor's account baselines; other members' values stay private."""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.list_performance_baselines(
            conn,
            tenant_id=ctx.tenant_id,
            owner_user_id=ctx.user_id,
        )
    return ok(data, request_id=request_id, as_of=as_of)


@router.put("/performance-baselines/{account_id}")
async def portfolio_revise_performance_baseline(
    account_id: str,
    payload: PerformanceBaselineUpdateRequest,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Append a reviewed pre-tracking cumulative PnL value for one account."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.revise_performance_baseline(
            conn,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            account_id=account_id,
            initial_cumulative_pnl=payload.initialCumulativePnl,
            expected_version=_parse_if_match(if_match),
            idempotency_key=idempotency_key,
        )
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/positions")
async def portfolio_positions(
    scope: Literal["MINE", "TENANT", "MEMBER"] = "MINE",
    memberId: str | None = None,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """当前持仓列表（含质量状态），按市值降序。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        resolved_scope = await resolve_asset_scope(
            conn,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            kind=scope,
            member_id=memberId,
        )
        data = await service.get_positions(
            conn, ctx.tenant_id, scope=resolved_scope
        )
    return ok(
        data,
        request_id=request_id,
        as_of=as_of,
        metadata={
            "scope": resolved_scope.kind,
            "memberId": resolved_scope.owner_user_id
            if resolved_scope.kind == "MEMBER"
            else None,
            "tenantId": ctx.tenant_id,
        },
    )


@router.get("/nav-trend")
async def portfolio_nav_trend(
    days: int = Query(default=60, ge=1, le=365, description="返回最近 N 个交易日"),
    scope: Literal["MINE", "TENANT", "MEMBER"] = "MINE",
    memberId: str | None = None,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """资产净值走势（日频）。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        resolved_scope = await resolve_asset_scope(
            conn,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            kind=scope,
            member_id=memberId,
        )
        data = await service.get_nav_trend(
            conn, ctx.tenant_id, days=days, scope=resolved_scope
        )
    return ok(
        data,
        request_id=request_id,
        as_of=as_of,
        quality_status="MISSING" if not data["points"] else "FRESH",
        metadata={
            "scope": resolved_scope.kind,
            "memberId": resolved_scope.owner_user_id
            if resolved_scope.kind == "MEMBER"
            else None,
            "tenantId": ctx.tenant_id,
        },
    )


@router.get("/search")
async def portfolio_search(
    keyword: str = Query(..., min_length=2, max_length=32, description="股票/ETF/基金名称或代码"),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """搜索可添加的标的：本地主数据 + AkShare 实时（降级安全）。

    返回: { items: [{symbol, name, market, type: STOCK|ETF|FUND}], total }
    """
    as_of = now_shanghai()
    data = await service.search_instruments(
        pool,
        ctx,
        keyword,
        provider_timeout_seconds=settings.market_master_timeout_seconds,
    )
    return ok(data, request_id=request_id, as_of=as_of)


class PositionCreateRequest(BaseModel):
    """用户创建期初持仓导入草稿的请求体。"""

    symbol: str = Field(..., description="6 位证券代码（如 600519 / 510300 / 110022）")
    name: str | None = Field(None, description="标的名称（搜索带入，可选）")
    assetType: Literal["STOCK", "ETF", "FUND"] = Field(..., description="资产类型")
    quantity: Decimal = Field(..., gt=0, description="数量/份额")
    avgCost: Decimal = Field(..., ge=0, description="成本价（每股/每份）")
    buyDate: date | None = Field(None, description="基线日期，默认今天")
    accountId: str | None = Field(None, description="账户 id（可选，默认按类型自动选择/创建）")


class CsvPositionImportRequest(BaseModel):
    """One bounded five-column CSV opening-position command."""

    csvData: str = Field(..., min_length=1, max_length=100_000)
    baselineDate: date | None = None


class TiantianHoldingStartDecision(BaseModel):
    """A user-reviewed holding episode start for one preview item."""

    itemId: str = Field(..., min_length=1, max_length=128)
    startedOn: date


class TiantianConfirmRequest(BaseModel):
    decisions: list[TiantianHoldingStartDecision] = Field(default_factory=list)
    confirmEmpty: bool = False


class TiantianHoldingStartConfirmRequest(BaseModel):
    startedOn: date


class TiantianHoldingStartEstimateItem(BaseModel):
    accountId: str = Field(min_length=1, max_length=64)
    instrumentId: str = Field(min_length=1, max_length=64)
    startedOn: date


class TiantianHoldingStartEstimateBatchRequest(BaseModel):
    items: list[TiantianHoldingStartEstimateItem] = Field(
        min_length=1, max_length=100
    )


def _require_tiantian_mutations(settings: Settings) -> None:
    if not settings.tiantian_import_enabled:
        raise DataUnavailableError("天天基金导入功能当前未启用", retryable=False)


def _import_limits(settings: Settings) -> ImportLimits:
    return ImportLimits(
        max_compressed_bytes=settings.tiantian_import_max_upload_bytes,
        max_expanded_bytes=settings.tiantian_import_max_expanded_bytes,
        max_entries=settings.tiantian_import_max_entries,
        max_compression_ratio=settings.tiantian_import_max_compression_ratio,
        max_positions=settings.tiantian_import_max_positions,
        max_transactions=settings.tiantian_import_max_transactions,
        max_json_depth=settings.tiantian_import_max_json_depth,
        max_text_length=settings.tiantian_import_max_text_length,
    )


def _parse_if_match(value: str | None) -> int:
    if value is None:
        raise ValidationError_("If-Match 版本请求头必填")
    normalized = value.strip()
    if normalized.startswith("W/"):
        raise ValidationError_("If-Match 不接受弱版本标识")
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1]
    try:
        version = int(normalized)
    except ValueError as exc:
        raise ValidationError_("If-Match 必须是正整数版本") from exc
    if version <= 0:
        raise ValidationError_("If-Match 必须是正整数版本")
    return version


async def _persist_bounded_upload(upload: UploadFile, max_bytes: int) -> Path:
    filename = (upload.filename or "").strip()
    if Path(filename).suffix.lower() != ".zip":
        await upload.close()
        raise ValidationError_(
            "请选择 .zip 导入包",
            field_errors=[{"field": "file", "message": "文件扩展名必须是 .zip"}],
        )

    descriptor, raw_path = tempfile.mkstemp(
        prefix="wealthsail-tiantian-", suffix=".zip"
    )
    os.close(descriptor)
    path = Path(raw_path)
    total = 0
    try:
        with path.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValidationError_(
                        "导入包超过允许大小",
                        field_errors=[
                            {
                                "field": "file",
                                "message": f"文件不能超过 {max_bytes} 字节",
                            }
                        ],
                    )
                target.write(chunk)
        if total == 0:
            raise ValidationError_(
                "导入包不能为空",
                field_errors=[{"field": "file", "message": "文件不能为空"}],
            )
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@router.post("/imports/tiantian/preview", status_code=201)
async def portfolio_preview_tiantian_import(
    file: UploadFile = File(...),
    account_id: str | None = Form(None, alias="accountId"),
    account_name: str | None = Form(None, alias="accountName"),
    duplicate_policy: Literal["AUTO", "RESTORE", "REPARSE"] = Form(
        "AUTO", alias="duplicatePolicy"
    ),
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Upload one bounded ZIP and persist a recoverable review-only DRAFT."""
    _require_tiantian_mutations(settings)
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    upload_path = await _persist_bounded_upload(
        file, settings.tiantian_import_max_upload_bytes
    )
    try:
        data = await tiantian_import_service.preview_tiantian_import(
            pool,
            ctx,
            upload_path,
            account_id,
            account_name,
            idempotency_key,
            limits=_import_limits(settings),
            provider_timeout_seconds=settings.market_master_timeout_seconds,
            duplicate_policy=duplicate_policy,
        )
    finally:
        upload_path.unlink(missing_ok=True)
    return ok(
        data,
        request_id=request_id,
        as_of=now_shanghai(),
        quality_status="FRESH",
    )


@router.post("/imports/csv", status_code=201)
async def portfolio_import_csv(
    payload: CsvPositionImportRequest,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Validate and atomically publish one CSV opening-position batch."""
    data = await service.import_csv_positions(
        pool,
        ctx,
        payload.csvData,
        baseline_date=payload.baselineDate,
        idempotency_key=idempotency_key,
        provider_timeout_seconds=settings.market_master_timeout_seconds,
    )
    return ok(
        data,
        request_id=request_id,
        as_of=now_shanghai(),
        quality_status="FRESH",
    )


@router.get("/imports")
async def portfolio_list_tiantian_imports(
    source_type: Literal["TIANTIAN_FUND_ZIP"] = Query(
        default="TIANTIAN_FUND_ZIP", alias="sourceType"
    ),
    status: Literal["DRAFT"] | None = Query(default=None),
    cursor: datetime | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    data = await tiantian_import_service.list_tiantian_imports(
        pool,
        ctx,
        source_type=source_type,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.get("/imports/{batch_id}")
async def portfolio_get_tiantian_import(
    batch_id: str,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    data = await tiantian_import_service.get_tiantian_import(pool, ctx, batch_id)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/imports", status_code=201)
async def portfolio_create_import(
    payload: PositionCreateRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """创建一个可预览的期初持仓草稿，不写入任何已确认经济事实。

    标的名称和市场由服务端主数据/Provider 重新核验，不采信表单展示文本。
    返回 DRAFT 后，客户端必须要求用户明确确认，才能生成期初 ADJUSTMENT。
    """
    as_of = now_shanghai()
    data = await service.create_position_import(
        pool,
        ctx,
        payload,
        idempotency_key=idempotency_key,
        provider_timeout_seconds=settings.market_master_timeout_seconds,
    )
    return ok(data, request_id=request_id, as_of=as_of, quality_status="FRESH")


@router.post("/imports/{batch_id}/confirm")
async def portfolio_confirm_import(
    batch_id: str,
    payload: TiantianConfirmRequest | None = Body(default=None),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    if_match: str | None = Header(default=None, alias="If-Match"),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """确认期初组合基线并生成带批次来源的 ADJUSTMENT；不会伪造交易或现金流。"""
    as_of = now_shanghai()
    if payload is not None or if_match is not None:
        _require_tiantian_mutations(settings)
        payload = payload or TiantianConfirmRequest()
        decisions: dict[str, date] = {}
        for decision in payload.decisions:
            if decision.itemId in decisions:
                raise ValidationError_("同一持仓项目不能提交多个开始日期")
            decisions[decision.itemId] = decision.startedOn
        data = await tiantian_import_service.confirm_tiantian_import(
            pool,
            ctx,
            batch_id,
            expected_version=_parse_if_match(if_match),
            decisions=decisions,
            confirm_empty=payload.confirmEmpty,
            idempotency_key=idempotency_key,
        )
    else:
        data = await service.confirm_position_import(
            pool,
            ctx,
            batch_id,
            idempotency_key=idempotency_key,
        )
    return ok(data, request_id=request_id, as_of=as_of, quality_status="FRESH")


@router.post("/imports/{batch_id}/cancel")
async def portfolio_cancel_tiantian_import(
    batch_id: str,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    _require_tiantian_mutations(settings)
    data = await tiantian_import_service.cancel_tiantian_import(
        pool,
        ctx,
        batch_id,
        expected_version=_parse_if_match(if_match),
        idempotency_key=idempotency_key,
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/imports/{batch_id}/metrics/retry", status_code=202)
async def portfolio_retry_tiantian_metrics(
    batch_id: str,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    _require_tiantian_mutations(settings)
    data = await tiantian_import_service.retry_tiantian_metrics(
        pool,
        ctx,
        batch_id,
        idempotency_key=idempotency_key,
    )
    return ok(
        data,
        request_id=request_id,
        as_of=now_shanghai(),
        quality_status="PENDING",
    )


@router.post(
    "/positions/{account_id}/{instrument_id}/holding-start/confirm",
    status_code=202,
)
async def portfolio_confirm_tiantian_holding_start(
    account_id: str,
    instrument_id: str,
    payload: TiantianHoldingStartConfirmRequest,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    _require_tiantian_mutations(settings)
    data = await tiantian_import_service.confirm_tiantian_holding_start(
        pool,
        ctx,
        account_id,
        instrument_id,
        started_on=payload.startedOn,
        idempotency_key=idempotency_key,
    )
    return ok(
        data,
        request_id=request_id,
        as_of=now_shanghai(),
        quality_status=(
            "PENDING" if data["recalculationRequired"] else "FRESH"
        ),
    )


@router.post("/positions/holding-start/confirm-estimates")
async def portfolio_confirm_tiantian_holding_start_estimates(
    payload: TiantianHoldingStartEstimateBatchRequest,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    _require_tiantian_mutations(settings)
    data = (
        await tiantian_import_service.confirm_tiantian_holding_start_estimates(
            pool,
            ctx,
            items=[
                {
                    "account_id": item.accountId,
                    "instrument_id": item.instrumentId,
                    "started_on": item.startedOn,
                }
                for item in payload.items
            ],
            idempotency_key=idempotency_key,
        )
    )
    return ok(
        data,
        request_id=request_id,
        as_of=now_shanghai(),
        quality_status="FRESH",
    )
