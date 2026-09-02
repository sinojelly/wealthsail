"""Reports API: /api/v1/reports/list · {id} · generate."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
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
from backend.errors import ValidationError_, ok
from backend.reports import repository, service

router = APIRouter(prefix="/reports", tags=["reports"])


class GenerateRequest(BaseModel):
    session: str = Field(description="PRE_MARKET | INTRADAY | FUND_1430 | POST_MARKET")


@router.get("/list")
async def reports_list(
    trade_date: date | None = Query(
        default=None,
        alias="tradeDate",
        description="交易日期，缺省取最新",
    ),
    session: str | None = Query(default=None),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """四时段报告列表（每个时段返回最新版本）。"""
    if session is not None and session not in repository.REPORT_SESSIONS:
        raise ValidationError_(
            "未知报告时段", field_errors=[{"field": "session", "message": session}]
        )
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        if trade_date is None:
            latest_date = await conn.fetchval(
                """
                SELECT MAX(trade_date) FROM reporting.reports WHERE tenant_id = $1
                """,
                ctx.tenant_id,
            )
            trade_date = latest_date
        data = await service.list_reports(
            conn,
            ctx.tenant_id,
            trade_date,
            session,
            actor_user_id=ctx.user_id,
        )
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/{report_id}")
async def report_detail(
    report_id: str,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """报告详情：摘要、条目、模型运行与版本审计链。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_report_detail(
            conn,
            ctx.tenant_id,
            report_id,
            actor_user_id=ctx.user_id,
        )
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/generate")
async def generate(
    body: GenerateRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """生成指定时段研报（LLM 网关可用时调用 DeepSeek，否则确定性降级）。"""
    as_of = now_shanghai()
    data = await service.generate_report(pool, ctx, settings, body.session)
    return ok(data, request_id=request_id, as_of=as_of)
