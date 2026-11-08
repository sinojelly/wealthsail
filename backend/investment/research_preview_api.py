"""OWNER/ADMIN historical research simulation, without durable side effects."""

from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import APIRouter, Depends

from backend.common.database import apply_tenant_context
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ForbiddenError, ok
from backend.macro import research_preview as service
from backend.macro.research_debug_repository import active_operator

router = APIRouter(prefix="/operations/research-preview", tags=["operations"])


@asynccontextmanager
async def readonly_tenant_transaction(pool, ctx):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await apply_tenant_context(conn, ctx.user_id, ctx.tenant_id)
            yield conn


@router.get("/{run_id}")
async def preview(
    run_id: UUID,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    if not ctx.is_owner_or_admin:
        raise ForbiddenError("仅当前有效管理员可查看历史模拟预览")
    now = now_shanghai()
    async with readonly_tenant_transaction(pool, ctx) as conn:
        if not await active_operator(conn, UUID(ctx.tenant_id), UUID(ctx.user_id)):
            raise ForbiddenError("当前成员已无历史预览权限")
        value = await service.preview_research(conn, run_id, now=now)
    return ok(value, request_id=request_id, as_of=now)
