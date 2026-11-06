"""Paginated frozen research observations; reading never starts a model."""

from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from backend.common.database import apply_tenant_context
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.macro import research_topic_repository as repository

router = APIRouter(prefix="/investment/daily-research/topic-registry", tags=["investment"])


@asynccontextmanager
async def readonly_tenant_transaction(pool, ctx):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True, isolation="repeatable_read"):
            await apply_tenant_context(conn, ctx.user_id, ctx.tenant_id)
            yield conn


@router.get("/{run_id}")
async def read_registry(run_id: UUID, limit: int = Query(default=12, ge=1, le=30), cursor: str | None = Query(default=None, max_length=512), status: Literal["FORMAL", "OBSERVATION_ONLY", "PENDING_REVIEW", "QUARANTINED"] | None = None, include_highlights: bool = Query(default=False, alias="includeHighlights"), pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    now = now_shanghai()
    async with readonly_tenant_transaction(pool, ctx) as conn:
        value = await repository.read_topic_registry(conn, run_id, now, limit=limit, cursor=cursor, status=status, include_highlights=include_highlights)
    return ok(value, request_id=request_id, as_of=now)
