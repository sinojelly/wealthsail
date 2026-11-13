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
        self.attempts = set()

    async def fetchrow(self, sql, tenant, user, code):
        assert "claim_member_fund_nav_sync_attempt" in sql
        assert (tenant, user) == ("tenant", "actor")
        claimed = code not in self.attempts
        self.attempts.add(code)
        return {"claimed": claimed, "last_sync_attempt_at": NOW}

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
async def test_default_nav_read_never_calls_provider_claims_or_enqueues(monkeypatch):
    store = Store([])
    forbidden = AsyncMock(side_effect=AssertionError("read must be local only"))
    store.fetchrow = forbidden
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", forbidden)
    result = await service.ensure_nav_histories(
        store, ["011035"], NOW, tenant_id="tenant", user_id="actor"
    )
    assert result["histories"] == []
    assert result["refresh"]["011035"]["state"] == "IDLE"
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,count,lag",
    [
        ("028764", 19, 0),
        ("011040", 60, 1),
        ("006756", 119, 0),
        ("270042", 220, 2),
        ("161129", 500, 2),
        ("162411", 500, 2),
    ],
)
async def test_failed_history_sync_still_claims_twelve_hour_cooldown(
    monkeypatch, code, count, lag
):
    old = history(code, count=count, lag=lag)
    old.pop("providerHistoryProof")
    store = Store([old])
    fetch = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch)
    options = dict(
        allow_external_fetch=True,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
    )
    first = await service.ensure_nav_histories(store, [code], NOW, **options)
    again = await service.ensure_nav_histories(
        store, [code], NOW + timedelta(minutes=1), **options
    )
    assert fetch.await_count == 1
    assert first["refresh"][code]["reason"] == "NAV_SOURCE_SYNC_FAILED"
    assert first["refresh"][code]["retryAfterSeconds"] == 43200
    assert again["refresh"][code]["reason"] == "NAV_SYNC_COOLDOWN"
    assert again["histories"] == [old] and not store.persisted


@pytest.mark.asyncio
async def test_claim_error_uses_savepoint_and_keeps_other_codes_usable(monkeypatch):
    from contextlib import asynccontextmanager

    class ClaimStore(Store):
        poisoned = False
        savepoints = 0

        @asynccontextmanager
        async def transaction(self):
            self.savepoints += 1
            try:
                yield self
            except RuntimeError:
                self.poisoned = False
                raise

        async def fetchrow(self, sql, tenant, user, code):
            assert not self.poisoned, (
                "previous failed claim poisoned the outer transaction"
            )
            if code == "011035":
                self.poisoned = True
                raise RuntimeError("claim rejected")
            return await super().fetchrow(sql, tenant, user, code)

    store = ClaimStore([])
    fetch = AsyncMock(side_effect=lambda code, now: history(code, count=130, lag=0))
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch)
    result = await service.ensure_nav_histories(
        store,
        ["011035", "011040"],
        NOW,
        allow_external_fetch=True,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
    )
    assert result["refresh"]["011035"]["reason"] == "NAV_SYNC_CLAIM_UNAVAILABLE"
    assert result["refresh"]["011040"]["state"] == "READY"
    assert store.savepoints == 3 and not store.poisoned
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_explicit_refresh_can_claim_in_a_separate_committed_transaction(
    monkeypatch,
):
    store = Store([])
    durable_claims = []

    async def committed_claim(code):
        durable_claims.append(code)
        return {"claimed": True, "last_sync_attempt_at": NOW}

    async def source(code, now):
        assert durable_claims == [code]
        return history(code, count=130, lag=0)

    local_claim = AsyncMock(
        side_effect=AssertionError("independent claim callback owns this operation")
    )
    store.fetchrow = local_claim
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", source)
    result = await service.ensure_nav_histories(
        store,
        ["011035"],
        NOW,
        allow_external_fetch=True,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
        claim_attempt=committed_claim,
    )
    assert result["refresh"]["011035"]["state"] == "READY"
    local_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_short_transaction_factory_releases_single_connection_before_all_provider_io(
    monkeypatch,
):
    import asyncio
    from contextlib import asynccontextmanager
    from backend.funds import latest_quotes

    store = Store([])
    available = asyncio.Semaphore(1)
    borrowed = 0

    @asynccontextmanager
    async def transaction():
        nonlocal borrowed
        async with available:
            borrowed += 1
            try:
                yield store
            finally:
                borrowed -= 1

    async def quotes(*args, **kwargs):
        assert borrowed == 0
        return {"items": []}

    async def source(code, now):
        assert borrowed == 0 and code in store.attempts
        await asyncio.sleep(0)
        return history(code, count=130, lag=0)

    monkeypatch.setattr(latest_quotes.latest_quote_cache, "get", quotes)
    fetch = AsyncMock(side_effect=source)
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch)

    async def refresh():
        return await service.ensure_nav_histories(
            None,
            ["011035"],
            NOW,
            allow_external_fetch=True,
            tenant_id="tenant",
            user_id="actor",
            now_provider=lambda: NOW,
            transaction_factory=transaction,
        )

    results = await asyncio.wait_for(
        asyncio.gather(*(refresh() for _ in range(4))), timeout=2
    )
    assert fetch.await_count == 1 and len(store.persisted) == 1 and borrowed == 0
    assert sum(row["refresh"]["011035"]["state"] == "READY" for row in results) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count,lag", [(119, 0), (220, 4)])
async def test_thin_or_behind_confirmed_history_is_repaired_once_before_pricing(
    monkeypatch, count, lag
):
    existing = history(count=count, lag=lag)
    if count < 120:
        existing.pop("providerHistoryProof")
    store = Store([existing])
    fresh = history(count=550, lag=0)
    fetch = AsyncMock(return_value=fresh)
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch, raising=False)
    result = await service.ensure_nav_histories(
        store,
        ["011035", "011035"],
        NOW,
        allow_external_fetch=True,
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
@pytest.mark.parametrize("count", [1, 19, 20, 60, 119])
async def test_complete_short_history_survives_provider_cooldown_without_refetch(
    monkeypatch,
    count,
):
    store = Store([])
    fresh = history(count=count, lag=0)
    fetch = AsyncMock(return_value=fresh)
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch)
    kwargs = dict(
        allow_external_fetch=True,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
    )

    first = await service.ensure_nav_histories(
        store,
        ["011035"],
        NOW,
        now_provider=lambda: NOW,
        **kwargs,
    )
    later = NOW + timedelta(minutes=5)
    second = await service.ensure_nav_histories(
        store,
        ["011035"],
        later,
        now_provider=lambda: later,
        **kwargs,
    )

    assert fetch.await_count == len(store.persisted) == 1
    assert first["refresh"]["011035"]["state"] == "PARTIAL"
    assert first["refresh"]["011035"]["reason"] == "LOCAL_NAV_HISTORY_INSUFFICIENT"
    assert second["refresh"]["011035"]["state"] == "IDLE"
    assert second["histories"] == [fresh]
    selected = service.select_history_view(
        "011035",
        "新基金",
        "2y",
        second["histories"],
        later,
    )
    assert selected["state"] == "READY", (
        "The fetched window is complete, even for a new fund"
    )
    assert selected["coverage"]["sampleCount"] == count
    assert selected["points"] == [
        {"date": row["date"], "unitNav": row["close"]} for row in fresh["points"]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["newer_confirmed_nav", "source_recheck_due"])
async def test_complete_short_history_still_repairs_when_new_facts_or_recheck_are_due(
    monkeypatch,
    reason,
):
    existing = history(count=60, lag=1 if reason == "newer_confirmed_nav" else 0)
    if reason == "source_recheck_due":
        old = (NOW - timedelta(hours=36, seconds=1)).isoformat()
        existing.update(retrievedAt=old, availableAt=old)
        existing["providerHistoryProof"]["retrievedAt"] = old
    store = Store([existing])
    fresh = history(count=60, lag=0)
    fetch = AsyncMock(return_value=fresh)
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch)

    result = await service.ensure_nav_histories(
        store,
        ["011035"],
        NOW,
        allow_external_fetch=True,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={"011035": {"asOf": NOW.date().isoformat()}},
        now_provider=lambda: NOW,
    )

    assert fetch.await_count == len(store.persisted) == 1
    assert result["refresh"]["011035"]["state"] == "PARTIAL"
    assert result["refresh"]["011035"]["reason"] == "LOCAL_NAV_HISTORY_INSUFFICIENT"
    assert result["histories"][-1] == fresh


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
        allow_external_fetch=True,
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
    old.pop("providerHistoryProof")
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
        allow_external_fetch=True,
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
        Cache(),
        "011035",
        "2y",
        allow_external_fetch=True,
        tenant_id="tenant",
        user_id="actor",
        now=NOW,
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
async def test_persistence_failure_retains_old_facts_and_recovers_after_cooldown(
    monkeypatch,
):
    old, fresh = history(count=80), history(count=550, lag=0)
    old.pop("providerHistoryProof")
    store = Store([old])
    persist = store.fetchval
    store.fetchval = AsyncMock(side_effect=RuntimeError("temporary database error"))
    monkeypatch.setattr(
        service, "fetch_on_demand_nav_history", AsyncMock(return_value=fresh)
    )
    kwargs = dict(
        allow_external_fetch=True,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
    )
    first = await service.ensure_nav_histories(store, ["011035"], NOW, **kwargs)
    assert first["histories"] == [old]
    assert first["refresh"]["011035"]["reason"] == "NAV_HISTORY_PERSIST_FAILED"
    store.fetchval = persist
    retry = await service.ensure_nav_histories(store, ["011035"], NOW, **kwargs)
    assert retry["refresh"]["011035"]["reason"] == "NAV_SYNC_COOLDOWN"
    store.attempts.clear()  # Simulate the next database claim after 12 hours.
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
        allow_external_fetch=True,
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
        allow_external_fetch=True,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
    )
    assert fetch.await_count == 1 and result["refresh"]["011035"]["state"] == "READY"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_proof,lag", [(True, 0), (False, 20)])
async def test_recent_check_does_not_prevent_repair_of_uncovered_or_stale_nav(
    monkeypatch,
    missing_proof,
    lag,
):
    incomplete = history(count=220, lag=lag)
    if missing_proof:
        incomplete.pop("providerHistoryProof")
    fetch = AsyncMock(return_value=history(count=550, lag=0))
    monkeypatch.setattr(service, "fetch_on_demand_nav_history", fetch)
    result = await service.ensure_nav_histories(
        Store([incomplete]),
        ["011035"],
        NOW,
        allow_external_fetch=True,
        tenant_id="tenant",
        user_id="actor",
        latest_confirmed={},
        now_provider=lambda: NOW,
    )
    assert fetch.await_count == 1
    assert result["refresh"]["011035"]["state"] == "READY"
