"""Inspect or replay frozen validation; never recollect or invoke a model."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from backend.common.database import tenant_transaction
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ForbiddenError, NotFoundError, ok
from backend.macro import research_acceptance_repository as repository
from backend.macro.research_debug_repository import active_operator

router = APIRouter(prefix="/investment/daily-research/validation", tags=["investment"])


class ReplayCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandKey: UUID


async def _admin(conn, ctx):
    return ctx.is_owner_or_admin and await active_operator(conn, UUID(ctx.tenant_id), UUID(ctx.user_id))


@router.get("/{run_id}")
async def read_initial(run_id: UUID, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        value = await repository.read_validation_summary(conn, run_id, now, admin=bool(await _admin(conn, ctx)))
    if value is None:
        raise NotFoundError("该研究尚无结构化校验记录")
    return ok(value, request_id=request_id, as_of=now)


@router.get("/{run_id}/artifacts")
async def read_artifacts(run_id: UUID, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        if not await _admin(conn, ctx):
            raise ForbiddenError("仅当前有效管理员可查看冻结材料")
        value = await repository.read_artifacts_admin(conn, run_id, UUID(ctx.user_id), UUID(ctx.tenant_id))
    return ok(value, request_id=request_id, as_of=now)


@router.post("/{run_id}/replays")
async def replay(run_id: UUID, body: ReplayCommand, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        if not await _admin(conn, ctx):
            raise ForbiddenError("仅当前有效管理员可执行离线重放")
        value = await repository.replay_validation(conn, run_id, UUID(ctx.user_id), UUID(ctx.tenant_id), body.commandKey, now)
    if isinstance(value, repository.ReplayFailure):
        raise value.error
    return ok(value, request_id=request_id, as_of=now)


@router.get("/{run_id}/replays")
async def read_replays(run_id: UUID, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        if not await _admin(conn, ctx):
            raise ForbiddenError("仅当前有效管理员可查看离线重放记录")
        value = await repository.list_validation_replays(conn, run_id, now)
    return ok({"items": value}, request_id=request_id, as_of=now)
