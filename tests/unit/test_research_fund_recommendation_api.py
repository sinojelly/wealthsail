"""Recommendation reads may repair public NAV inside the actor's tenant."""

from contextlib import asynccontextmanager
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context
from backend.errors import MoneyJSONResponse


def test_recommendations_are_available_without_a_formal_report(monkeypatch):
    assert util.find_spec("backend.investment.research_fund_recommendation_api"), (
        "recommendation GET missing"
    )
    module = import_module("backend.investment.research_fund_recommendation_api")
    run_id = uuid4()
    seen = []

    @asynccontextmanager
    async def transaction(*args):
        seen.append(args)
        yield object()

    monkeypatch.setattr(module, "tenant_transaction", transaction)
    read = AsyncMock(
        return_value={
            "researchId": str(run_id),
            "items": [{"recommendedFunds": [{"fundCode": "123456"}]}],
            "actionable": False,
        }
    )
    monkeypatch.setattr(module, "read_topic_fund_recommendations", read)
    app = FastAPI(default_response_class=MoneyJSONResponse)
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        str(uuid4()), str(uuid4()), tenant_role="VIEWER"
    )
    response = TestClient(app).get(
        f"/api/v1/investment/daily-research/topic-fund-recommendations/{run_id}"
    )
    assert response.status_code == 200 and len(seen) == 1
    assert (
        response.json()["data"]["items"][0]["recommendedFunds"][0]["fundCode"]
        == "123456"
    )
    assert read.await_args.args[1] == run_id
    assert seen[0][1:] == (read.await_args.kwargs["user_id"], read.await_args.kwargs["tenant_id"])
