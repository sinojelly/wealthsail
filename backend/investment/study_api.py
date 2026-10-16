"""Private research journals and links to existing confirmed transactions."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from backend.common.database import tenant_transaction
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.investment import study_repository as repository, study_service as service
from backend.investment.study_schemas import (
    CreateStudyRequest,
    VersionRequest,
    StatusRequest,
    CheckRequest,
    NoteRequest,
    TradeLinkRequest,
)

router = APIRouter(prefix="/investment/study-tracks", tags=["investment"])


@router.get("")
async def list_tracks(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await repository.list_tracks(
            conn, ctx.tenant_id, ctx.user_id, now, limit=limit, offset=offset
        )
    return ok(data, request_id=request_id, as_of=now)


@router.post("")
async def create_track(
    body: CreateStudyRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=120),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.create_study(
            conn, ctx.tenant_id, ctx.user_id, body, now, idempotency_key=idempotency_key
        )
    return ok(data, request_id=request_id, as_of=now)


@router.get("/{track_id}")
async def get_track(
    track_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_detail(
            conn, ctx.tenant_id, ctx.user_id, track_id, now, limit=limit, offset=offset
        )
    return ok(data, request_id=request_id, as_of=now)


@router.post("/{track_id}/versions")
async def add_version(
    track_id: UUID,
    body: VersionRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=120),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.append_version(
            conn,
            ctx.tenant_id,
            ctx.user_id,
            track_id,
            body,
            now,
            idempotency_key=idempotency_key,
        )
    return ok(data, request_id=request_id, as_of=now)


@router.post("/{track_id}/status")
async def set_status(
    track_id: UUID,
    body: StatusRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=120),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.set_status(
            conn,
            ctx.tenant_id,
            ctx.user_id,
            track_id,
            body,
            now,
            idempotency_key=idempotency_key,
        )
    return ok(data, request_id=request_id, as_of=now)


@router.post("/{track_id}/check")
async def check_track(
    track_id: UUID,
    body: CheckRequest,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        await service.check_study(
            conn, ctx.tenant_id, ctx.user_id, track_id, body.expected_generation, now
        )
        data = await service.get_detail(conn, ctx.tenant_id, ctx.user_id, track_id, now)
    return ok(data, request_id=request_id, as_of=now)


@router.post("/{track_id}/notes")
async def add_note(
    track_id: UUID,
    body: NoteRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=120),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.append_note(
            conn,
            ctx.tenant_id,
            ctx.user_id,
            track_id,
            body,
            now,
            idempotency_key=idempotency_key,
        )
    return ok(data, request_id=request_id, as_of=now)


@router.get("/{track_id}/eligible-trades")
async def eligible_trades(
    track_id: UUID,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.eligible_trades(
            conn, ctx.tenant_id, ctx.user_id, track_id, now
        )
    return ok(data, request_id=request_id, as_of=now)


@router.post("/{track_id}/trade-links")
async def link_trade(
    track_id: UUID,
    body: TradeLinkRequest,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.link_trade(
            conn, ctx.tenant_id, ctx.user_id, track_id, body, now
        )
    return ok(data, request_id=request_id, as_of=now)
