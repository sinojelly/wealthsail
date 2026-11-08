"""Operations exposes only read-only historical research preview identities."""

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.common.tenancy import TenantContext
from backend.errors import ForbiddenError
from backend.operations import api


@pytest.mark.asyncio
async def test_operations_jobs_lists_frozen_research_preview_metadata(monkeypatch):
    from backend.macro import research_preview

    now = datetime(2026, 11, 8, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    conn = object()

    @asynccontextmanager
    async def transaction(*_args):
        yield conn

    metadata = [
        {
            "runId": str(uuid4()),
            "status": "FAILED",
            "startedAt": now,
            "finishedAt": now,
            "errorClass": "ResearchValidationError",
            "canPreview": True,
            "previewMissingReasons": [],
        }
    ]
    listing = AsyncMock(return_value=metadata)
    monkeypatch.setattr(api, "tenant_transaction", transaction)
    monkeypatch.setattr(api, "now_shanghai", lambda: now)
    monkeypatch.setattr(api.repository, "list_jobs", AsyncMock(return_value=[]))
    monkeypatch.setattr(api.repository, "list_job_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(research_preview, "list_preview_runs", listing)
    ctx = TenantContext(
        user_id=str(uuid4()), tenant_id=str(uuid4()), tenant_role="OWNER"
    )
    result = await api.operations_jobs(pool=object(), ctx=ctx, request_id="offline")
    assert result["data"]["researchRuns"] == metadata
    listing.assert_awaited_once_with(conn, limit=30, now=now)


@pytest.mark.asyncio
async def test_viewer_cannot_load_operations_or_research_preview_metadata():
    ctx = TenantContext(
        user_id=str(uuid4()), tenant_id=str(uuid4()), tenant_role="VIEWER"
    )
    with pytest.raises(ForbiddenError):
        await api.operations_jobs(pool=object(), ctx=ctx, request_id="offline")
