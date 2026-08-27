"""modules/research DecisionService（reporting.decisions）单元测试。"""

from __future__ import annotations

from typing import Any

import pytest

from modules.research.service import DecisionService


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.fetchval_results: list[Any] = []
        self.fetchrow_results: list[Any] = []
        self.fetch_results: list[list[Any]] = []

    async def fetchval(self, query, *args) -> Any:
        self.calls.append((query, args))
        if not self.fetchval_results:
            return None
        return self.fetchval_results.pop(0)

    async def fetchrow(self, query, *args) -> Any:
        self.calls.append((query, args))
        if not self.fetchrow_results:
            return None
        return self.fetchrow_results.pop(0)

    async def fetch(self, query, *args) -> Any:
        self.calls.append((query, args))
        if not self.fetch_results:
            return []
        return self.fetch_results.pop(0)

    async def execute(self, query, *args) -> Any:
        self.calls.append((query, args))
        return "UPDATE 1"

    async def executemany(self, query, args) -> Any:
        self.calls.append((query, tuple(args)))
        return "OK"

    def transaction(self):
        class _Tx:
            def __init__(self, conn):
                self._conn = conn

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, *_args):
                return None

        return _Tx(self)


@pytest.mark.asyncio
async def test_record_decision_inserts_normalized_action() -> None:
    conn = FakeConn()
    conn.fetchval_results = ["dec-1"]
    service = DecisionService()

    result = await service.record_decision(
        conn,
        tenant_id="tenant-1",
        user_id="user-1",
        decision_action=" accepted ",
        idempotency_key="decision-1",
        recommendation_id="rec-1",
        reason="基本面改善",
    )

    assert result == {"decisionId": "dec-1", "replayed": False}
    insert = next(c for c in conn.calls if "INSERT INTO reporting.decisions" in c[0])
    assert insert[1][4] == "ACCEPTED"  # 归一化大写
    assert insert[1][7] == "decision-1"  # idempotency_key


@pytest.mark.asyncio
async def test_record_decision_replays_duplicate_key() -> None:
    conn = FakeConn()
    conn.fetchval_results = [None]  # ON CONFLICT DO NOTHING
    conn.fetchrow_results = [
        {"id": "dec-old", "decision_action": "DEFERRED", "status": "RECORDED"}
    ]
    service = DecisionService()

    result = await service.record_decision(
        conn,
        tenant_id="tenant-1",
        user_id="user-1",
        decision_action="DEFERRED",
        idempotency_key="decision-1",
    )

    assert result["decisionId"] == "dec-old"
    assert result["replayed"] is True
    assert result["decisionAction"] == "DEFERRED"


@pytest.mark.asyncio
async def test_record_decision_validates_action() -> None:
    service = DecisionService()
    conn = FakeConn()
    with pytest.raises(ValueError):
        await service.record_decision(
            conn, tenant_id="t", user_id="u", decision_action="MAYBE",
            idempotency_key="k",
        )
    with pytest.raises(ValueError):
        await service.record_decision(
            conn, tenant_id="t", user_id="u", decision_action="ACCEPTED",
            idempotency_key="",
        )


@pytest.mark.asyncio
async def test_retract_decision_soft_deletes() -> None:
    conn = FakeConn()
    conn.fetchrow_results = [{"id": "dec-1", "status": "RETRACTED"}]
    service = DecisionService()

    result = await service.retract_decision(
        conn, tenant_id="tenant-1", decision_id="dec-1", reason="误操作"
    )

    assert result == {"decisionId": "dec-1", "status": "RETRACTED"}
    update = next(c for c in conn.calls if "UPDATE reporting.decisions" in c[0])
    assert update[1][1] == "dec-1"

    # 非 RECORDED 或不存在 → 幂等 no-op
    conn2 = FakeConn()
    noop = await service.retract_decision(conn2, tenant_id="t", decision_id="x")
    assert noop["status"] == "NOT_FOUND_OR_NOT_RECORDED"


@pytest.mark.asyncio
async def test_list_decisions_joins_recommendation() -> None:
    conn = FakeConn()
    conn.fetch_results = [
        [
            {
                "id": "dec-1",
                "decision_action": "ACCEPTED",
                "status": "RECORDED",
                "reason": "采纳",
                "correction": None,
                "decided_at": None,
                "report_id": None,
                "recommendation_id": "rec-1",
                "recommendation_summary": "建议加仓",
                "instrument_name": "贵州茅台",
                "ticker": "600519",
            }
        ]
    ]
    service = DecisionService()
    result = await service.list_decisions(conn, "tenant-1")
    item = result["items"][0]
    assert item["decisionAction"] == "ACCEPTED"
    assert item["instrument"] == "600519 贵州茅台"
    assert item["recommendationId"] == "rec-1"
    assert result["total"] == 1
