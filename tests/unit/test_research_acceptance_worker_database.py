"""Committed owned rows prove evidence survives a rolled-back terminal write."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import asyncpg
import pytest

from backend.macro import daily_repository
from backend.macro import research_acceptance_repository as acceptance
from backend.macro.research_acceptance import ValidationOutcome
from tests.unit.test_daily_research_job import isolate, settings
from tests.unit.test_manual_research_database import NOW, fixture, make_run
from tests.unit.test_research_acceptance_database import DSN

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned acceptance DB required"),
]


@pytest.mark.parametrize("fail_outcome", [False, True])
async def test_worker_commits_evidence_before_atomic_result_and_acceptance(
    monkeypatch, fail_outcome
):
    target = urlsplit(DSN)
    assert target.hostname in {"localhost", "127.0.0.1", "::1"}
    assert target.path.startswith("/wealthsail_manual_research_acceptance")
    async with fixture(committed=True) as (conn, tenant, actor, _other):
        request_id, batch_id, run_id = await make_run(conn, tenant, actor)
        mod, _state = isolate(monkeypatch)

        @asynccontextmanager
        async def connection(_pool):
            owned = await asyncpg.connect(DSN)
            try:
                yield owned
            finally:
                await owned.close()

        async def outcome_write(*args, **kwargs):
            value = await acceptance.save_validation_outcome(*args, **kwargs)
            if fail_outcome:
                raise ConnectionError("fixture terminal transaction rollback")
            return value

        outcome = ValidationOutcome(
            str(run_id),
            NOW,
            "COMPLETE",
            (),
            {},
            {},
            formal_payload={"summary": "formal", "sources": []},
        )
        from backend.macro import alpha_repository

        monkeypatch.setattr(mod, "system_connection", connection)
        monkeypatch.setattr(mod, "now_shanghai", lambda: NOW)
        monkeypatch.setattr(
            mod, "claim_daily_run", AsyncMock(return_value={"run_id": run_id})
        )
        monkeypatch.setattr(mod, "finish_daily_run", daily_repository.finish_daily_run)
        monkeypatch.setattr(
            mod, "save_research_artifacts", acceptance.save_research_artifacts
        )
        monkeypatch.setattr(mod, "save_validation_outcome", outcome_write)
        monkeypatch.setattr(mod, "validate_report_outcome", lambda *_, **__: outcome)
        monkeypatch.setattr(
            alpha_repository,
            "read_alpha_public",
            AsyncMock(return_value={"batchId": str(batch_id)}),
        )
        arguments = {
            "manual_request_id": request_id,
            "source_batch_id": batch_id,
            "packet": {"asOf": NOW.isoformat()},
        }
        if fail_outcome:
            with pytest.raises(ConnectionError):
                await mod._execute_daily_research(
                    object(),
                    settings(daily_research_manual_enabled=True),
                    NOW,
                    **arguments,
                )
        else:
            result = await mod._execute_daily_research(
                object(),
                settings(daily_research_manual_enabled=True),
                NOW,
                **arguments,
            )
            assert result["status"] == "READY"
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.research_validation_artifacts WHERE run_id=$1",
                run_id,
            )
            == 1
        )
        for table in ("daily_research_results", "research_validation_records"):
            assert await conn.fetchval(
                f"SELECT count(*) FROM research.{table} WHERE run_id=$1",
                run_id,
            ) == (0 if fail_outcome else 1)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.daily_research_runs WHERE run_id=$1",
                run_id,
            )
            == 1
        )
        mod.run_codex_research.assert_awaited_once()
        mod.release_model_guard.assert_awaited_once()
