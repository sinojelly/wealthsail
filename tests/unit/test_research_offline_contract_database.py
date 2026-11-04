"""Disposable PostgreSQL keeps old attempt constraints and same-key accounting."""

import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.common.database import apply_tenant_context
from backend.errors import ValidationError_
from backend.macro import research_acceptance_repository as acceptance
from backend.macro import research_history_repair_repository as history
from tests.unit.test_research_acceptance_database import outcome
from tests.unit.test_research_frozen_contract_projection import frozen
from tests.unit.test_research_history_repair_database import fixture

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
        reason="owned loopback acceptance database required",
    ),
]


@pytest.mark.parametrize("operation", ["PREVIEW", "GENERATE", "REPLAY"])
@pytest.mark.parametrize("damaged", [False, True])
async def test_contract_failure_and_retry_keep_existing_ledger_constraints_and_source(
    monkeypatch, operation, damaged
):
    child = AsyncMock()
    monkeypatch.setattr(
        "backend.macro.research_acceptance_replay.run_offline_repair", child
    )
    monkeypatch.setattr(
        "backend.macro.research_acceptance_replay.run_offline_validation", child
    )
    async with fixture() as (conn, run_id, now, tenants, actors, _):
        for table in (
            "research_validation_archive_queue",
            "research_validation_records",
            "research_validation_artifacts",
        ):
            await conn.execute(f"DELETE FROM research.{table} WHERE run_id=$1", run_id)
        packet = frozen() if damaged else {
            "asOf": now.isoformat(),
            "researchDiscovery": {"version": "PRIVATE_UNSUPPORTED_VERSION"},
        }
        if damaged:
            packet["researchDiscovery"]["auditManifest"]["manifestHash"] = "0" * 64
        await acceptance.enqueue_research_archive(
            conn, run_id, packet, {}, {}, now, outcome(run_id, now)
        )
        claim = await acceptance.claim_research_archive(conn, run_id=run_id)
        await acceptance.persist_research_archive(conn, claim)
        original = await conn.fetchval(
            "SELECT outcome FROM research.research_validation_records WHERE run_id=$1",
            run_id,
        )
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        message = "哈希" if damaged and operation != "REPLAY" else "校验值" if damaged else "该冻结契约版本暂不受支持"
        if operation == "PREVIEW":
            with pytest.raises(ValidationError_, match=message):
                await history.preview_history_repair(
                    conn, run_id, actors[0], tenants[0], now
                )
        else:
            key = uuid4()
            function = (
                history.generate_history_repair
                if operation == "GENERATE"
                else acceptance.replay_validation
            )
            for _ in range(2):
                failure = await function(conn, run_id, actors[0], tenants[0], key, now)
                assert isinstance(failure.error, ValidationError_)
                assert message in str(failure.error)
            table, column = (
                ("research_history_repair_attempts", "source_run_id")
                if operation == "GENERATE"
                else ("research_validation_replay_attempts", "run_id")
            )
            await conn.execute("RESET ROLE")
            attempts = await conn.fetch(
                f"SELECT status,error_code FROM research.{table} WHERE {column}=$1", run_id
            )
            assert len(attempts) == 1
            assert dict(attempts[0]) == {
                "status": "FAILED",
                "error_code": "MATERIALS_INVALID" if damaged else "VALIDATOR_FAILED",
            }
        child.assert_not_awaited()
        await conn.execute("RESET ROLE")
        assert await conn.fetchval(
            "SELECT status FROM research.daily_research_results WHERE run_id=$1", run_id
        ) == "FAILED"
        assert await conn.fetchval(
            "SELECT outcome FROM research.research_validation_records WHERE run_id=$1", run_id
        ) == original
