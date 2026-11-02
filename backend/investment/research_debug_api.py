"""Explicit administrator research queue, with no collector or model authority."""

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
from backend.macro.research_modes import (
    preferred_research_request_mode,
    research_request_mode_allowed,
)

router = APIRouter(prefix="/investment/daily-research/refresh", tags=["investment"])


class RefreshCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandKey: UUID


def debug_enabled(settings):
    return research_request_mode_allowed(settings, "DEBUG")


def _allowed_modes(settings):
    return [
        mode
        for mode in ("MANUAL", "DEBUG")
        if research_request_mode_allowed(settings, mode)
    ]


def _request_visible(settings, value):
    return value is not None and research_request_mode_allowed(
        settings, value.get("requestMode", "DEBUG")
    )


async def _enabled(conn, ctx, settings):
    return (
        preferred_research_request_mode(settings) is not None
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
    if preferred_research_request_mode(settings) is None or not ctx.is_owner_or_admin:
        return ok({"enabled": False, "request": None}, request_id=request_id, as_of=now)
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        enabled = await _enabled(conn, ctx, settings)
        value = (
            await repository.latest_refresh(
                conn,
                UUID(ctx.tenant_id),
                UUID(ctx.user_id),
                now,
                allowed_modes=_allowed_modes(settings),
            )
            if enabled
            else None
        )
        if not _request_visible(settings, value):
            value = None
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
    mode = preferred_research_request_mode(settings)
    if mode is None or not ctx.is_owner_or_admin:
        raise ForbiddenError("当前环境或角色未启用手动研究")
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        if not await _enabled(conn, ctx, settings):
            raise ForbiddenError("当前成员已无手动研究权限")
        value = await repository.enqueue_refresh(
            conn,
            UUID(ctx.tenant_id),
            UUID(ctx.user_id),
            body.commandKey,
            now,
            **({"request_mode": "MANUAL"} if mode == "MANUAL" else {}),
        )
        if not _request_visible(settings, value):
            raise ForbiddenError("当前未启用此已保存研究请求的模式")
    return ok(value, request_id=request_id, as_of=now)


@router.get("/{run_id}")
async def status(
    run_id: UUID,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    settings=Depends(get_settings_dep),
    request_id=Depends(get_request_id),
):
    if preferred_research_request_mode(settings) is None or not ctx.is_owner_or_admin:
        raise ForbiddenError("当前环境或角色未启用手动研究")
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        if not await _enabled(conn, ctx, settings):
            raise ForbiddenError("当前成员已无手动研究权限")
        value = await repository.read_refresh(
            conn, UUID(ctx.tenant_id), UUID(ctx.user_id), run_id, now
        )
        if not _request_visible(settings, value):
            raise ForbiddenError("当前未启用此已保存研究请求的模式")
    return ok(value, request_id=request_id, as_of=now)
