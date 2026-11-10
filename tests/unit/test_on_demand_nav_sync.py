"""Personal fund reads repair thin/stale public NAV cohorts before pricing."""

from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.funds import nav_history as service
from backend.integrations.nav_history_proof import nav_points_digest

NOW = datetime(2026, 11, 9, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def history(code="011035", count=220, lag=3):
    points = [
        {
            "date": (NOW.date() - timedelta(days=lag + index)).isoformat(),
            "close": str(1 + index / 1000),
        }
        for index in reversed(range(count))
    ]
    return {
        "fundCode": code,
        "priceBasis": "UNIT_NAV",
        "sourceUri": f"https://fund.eastmoney.com/{code}.html",
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "asOf": points[-1]["date"],
        "points": points,
        "providerHistoryProof": {
            "methodVersion": "eastmoney-nav-history-v1",
            "complete": True,
            "fundCode": code,
            "sourceUri": "https://api.fund.eastmoney.com/f10/lsjz",
            "retrievedAt": NOW.isoformat(),
            "queryStart": (NOW.date() - timedelta(days=900)).isoformat(),
            "queryEnd": NOW.date().isoformat(),
            "pointCount": len(points),
            "uniquePointCount": len(points),
            "totalCount": len(points),
            "receivedRowCount": len(points),
            "pageCount": 3,
            "requiredPages": 3,
            "firstDate": points[0]["date"],
            "lastDate": points[-1]["date"],
            "pointsDigest": nav_points_digest(points),
        },
    }


class Store:
    def __init__(self, histories):
        self.histories, self.persisted = deepcopy(histories), []

    async def fetch(self, sql, *args):
        assert "fund_nav_chart_histories" in sql
        return [
            {"fund_code": row["fundCode"], "payload": row} for row in self.histories
        ]

    async def fetchval(self, sql, tenant, user, code, payload):
        import json

        assert "append_fund_nav_sync_history" in sql
        assert (tenant, user) == ("tenant", "actor")
        row = json.loads(payload)
        assert row["fundCode"] == code
        self.persisted.append(row)
        self.histories.append(row)
        return "persisted-id"

    def transaction(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def transaction():
            yield self

        return transaction()


@pytest.mark.asyncio
@pytest.mark.parametrize("count,lag", [(119, 0), (220, 4)])
async def test_thin_or_behind_confirmed_history_is_repaired_once_before_pricing(
    monkeypatch, count, lag
):
    store = Store([history(count=count, lag=lag)])
    fresh = history(count=550, lag=0)
    fetch = AsyncMock(return_value=fresh)
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch, raising=False)
    result = await service.ensure_nav_histories(
        store,
        ["011035", "011035"],
        NOW,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={"011035": {"asOf": NOW.date().isoformat()}},
        now_provider=lambda: NOW,
    )
    assert len(store.persisted) == 1
    selected = service.select_history_view(
        "011035", "基金", "2y", result["histories"], result["asOf"]
    )
    assert selected["asOf"] == NOW.date().isoformat()
    assert len(selected["points"]) == 500
    assert fetch.await_count == 1
    assert result["refresh"]["011035"]["state"] == "READY"


@pytest.mark.asyncio
async def test_sufficient_current_history_skips_provider_and_database_writes(
    monkeypatch,
):
    store = Store([history(lag=0)])
    fetch = AsyncMock(side_effect=AssertionError("already complete"))
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch, raising=False)
    result = await service.ensure_nav_histories(
        store,
        ["011035"],
        NOW,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={"011035": {"asOf": NOW.date().isoformat()}},
        now_provider=lambda: NOW,
    )
    assert result["refresh"]["011035"]["state"] == "IDLE"
    assert len(result["histories"]) == 1 and not store.persisted


@pytest.mark.asyncio
async def test_provider_failure_retains_actual_old_history_and_reports_recoverable_failure(
    monkeypatch,
):
    old = history(count=80)
    store = Store([old])
    monkeypatch.setattr(
        service,
        "fetch_on_demand_nav_history",
        AsyncMock(side_effect=TimeoutError()),
        raising=False,
    )
    result = await service.ensure_nav_histories(
        store,
        ["011035"],
        NOW,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
    )
    assert result["histories"] == [old] and not store.persisted
    assert result["refresh"]["011035"]["state"] == "FAILED"
    assert result["refresh"]["011035"]["reason"] == "NAV_SOURCE_SYNC_FAILED"


@pytest.mark.asyncio
async def test_chart_get_returns_newly_persisted_complete_sequence_immediately(
    monkeypatch,
):
    from tests.unit.test_nav_history47 import Cache

    fresh = history(count=550, lag=0)
    repaired = AsyncMock(
        return_value={
            "histories": [fresh],
            "asOf": NOW,
            "refresh": {"011035": {"state": "READY"}},
            "latestConfirmed": {},
        }
    )
    monkeypatch.setattr(service, "ensure_nav_histories", repaired)
    result = await service.get_nav_history(
        Cache(), "011035", "2y", tenant_id="tenant", user_id="actor", now=NOW
    )
    assert result["asOf"] == NOW.date().isoformat() and len(result["points"]) == 500
    assert result["refresh"]["state"] == "READY"


def test_all_chart_uses_latest_deep_sequence_even_when_old_cohort_has_full_proof():
    from backend.jobs.fund_nav_full_history import validate_full_payload

    old = history(count=550, lag=3)
    full = validate_full_payload(
        {
            "fundCode": "011035",
            "sourceUri": "https://fund.eastmoney.com/pingzhongdata/011035.js",
            "queryStart": "1900-01-01",
            "queryEnd": NOW.date().isoformat(),
            "complete": True,
            "receivedRowCount": 550,
            "points": old["points"],
        },
        "011035",
        NOW,
    )
    fresh = history(count=550, lag=0)
    selected = service.select_history_view("011035", "基金", "all", [full, fresh], NOW)
    assert selected["asOf"] == NOW.date().isoformat()
    assert (
        selected["state"] == "PARTIAL" and selected["coverage"]["fullHistory"] is False
    )


@pytest.mark.asyncio
async def test_persistence_failure_retains_old_facts_and_recovers_on_next_read(
    monkeypatch,
):
    old, fresh = history(count=80), history(count=550, lag=0)
    store = Store([old])
    persist = store.fetchval
    store.fetchval = AsyncMock(side_effect=RuntimeError("temporary database error"))
    monkeypatch.setattr(
        service, "fetch_on_demand_nav_history", AsyncMock(return_value=fresh)
    )
    kwargs = dict(
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
    )
    first = await service.ensure_nav_histories(store, ["011035"], NOW, **kwargs)
    assert first["histories"] == [old]
    assert first["refresh"]["011035"]["reason"] == "NAV_HISTORY_PERSIST_FAILED"
    store.fetchval = persist
    second = await service.ensure_nav_histories(store, ["011035"], NOW, **kwargs)
    assert second["refresh"]["011035"]["state"] == "READY" and len(store.persisted) == 1


@pytest.mark.asyncio
async def test_requested_budget_can_attempt_every_displayed_fund_beyond_32(monkeypatch):
    codes = [f"{index:06}" for index in range(36)]
    store = Store([])
    fetch = AsyncMock(side_effect=lambda code, now: history(code, count=130, lag=0))
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch)
    result = await service.ensure_nav_histories(
        store,
        codes,
        NOW,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
        provider_budget=len(codes),
    )
    assert fetch.await_count == len(store.persisted) == 36
    assert all(row["state"] == "READY" for row in result["refresh"].values())


@pytest.mark.asyncio
async def test_partial_source_proof_is_retried_despite_recent_sufficient_samples(
    monkeypatch,
):
    partial = history(count=220, lag=0)
    partial["coverageReason"] = "SOURCE_HISTORY_INCOMPLETE"
    partial.pop("providerHistoryProof")
    fetch = AsyncMock(return_value=history(count=550, lag=0))
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch)
    result = await service.ensure_nav_histories(
        Store([partial]),
        ["011035"],
        NOW,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
    )
    assert fetch.await_count == 1 and result["refresh"]["011035"]["state"] == "READY"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_proof,lag", [(True, 0), (False, 20)])
async def test_recent_check_does_not_prevent_repair_of_uncovered_or_stale_nav(
    monkeypatch, missing_proof, lag,
):
    incomplete = history(count=220, lag=lag)
    if missing_proof:
        incomplete.pop("providerHistoryProof")
    fetch = AsyncMock(return_value=history(count=550, lag=0))
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch)
    result = await service.ensure_nav_histories(
        Store([incomplete]), ["011035"], NOW, tenant_id="tenant", user_id="actor",
        latest_confirmed={}, now_provider=lambda: NOW,
    )
    assert fetch.await_count == 1
    assert result["refresh"]["011035"]["state"] == "READY"
