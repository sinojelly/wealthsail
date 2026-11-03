"""Explicit tenant-local historical repairs; no current-report publication."""

from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from backend.common.database import apply_tenant_context, tenant_transaction
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ForbiddenError, NotFoundError, ok
from backend.macro import research_history_repair_repository as repository
from backend.macro.research_debug_repository import active_operator

router = APIRouter(prefix="/investment/daily-research/history-repairs", tags=["investment"])


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandKey: UUID


class ReviewCommand(Command):
    decision: Literal["APPROVE", "REJECT"]


@asynccontextmanager
async def readonly_tenant_transaction(pool, ctx):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await apply_tenant_context(conn, ctx.user_id, ctx.tenant_id)
            yield conn


async def _admin(conn, ctx):
    return ctx.is_owner_or_admin and await active_operator(conn, UUID(ctx.tenant_id), UUID(ctx.user_id))


async def _require_admin(conn, ctx):
    if not await _admin(conn, ctx):
        raise ForbiddenError("仅当前有效管理员可预览、生成、审核或发布历史修复")


@router.get("/from/{run_id}/preview")
async def preview(run_id: UUID, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with readonly_tenant_transaction(pool, ctx) as conn:
        await _require_admin(conn, ctx)
        value = await repository.preview_history_repair(conn, run_id, UUID(ctx.user_id), UUID(ctx.tenant_id), now)
    return ok(value, request_id=request_id, as_of=now)


@router.post("/from/{run_id}")
async def generate(run_id: UUID, body: Command, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        await _require_admin(conn, ctx)
        value = await repository.generate_history_repair(conn, run_id, UUID(ctx.user_id), UUID(ctx.tenant_id), body.commandKey, now)
    if isinstance(value, repository.ReplayFailure):
        raise value.error
    return ok(value, request_id=request_id, as_of=now)


@router.get("")
async def listing(sourceRunId: UUID | None = None, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        admin = bool(await _admin(conn, ctx))
        items = await repository.list_history_repairs(conn, sourceRunId, now, admin=admin)
    return ok({"items": items, "canGenerate": admin}, request_id=request_id, as_of=now)


@router.get("/{identity}")
async def detail(identity: UUID, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        value = await repository.read_history_repair(conn, identity, now, admin=bool(await _admin(conn, ctx)))
    if value is None:
        raise NotFoundError("历史修复不存在或尚未发布")
    return ok(value, request_id=request_id, as_of=now)


@router.get("/{identity}/details")
async def details(identity: UUID, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        await _require_admin(conn, ctx)
        value = await repository.read_history_repair_details(conn, identity, now)
    if value is None:
        raise NotFoundError("历史修复不存在")
    return ok(value, request_id=request_id, as_of=now)


@router.post("/{identity}/reviews")
async def review(identity: UUID, body: ReviewCommand, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        await _require_admin(conn, ctx)
        value = await repository.review_history_repair(conn, identity, UUID(ctx.user_id), UUID(ctx.tenant_id), body.commandKey, body.decision, now)
    return ok(value, request_id=request_id, as_of=now)


@router.post("/{identity}/publish")
async def publish(identity: UUID, body: Command, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        await _require_admin(conn, ctx)
        value = await repository.publish_history_repair(conn, identity, UUID(ctx.user_id), UUID(ctx.tenant_id), body.commandKey, now)
    return ok(value, request_id=request_id, as_of=now)
