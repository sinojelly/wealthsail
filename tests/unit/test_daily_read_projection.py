"""Latest failures keep frozen scans without transferring unused research inputs."""

import json
import os
from unittest.mock import AsyncMock

import pytest

from backend.macro import daily_repository as repository
from tests.unit.test_daily_repository import NOW, run

DB_REQUIRED = pytest.mark.skipif(
    not os.environ.get("INVESTMENT_TEST_DATABASE_URL"), reason="owned loopback DB required"
)
OBSERVATION_FIELDS = {
    "asOf", "quantitativeScan", "discoveryUniverse", "researchDiscovery",
    "previousIndustryRiskStates", "fundSubscriptionStatus",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [False, True])
async def test_latest_projection_is_explicit_and_full_ready_report_is_read_once(ready):
    old = run(
        run_id="ready", status="READY", recorded_at=NOW,
        public_packet={"asOf": NOW.isoformat()}, payload={"summary": "retained"},
        source_batch_id="source-batch", prompt_version="original-prompt",
    )
    latest = old if ready else run(status="FAILED", recorded_at=NOW)
    conn = AsyncMock()
    conn.fetchrow.side_effect = [latest] if ready else [latest, old]

    result = await repository.read_daily_research(conn, NOW)

    first_query = conn.fetchrow.await_args_list[0].args[0]
    assert "r.*" not in first_query
    assert "CASE WHEN t.status='READY'" in first_query
    assert result["reportRunId"] == "ready"
    assert result["sourceBatchId"] == "source-batch"
    assert result["promptVersion"] == "original-prompt"
    assert conn.fetchrow.await_count == (1 if ready else 2)
    if not ready:
        assert "CASE WHEN t.status='READY'" not in conn.fetchrow.await_args.args[0]
    conn.execute.assert_not_awaited()


@DB_REQUIRED
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "UNCERTAIN", "READY", None])
async def test_real_latest_sql_preserves_ready_material_or_complete_failed_observations(status):
    from backend.investment.fund_observation import build_fund_observation
    from tests.unit.test_fund_observation import case, NOW as INPUT_AT, RUN
    from tests.unit.test_research_acceptance_database import fixture

    packet = case(46)
    packet.update(
        fundHistories=[{"unusedSourceHistory": "x" * 1_000_000}],
        documents=[{"unusedText": "not needed for frozen fund observations"}],
        fundSubscriptionStatus=None,
    )
    payload = {"summary": "Complete original report", "valueDirections": []}
    async with fixture() as (conn, run_id, now, *_):
        await conn.execute(
            "UPDATE research.daily_research_runs SET public_packet=$2::jsonb WHERE run_id=$1",
            run_id, json.dumps(packet),
        )
        if status is not None:
            await repository.finish_daily_run(
                conn, run_id, now, status=status,
                payload=payload if status == "READY" else None,
                error_class=None if status == "READY" else "TimeoutError",
            )
        query = getattr(repository, "_READ_LATEST", repository._READ)
        row = await conn.fetchrow(query + " AND r.run_id=$3", now.date(), now, run_id)
        selected = json.loads(row["public_packet"])
        if status == "READY":
            assert selected == packet
            assert json.loads(row["payload"]) == payload
        else:
            assert selected == {key: value for key, value in packet.items() if key in OBSERVATION_FIELDS}
            assert "fundSubscriptionStatus" in selected and selected["fundSubscriptionStatus"] is None
            assert "researchDiscovery" not in selected
            assert len(json.dumps(selected).encode()) < len(json.dumps(packet).encode()) / 5
            assert build_fund_observation(
                selected, INPUT_AT, research_id=RUN, validated_at=INPUT_AT,
            ) == build_fund_observation(
                packet, INPUT_AT, research_id=RUN, validated_at=INPUT_AT,
            )


@DB_REQUIRED
@pytest.mark.asyncio
@pytest.mark.parametrize("packet", [{"researchDiscovery": None, "fundSubscriptionStatus": None}, [], "legacy", None])
async def test_latest_packet_sql_keeps_explicit_null_fields_and_handles_non_objects(packet):
    from tests.unit.test_research_acceptance_database import fixture

    expression = getattr(repository, "_LATEST_PACKET_SQL", "r.public_packet")
    async with fixture() as (conn, *_):
        result = json.loads(await conn.fetchval(
            f"""SELECT {expression} FROM (SELECT $1::jsonb AS public_packet) r
            CROSS JOIN (SELECT 'FAILED'::text AS status) t""",
            json.dumps(packet),
        ))
        assert result == (packet if isinstance(packet, dict) else {})
