"""Direction selection and private scope travel together through the read API."""

from contextlib import asynccontextmanager
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context
from backend.main import create_app
from backend.errors import NotFoundError
from test_opportunity_market import NOW


def service():
    assert util.find_spec("backend.investment.opportunity_service"), (
        "scoped opportunity context is not implemented"
    )
    return import_module("backend.investment.opportunity_service")


def dependencies(monkeypatch):
    module = service()
    from backend.macro import alpha_repository
    from backend.investment import theme_graph

    monkeypatch.setattr(
        alpha_repository, "read_alpha_public", AsyncMock(return_value={})
    )
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        theme_graph, "cached_nav_analysis", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        module,
        "read_daily_research",
        AsyncMock(
            return_value={
                "methodVersion": "daily-research-v1",
                "state": "NOT_RUN",
                "report": None,
            }
        ),
    )
    monkeypatch.setattr(module, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(
        module,
        "market_context",
        AsyncMock(
            return_value={
                "items": [],
                "months": 36,
                "research": {"changes": [], "dimensions": []},
                "events": [],
                "state": "UNVERIFIED",
            }
        ),
    )
    monkeypatch.setattr(
        module.market_repository, "read_series", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        module.market_repository, "read_permissions", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        module.market_repository, "read_collection", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        module,
        "decision_context",
        AsyncMock(
            return_value={
                "decision": {"state": "WAIT", "summary": "证据条件不足", "gates": []},
                "portfolio": {
                    "state": "UNKNOWN",
                    "summary": "未知持仓",
                    "heldSelected": None,
                },
                "notices": [],
            }
        ),
    )
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("market", ["SSE", "HK", None])
async def test_nav_fallback_cannot_relabel_mainland_code_as_other_or_unknown_market(
    monkeypatch, market
):
    module = dependencies(monkeypatch)
    from backend.investment import theme_graph

    cached = AsyncMock(
        return_value={
            "actionableZones": {
                "subjectKind": "nav",
                "subjectId": "008281",
                "priceBasis": "UNIT_NAV",
                "market": "FUND_CN",
                "tracks": [],
            }
        }
    )
    monkeypatch.setattr(theme_graph, "cached_nav_analysis", cached)
    conn = AsyncMock()
    conn.fetch.return_value = []
    result = await module.get_context(
        conn, "tenant-a", "user-a", fund_code="008281", fund_market=market, now=NOW
    )
    assert result["decision"].get("actionableZones") is None


@pytest.mark.asyncio
async def test_unknown_direction_cannot_silently_fall_back_to_ashare(monkeypatch):
    module = dependencies(monkeypatch)
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with pytest.raises(NotFoundError):
        await module.get_context(
            conn, "tenant-a", "user-a", direction_id="bad-direction", now=NOW
        )


@pytest.mark.asyncio
async def test_context_keeps_actor_and_original_unresolved_fund_scope(monkeypatch):
    module = dependencies(monkeypatch)
    conn = AsyncMock()
    conn.fetch.return_value = []
    result = await module.get_context(
        conn, "tenant-a", "user-a", fund_code="000001", fund_market="HK", now=NOW
    )
    assert result["direction"]["marketId"] == "other"
    call = module.decision_context.await_args
    assert call.args[1:3] == ("tenant-a", "user-a")
    assert call.kwargs["fund_market"] == "HK" and call.kwargs["fund_code"] == "000001"
    assert result["decision"]["state"] == "WAIT"
    assert result["selectionState"] == "UNKNOWN"
    assert result["actionableZones"] == []
    assert "researchBoard" not in result and "radar" not in result


@pytest.mark.asyncio
async def test_context_uses_configured_daily_pipeline_window_for_status(monkeypatch):
    from backend.config import Settings

    module = dependencies(monkeypatch)
    configured = Settings(
        _env_file=None,
        daily_research_enabled=True,
        daily_research_start_window_minutes=90,
        daily_research_schedule_mode="WEEKDAYS",
        daily_research_schedule_time="08:15",
    )
    monkeypatch.setattr(module, "get_settings", lambda: configured)
    conn = AsyncMock()
    conn.fetch.return_value = []
    await module.get_context(
        conn, "tenant-a", "user-a", fund_code="000001", fund_market="HK", now=NOW
    )
    assert module.read_daily_research.await_args.kwargs["enabled"] is True
    assert module.read_daily_research.await_args.kwargs["start_window_minutes"] == 90
    assert module.read_daily_research.await_args.kwargs["schedule_mode"] == "WEEKDAYS"
    assert module.read_daily_research.await_args.kwargs["schedule_time"] == "08:15"


def test_new_opportunity_route_is_authenticated_and_passes_full_selection(monkeypatch):
    module = service()
    api = import_module("backend.investment.radar_api")

    @asynccontextmanager
    async def transaction(*args):
        yield object()

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    read = AsyncMock(return_value={"radar": {"questions": []}})
    monkeypatch.setattr(module, "get_context", read)
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        "user-a", "tenant-a"
    )
    ident = str(uuid4())
    response = TestClient(app).get(
        f"/api/v1/investment/opportunity-context?directionId=gold:physical&indexId=au9999&instrumentId={ident}&fundCode=999999&fundMarket=SGE&months=12"
    )
    assert response.status_code == 200
    call = read.await_args
    assert call.args[1:3] == ("tenant-a", "user-a")
    assert str(call.kwargs["instrument_id"]) == ident
    assert call.kwargs["fund_market"] == "SGE" and call.kwargs["months"] == 12
    assert call.kwargs["can_manage_operations"] is False


@pytest.mark.parametrize(
    "query", ["months=0", "months=121", "instrumentId=bad", "fundCode=" * 1]
)
def test_new_opportunity_route_rejects_invalid_selection(query):
    service()
    app = create_app()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        "user-a", "tenant-a"
    )
    app.dependency_overrides[get_pool] = lambda: object()
    assert (
        TestClient(app)
        .get("/api/v1/investment/opportunity-context?" + query)
        .status_code
        == 400
    )


def test_market_evidence_does_not_claim_earnings_or_value_from_technical_score():
    module = service()
    result = module.market_evidence(
        {"id": "hs300", "name": "沪深300"},
        {
            "state": "STRONG",
            "score": 90,
            "points": [{"date": "2026-10-13", "close": 100}],
            "indicators": {"ma200": 90, "momentum63": 0.1, "volatility20": 0.2},
            "source": "https://example.org/market",
        },
    )
    assert result["trend"]["state"] == "READY"
    assert result["earnings"]["state"] == "UNKNOWN"
    assert result["valuation"]["state"] != "READY"
    assert "企业盈利证据" in result["missingEvidence"]


@pytest.mark.asyncio
async def test_selected_fund_and_index_reject_a_known_verified_contradiction(
    monkeypatch,
):
    module = dependencies(monkeypatch)
    monkeypatch.setattr(
        module, "analysis", AsyncMock(return_value={"state": "NOT_COLLECTED"})
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": "hs300", "name": "沪深300"}
    conn.fetchval.return_value = False
    conn.fetch.return_value = [
        {"index_id": "zz500", "direction_ids": ["a-share:broad"]}
    ]
    monkeypatch.setattr(
        module,
        "resolve_instrument",
        AsyncMock(return_value="known-instrument"),
        raising=False,
    )
    from backend.errors import ValidationError_

    with pytest.raises(ValidationError_):
        await module.get_context(
            conn,
            "tenant-a",
            "user-a",
            index_id="hs300",
            instrument_id=str(uuid4()),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_explicit_instrument_cannot_be_mixed_with_another_fund_identity(
    monkeypatch,
):
    module = dependencies(monkeypatch)
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": "hs300", "name": "沪深300"}
    conn.fetchval.return_value = False
    from backend.errors import ValidationError_

    with pytest.raises(ValidationError_):
        await module.get_context(
            conn,
            "tenant-a",
            "user-a",
            instrument_id=str(uuid4()),
            fund_code="other-code",
            fund_market="CN",
            now=NOW,
        )


def test_invalid_market_measurements_remain_unknown():
    module = service()
    result = module.market_evidence(
        {"id": "hs300"},
        {
            "state": "STRONG",
            "valuation": {"state": "READY"},
            "indicators": {"ma200": 90, "momentum63": 0.1, "volatility20": -0.2},
            "points": [{"date": "2026-10-13", "close": 100}],
        },
    )
    assert result["valuation"]["state"] != "READY"
    assert result["risk"]["state"] != "READY"


@pytest.mark.asyncio
async def test_missing_verified_mapping_keeps_radar_and_blocks_fund_qualification(
    monkeypatch,
):
    module = dependencies(monkeypatch)
    monkeypatch.setattr(
        module,
        "resolve_instrument",
        AsyncMock(return_value="known-instrument"),
        raising=False,
    )
    monkeypatch.setattr(
        module, "analysis", AsyncMock(return_value={"state": "NOT_COLLECTED"})
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": "hs300", "name": "沪深300"}
    conn.fetchval.return_value = False
    conn.fetch.return_value = []
    result = await module.get_context(
        conn,
        "tenant-a",
        "user-a",
        index_id="hs300",
        instrument_id=str(uuid4()),
        now=NOW,
    )
    assert result["selectionState"] == "UNKNOWN"
    assert (
        "基金指数/方向归属尚待唯一核查" in result["directionModel"]["missingEvidence"]
    )
    assert result["actionableZones"][0]["subjectId"] == "hs300"
    assert result["actionableZones"][0]["orderPriceAvailable"] is False


@pytest.mark.asyncio
async def test_code_only_resolved_identity_cannot_skip_direction_mapping_check(
    monkeypatch,
):
    module = dependencies(monkeypatch)
    monkeypatch.setattr(module.repository, "indices", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        module,
        "resolve_instrument",
        AsyncMock(return_value="known-instrument"),
        raising=False,
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": "us:broad",
        "market_id": "us",
        "label": "美股宽基",
    }
    conn.fetch.return_value = [
        {"index_id": "hs300", "direction_ids": ["a-share:broad"]}
    ]
    from backend.errors import ValidationError_

    with pytest.raises(ValidationError_):
        await module.get_context(
            conn,
            "tenant-a",
            "user-a",
            direction_id="us:broad",
            fund_code="000001",
            fund_market="FUND_CN",
            now=NOW,
        )


@pytest.mark.asyncio
async def test_legacy_macro_context_uses_the_same_requested_clock(monkeypatch):
    from backend.investment import service as legacy
    from datetime import timedelta

    monkeypatch.setattr(legacy, "read_macro_evidence", AsyncMock(return_value={}))
    monkeypatch.setattr(
        legacy.repository,
        "macro",
        AsyncMock(
            return_value=[
                {
                    "field_code": "PMI",
                    "value": 51,
                    "as_of": NOW,
                    "published_at": NOW + timedelta(days=1),
                    "retrieved_at": NOW,
                    "quality_status": "FRESH",
                    "max_age_days": 45,
                }
            ]
        ),
    )
    monkeypatch.setattr(legacy.repository, "macro_history", AsyncMock(return_value=[]))
    conn = AsyncMock()
    conn.fetch.return_value = []
    result = await legacy.market_context(conn, now=NOW)
    assert result["items"][0]["value"] is None


def test_bad_intermediate_price_does_not_claim_complete_risk_evidence():
    module = service()
    result = module.market_evidence(
        {"id": "hs300"},
        {
            "state": "STRONG",
            "indicators": {"ma200": 90, "momentum63": 0.1, "volatility20": 0.2},
            "points": [
                {"date": "2026-10-12", "close": float("nan")},
                {"date": "2026-10-13", "close": 100},
            ],
        },
    )
    assert result["risk"]["state"] != "READY"
    assert "指数波动与回撤" in result["missingEvidence"]


@pytest.mark.asyncio
async def test_direction_only_fund_uses_its_verified_index_and_retains_notice_scope(
    monkeypatch,
):
    module = dependencies(monkeypatch)
    monkeypatch.setattr(
        module, "resolve_instrument", AsyncMock(return_value="known-instrument")
    )
    monkeypatch.setattr(
        module.repository,
        "indices",
        AsyncMock(return_value=[{"id": "zz500", "name": "中证500"}]),
    )
    analyze = AsyncMock(return_value={"state": "NOT_COLLECTED"})
    monkeypatch.setattr(module, "analysis", analyze)
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": "a-share:broad",
        "market_id": "a-share",
        "label": "宽基",
    }
    conn.fetch.return_value = [
        {"index_id": "hs300", "name": "沪深300", "direction_ids": ["a-share:broad"]}
    ]
    result = await module.get_context(
        conn,
        "tenant-a",
        "user-a",
        direction_id="a-share:broad",
        fund_code="000001",
        fund_market="FUND_CN",
        now=NOW,
    )
    assert [e["indexId"] for e in result["marketEvidence"]] == ["hs300"]
    assert module.decision_context.await_args.kwargs["evaluated_index_id"] == "hs300"
    assert module.decision_context.await_args.kwargs["index_id"] is None


@pytest.mark.asyncio
async def test_context_exposes_actual_selected_index_plan_without_legacy_board(
    monkeypatch,
):
    from datetime import timedelta

    module = dependencies(monkeypatch)
    monkeypatch.setattr(module, "resolve_instrument", AsyncMock(return_value=None))
    monkeypatch.setattr(
        module,
        "analysis",
        AsyncMock(
            return_value={
                "state": "STRONG",
                "asOf": NOW.date().isoformat(),
                "dataQuality": "READY",
                "retrievedAt": NOW.isoformat(),
                "source": "https://quotes.test/a500",
                "points": [
                    {
                        "date": (NOW.date() - timedelta(days=29 - i)).isoformat(),
                        "close": 100 + i % 2,
                    }
                    for i in range(30)
                ],
                "indicators": {"ma200": 100, "momentum63": 0.05},
            }
        ),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": "a500", "name": "中证A500"}
    conn.fetch.return_value = []
    result = await module.get_context(
        conn, "tenant-a", "user-a", index_id="a500", now=NOW
    )
    assert len(result["actionableZones"]) == 1
    assert result["actionableZones"][0]["subjectId"] == "a500"
    assert result["actionableZones"][0]["label"] == "中证A500"
    assert result["actionableZones"][0]["currentPrice"] == "101.00"
    assert (
        "researchOverview" not in result
        and "researchBoard" not in result
        and "radar" not in result
    )
