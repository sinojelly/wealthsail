"""Account performance baseline read/update contracts."""

from __future__ import annotations

from datetime import date
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.errors import IdempotencyConflictError, VersionConflictError
from backend.portfolio import api, repository, service


class QueryConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.calls.append((" ".join(sql.split()), args))
        return []


@pytest.mark.asyncio
async def test_baseline_list_is_limited_to_current_beneficial_owner() -> None:
    conn = QueryConn()

    await repository.list_performance_baselines(
        conn, tenant_id="tenant-1", owner_user_id="user-1"
    )

    sql, args = conn.calls[0]
    assert "identity.account_members" in sql
    assert "BENEFICIAL_OWNER" in sql
    assert "JOINT_OWNER" in sql
    assert "DISTINCT ON (baseline.account_id)" in sql
    assert args == ("tenant-1", "user-1")


@pytest.mark.asyncio
async def test_user_revision_is_append_only_versioned_and_audited(monkeypatch) -> None:
    inserted: dict[str, object] = {}
    audits: list[dict] = []

    async def previous(_conn, **_kwargs):
        return None

    async def command_lock(_conn, **_kwargs):
        return None

    async def locked(_conn, **_kwargs):
        return {
            "account_id": "account-1",
            "account_name": "天天基金账户",
            "baseline_as_of": date(2026, 8, 1),
            "baseline_total_asset": Decimal("100000"),
            "initial_cumulative_pnl": Decimal("0"),
            "source": "AUTO_MIGRATION",
            "version": 1,
            "created_at": None,
        }

    async def insert(_conn, **kwargs):
        inserted.update(kwargs)
        return {
            **(await locked(_conn)),
            "initial_cumulative_pnl": kwargs["initial_cumulative_pnl"],
            "source": "USER",
            "version": kwargs["version"],
        }

    async def audit(_conn, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(repository, "lock_performance_baseline_command", command_lock)
    monkeypatch.setattr(repository, "find_performance_baseline_by_idempotency", previous)
    monkeypatch.setattr(repository, "lock_latest_performance_baseline", locked)
    monkeypatch.setattr(repository, "insert_performance_baseline_revision", insert)
    monkeypatch.setattr(service, "write_audit", audit)

    result = await service.revise_performance_baseline(
        object(),
        tenant_id="tenant-1",
        actor_user_id="user-1",
        account_id="account-1",
        initial_cumulative_pnl=Decimal("-1234.56"),
        expected_version=1,
        idempotency_key="baseline-command-1",
    )

    assert inserted["version"] == 2
    assert inserted["initial_cumulative_pnl"] == Decimal("-1234.56")
    assert result["version"] == 2
    assert result["initialCumulativePnl"] == "-1234.56"
    assert audits[0]["action"] == "PERFORMANCE_BASELINE_REVISED"


@pytest.mark.asyncio
async def test_revision_rejects_stale_version(monkeypatch) -> None:
    async def previous(_conn, **_kwargs):
        return None

    async def command_lock(_conn, **_kwargs):
        return None

    async def locked(_conn, **_kwargs):
        return {"version": 3}

    monkeypatch.setattr(repository, "lock_performance_baseline_command", command_lock)
    monkeypatch.setattr(repository, "find_performance_baseline_by_idempotency", previous)
    monkeypatch.setattr(repository, "lock_latest_performance_baseline", locked)

    with pytest.raises(VersionConflictError):
        await service.revise_performance_baseline(
            object(),
            tenant_id="tenant-1",
            actor_user_id="user-1",
            account_id="account-1",
            initial_cumulative_pnl=Decimal("0"),
            expected_version=2,
            idempotency_key="baseline-command-2",
        )


@pytest.mark.asyncio
async def test_revision_replay_requires_same_request_hash(monkeypatch) -> None:
    async def previous(_conn, **_kwargs):
        return {"request_hash": "not-the-request-hash"}

    async def command_lock(_conn, **_kwargs):
        return None

    monkeypatch.setattr(repository, "lock_performance_baseline_command", command_lock)
    monkeypatch.setattr(repository, "find_performance_baseline_by_idempotency", previous)

    with pytest.raises(IdempotencyConflictError):
        await service.revise_performance_baseline(
            object(),
            tenant_id="tenant-1",
            actor_user_id="user-1",
            account_id="account-1",
            initial_cumulative_pnl=Decimal("100"),
            expected_version=1,
            idempotency_key="baseline-command-3",
        )


def test_performance_baseline_api_lists_and_appends_with_optimistic_version(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def transaction(_pool, _user_id, _tenant_id):
        yield object()

    async def list_rows(_conn, **kwargs):
        captured["list"] = kwargs
        return []

    async def revise(_conn, **kwargs):
        captured["revise"] = kwargs
        return {
            "accountId": kwargs["account_id"],
            "accountName": "天天基金账户",
            "baselineAsOf": "2026-08-01",
            "baselineTotalAsset": "100000.00",
            "initialCumulativePnl": "123.45",
            "source": "USER",
            "version": 2,
            "createdAt": "2026-08-30T12:00:00+08:00",
        }

    class Context(SimpleNamespace):
        def require_roles(self, *roles):
            captured["roles"] = roles

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    monkeypatch.setattr(api.service, "list_performance_baselines", list_rows)
    monkeypatch.setattr(api.service, "revise_performance_baseline", revise)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_pool] = lambda: object()
    app.dependency_overrides[api.get_tenant_context] = lambda: Context(
        user_id="user-1", tenant_id="tenant-1"
    )
    app.dependency_overrides[api.get_request_id] = lambda: "req-baseline"
    client = TestClient(app)

    assert client.get("/portfolio/performance-baselines").status_code == 200
    response = client.put(
        "/portfolio/performance-baselines/account-1",
        headers={"Idempotency-Key": "baseline-api-1", "If-Match": '"1"'},
        json={"initialCumulativePnl": "123.45"},
    )

    assert response.status_code == 200
    assert captured["roles"] == ("OWNER", "ADMIN", "EDITOR")
    assert captured["list"] == {
        "tenant_id": "tenant-1",
        "owner_user_id": "user-1",
    }
    assert captured["revise"] == {
        "tenant_id": "tenant-1",
        "actor_user_id": "user-1",
        "account_id": "account-1",
        "initial_cumulative_pnl": Decimal("123.45"),
        "expected_version": 1,
        "idempotency_key": "baseline-api-1",
    }
