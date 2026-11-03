"""Reading topic pages is a read-only operation, never a research command."""

from contextlib import asynccontextmanager
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context
from backend.errors import MoneyJSONResponse, register_exception_handlers


@pytest.mark.asyncio
async def test_topic_readonly_transaction_uses_one_repeatable_snapshot(monkeypatch):
    module = import_module("backend.investment.research_topic_api")
    options = []

    class Connection:
        @asynccontextmanager
        async def transaction(self, **kwargs):
            options.append(kwargs)
            yield

    connection = Connection()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield connection

    monkeypatch.setattr(module, "apply_tenant_context", AsyncMock())
    ctx = TenantContext(str(uuid4()), str(uuid4()), tenant_role="VIEWER")
    async with module.readonly_tenant_transaction(Pool(), ctx):
        pass
    assert options == [{"readonly": True, "isolation": "repeatable_read"}]


@pytest.mark.parametrize("limit", [12, 20, 30])
def test_topic_page_uses_readonly_transaction_and_passes_bounded_request(
    monkeypatch, limit
):
    module = import_module("backend.investment.research_topic_api")
    run_id, actor, tenant = [uuid4() for _ in range(3)]
    seen = []

    @asynccontextmanager
    async def readonly(*args):
        seen.append("readonly")
        yield object()

    monkeypatch.setattr(module, "readonly_tenant_transaction", readonly)
    read = AsyncMock(
        return_value={"researchId": str(run_id), "items": [], "actionable": False}
    )
    monkeypatch.setattr(module.repository, "read_topic_registry", read)
    app = FastAPI(default_response_class=MoneyJSONResponse)
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        str(actor), str(tenant), tenant_role="VIEWER"
    )
    response = TestClient(app).get(
        f"/api/v1/investment/daily-research/topic-registry/{run_id}?limit={limit}"
    )
    assert response.status_code == 200 and seen == ["readonly"]
    assert response.json()["data"]["actionable"] is False
    assert read.await_args.kwargs["limit"] == limit
