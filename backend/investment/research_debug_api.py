"""Explicit development-only research queue, with no collector or model authority."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from backend.common.database import tenant_transaction
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ForbiddenError, ok
from backend.macro import research_debug_repository as repository

router = APIRouter(prefix="/investment/daily-research/refresh", tags=["investment"])


class RefreshCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandKey: UUID


def debug_enabled(settings):
    return (
        settings.app_env in {"development", "test"}
        and settings.daily_research_debug_enabled
        and settings.daily_research_enabled
    )


async def _enabled(conn, ctx, settings):
    return (
        debug_enabled(settings)
        and ctx.is_owner_or_admin
        and await repository.active_operator(
            conn, UUID(ctx.tenant_id), UUID(ctx.user_id)
        )
    )


@router.get("")
async def capability(
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    settings=Depends(get_settings_dep),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    # No capability means no task lookup, and no routine forbidden browser response.
    if not debug_enabled(settings) or not ctx.is_owner_or_admin:
        return ok({"enabled": False, "request": None}, request_id=request_id, as_of=now)
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        enabled = await _enabled(conn, ctx, settings)
        value = (
            await repository.latest_refresh(
                conn, UUID(ctx.tenant_id), UUID(ctx.user_id), now
            )
            if enabled
            else None
        )
    return ok(
        {"enabled": bool(enabled), "request": value}, request_id=request_id, as_of=now
    )


@router.post("", status_code=202)
async def enqueue(
    body: RefreshCommand,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    settings=Depends(get_settings_dep),
    request_id=Depends(get_request_id),
):
    if not debug_enabled(settings) or not ctx.is_owner_or_admin:
        raise ForbiddenError("当前环境或角色未启用调试研究")
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        if not await _enabled(conn, ctx, settings):
            raise ForbiddenError("当前成员已无调试研究权限")
        value = await repository.enqueue_refresh(
            conn, UUID(ctx.tenant_id), UUID(ctx.user_id), body.commandKey, now
        )
    return ok(value, request_id=request_id, as_of=now)


@router.get("/{run_id}")
async def status(
    run_id: UUID,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    settings=Depends(get_settings_dep),
    request_id=Depends(get_request_id),
):
    if not debug_enabled(settings) or not ctx.is_owner_or_admin:
        raise ForbiddenError("当前环境或角色未启用调试研究")
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        if not await _enabled(conn, ctx, settings):
            raise ForbiddenError("当前成员已无调试研究权限")
        value = await repository.read_refresh(
            conn, UUID(ctx.tenant_id), UUID(ctx.user_id), run_id, now
        )
    return ok(value, request_id=request_id, as_of=now)
