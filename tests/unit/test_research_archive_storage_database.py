"""Capacity plans are read-only and require a complete matching archived copy."""

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from importlib import import_module
import json

import asyncpg
import pytest

from backend.macro import research_acceptance_repository as repo
from tests.unit.test_research_acceptance_database import DSN, fixture, outcome

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned acceptance DB required"),
]


def scanner():
    return import_module("scripts.research_archive_storage").scan_archive_storage


async def snapshot(conn, run_id):
    value = await conn.fetchval(
        """SELECT jsonb_build_object(
        'queue',(SELECT to_jsonb(q) FROM research.research_validation_archive_queue q WHERE run_id=$1),
        'artifacts',(SELECT to_jsonb(a) FROM research.research_validation_artifacts a WHERE run_id=$1),
        'records',(SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY id),'[]'::jsonb) FROM research.research_validation_records r WHERE run_id=$1)
        )::text""",
        run_id,
    )
    return sha256(value.encode("utf-8")).hexdigest()


async def prepare(conn, run_id, now, case):
    record = outcome(run_id, now)
    wall_clock = await conn.fetchval("SELECT clock_timestamp()")
    raw = None if case == "missing_body" else {"PRIVATE_RAW": "full original"}
    await repo.enqueue_research_archive(
        conn,
        run_id,
        {"PRIVATE_PACKET": "frozen"},
        raw,
        {"s1": {"PRIVATE_CHECK": "original source result"}},
        now,
        record,
        recorded_at=wall_clock,
    )
    if case in {"hash_mismatch", "size_mismatch", "omission_mismatch"}:
        bundle = json.loads(
            await conn.fetchval(
                "SELECT frozen_bundle FROM research.research_validation_archive_queue WHERE run_id=$1",
                run_id,
            )
        )
        values = deepcopy(
            [
                bundle["artifacts"][key]
                for key in ("packet", "raw_output", "source_checks")
            ]
        )
        key, value = {
            "hash_mismatch": ("sha256", "f" * 64),
            "size_mismatch": ("byteLength", 123456),
            "omission_mismatch": ("omissionReason", "SIZE_LIMIT"),
        }[case]
        values[1][key] = value
        await repo._save_frozen_artifacts(conn, run_id, now, values)
    if case == "outcome_mismatch":
        different = deepcopy(record)
        different["counts"] = {"PRIVATE_DIFFERENT_OUTCOME": True}
        await repo.save_validation_outcome(
            conn, run_id, different, recorded_at=wall_clock
        )
    if case != "pending":
        claim = await repo.claim_research_archive(conn, run_id=run_id)
        assert claim is not None
        if case == "unrecoverable":
            await repo.finish_research_archive(conn, claim, status="UNRECOVERABLE")
        elif case == "missing_materials":
            await repo.finish_research_archive(conn, claim, status="ARCHIVED")
        elif case != "archiving":
            await repo.persist_research_archive(conn, claim)
    if case != "recent":
        await conn.execute(
            "UPDATE research.research_validation_archive_queue SET updated_at=clock_timestamp()-interval '91 days' WHERE run_id=$1",
            run_id,
        )


@pytest.mark.parametrize(
    "case",
    [
        "complete",
        "pending",
        "archiving",
        "unrecoverable",
        "recent",
        "missing_materials",
        "missing_body",
        "hash_mismatch",
        "size_mismatch",
        "omission_mismatch",
        "outcome_mismatch",
    ],
)
async def test_storage_plan_only_contains_aged_archived_complete_matching_copies(case):
    async with fixture() as (conn, run_id, now, *_):
        await prepare(conn, run_id, now, case)
        before = await snapshot(conn, run_id)
        result = await scanner()(conn, retention_days=90, candidate_limit=20)
        assert await snapshot(conn, run_id) == before
        assert "PRIVATE" not in json.dumps(result)
        assert result["planOnly"] is True
        candidates = {row["runId"]: row for row in result["redundancyPlan"]}
        assert (str(run_id) in candidates) is (case == "complete")
        assert len(candidates) <= 20
        if case == "complete":
            stamp = datetime.fromisoformat(
                candidates[str(run_id)]["archivedAt"].replace("Z", "+00:00")
            )
            assert stamp.tzinfo is not None
        assert set(result["tables"]) == {
            "research.research_validation_archive_queue",
            "research.research_validation_artifacts",
            "research.research_validation_records",
            "research.research_validation_replay_attempts",
        }
        assert all(
            type(size) is int and size >= 0 for size in result["tables"].values()
        )
        assert isinstance(result["oldestPendingSeconds"], (float, int))
        assert result["oldestPendingSeconds"] >= 0
        if case in {"pending", "archiving", "unrecoverable"}:
            status = {
                "pending": "AUDIT_PENDING",
                "archiving": "ARCHIVING",
                "unrecoverable": "UNRECOVERABLE",
            }[case]
            assert result["queue"][status] >= 1


@pytest.mark.parametrize("sample_limit", [2, 20])
async def test_storage_plan_respects_candidate_sample_limit_without_writing(
    sample_limit,
):
    async with fixture() as (conn, run_id, now, *_):
        run_ids = [run_id]
        for offset in range(1, sample_limit + 1):
            stamp = now + timedelta(days=offset)
            run_ids.append(
                await conn.fetchval(
                    "INSERT INTO research.daily_research_runs(slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet) VALUES($1,$2,$2,'storage-capacity-fixture','storage-capacity-fixture','{}') RETURNING run_id",
                    stamp.date(),
                    stamp,
                )
            )
        for offset, identity in enumerate(run_ids):
            await prepare(conn, identity, now + timedelta(days=offset), "complete")
        before = [await snapshot(conn, identity) for identity in run_ids]
        result = await scanner()(conn, retention_days=90, candidate_limit=sample_limit)
        assert len(result["redundancyPlan"]) == sample_limit
        assert {row["runId"] for row in result["redundancyPlan"]}.issubset(
            {str(identity) for identity in run_ids}
        )
        assert [await snapshot(conn, identity) for identity in run_ids] == before


async def test_storage_scanner_runs_in_a_real_readonly_transaction():
    async with fixture():
        conn = await asyncpg.connect(DSN)
        try:
            async with conn.transaction(readonly=True):
                assert await conn.fetchval("SHOW transaction_read_only") == "on"
                result = await scanner()(conn)
                assert "PRIVATE" not in json.dumps(result)
        finally:
            await conn.close()
