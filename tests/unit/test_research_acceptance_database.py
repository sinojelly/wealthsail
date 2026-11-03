"""Real PostgreSQL checks for immutable evidence and tenant-bound offline replay."""

from contextlib import asynccontextmanager
from datetime import timedelta
from importlib import import_module
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.macro import research_acceptance_repository as repo
from tests.unit.test_daily_failure_diagnostics_database import stamp
from tests.unit.test_manual_research_database import make_run

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned acceptance DB required"),
]


@asynccontextmanager
async def fixture(*, manual=False):
    target = urlsplit(DSN)
    assert target.hostname in {"localhost", "127.0.0.1", "::1"}
    assert target.path.startswith("/wealthsail_manual_research_acceptance")
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    now = stamp()
    tenants, actors, viewer = [uuid4(), uuid4()], [uuid4(), uuid4()], uuid4()
    try:
        for tenant, actor in zip(tenants, actors):
            await conn.execute(
                "INSERT INTO identity.tenants(id,name) VALUES($1,'acceptance fixture')",
                tenant,
            )
            await conn.execute(
                "INSERT INTO identity.profiles(id,nickname) VALUES($1,'acceptance owner')",
                actor,
            )
            await conn.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
                tenant,
                actor,
            )
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'acceptance viewer')",
            viewer,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'VIEWER')",
            tenants[0],
            viewer,
        )
        if manual:
            _, _, run_id = await make_run(conn, tenants[0], actors[0], now=now)
        else:
            run_id = await conn.fetchval(
                "INSERT INTO research.daily_research_runs(slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet) VALUES($1,$2,$2,'acceptance','acceptance-fixture','{}') RETURNING run_id",
                now.date(),
                now,
            )
        yield conn, run_id, now, tenants, actors, viewer
    finally:
        await tx.rollback()
        await conn.close()


def outcome(run_id, now):
    return {
        "researchId": str(run_id),
        "validatorVersion": "research-acceptance-v1",
        "validatedAt": now.isoformat(),
        "decision": "PARTIAL",
        "formalPublishable": False,
        "actionable": False,
        "issueListComplete": True,
        "counts": {
            "sources": {"inputCount": 1, "acceptedCount": 1, "quarantinedCount": 0}
        },
        "issues": [],
        "accepted": {
            "sources": [
                {
                    "id": "s1",
                    "title": "公开来源",
                    "url": "https://example.com/report",
                    "quote": "来源摘要",
                }
            ],
            "valueDirections": [],
            "industryCycleReviews": [],
            "events": [],
            "upcoming": [],
        },
    }


async def initial(conn, run_id, now):
    await repo.save_research_artifacts(
        conn,
        run_id,
        {"asOf": now.isoformat(), "PRIVATE_PACKET": True},
        {"PRIVATE_RAW": "original"},
        {"s1": {"verification": "SOURCE_CHECKED"}},
        now,
    )
    return await repo.save_validation_outcome(
        conn, run_id, outcome(run_id, now), recorded_at=now
    )


async def test_099_tables_force_rls_and_raw_outcome_are_not_api_selectable():
    async with fixture() as (conn, *_):
        rows = await conn.fetch(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN(to_regclass('research.research_validation_artifacts'),to_regclass('research.research_validation_records'))"
        )
        assert len(rows) == 2 and all(
            row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api','research.research_validation_artifacts','SELECT')"
        )
        assert not await conn.fetchval(
            "SELECT has_column_privilege('wealthsail_api','research.research_validation_records','outcome','SELECT')"
        )
        for table in (
            "research.research_validation_artifacts",
            "research.research_validation_records",
        ):
            for role in ("wealthsail_api", "wealthsail_worker"):
                for privilege in ("UPDATE", "DELETE"):
                    assert not await conn.fetchval(
                        "SELECT has_table_privilege($1,$2,$3)", role, table, privilege
                    )


async def test_manual_artifacts_and_summary_stay_with_original_tenant_at_database_boundary():
    async with fixture(manual=True) as (conn, run_id, now, tenants, actors, viewer):
        record = await initial(conn, run_id, now)
        assert (
            await conn.fetchval(
                "SELECT tenant_id FROM research.research_validation_artifacts WHERE run_id=$1",
                run_id,
            )
            == tenants[0]
        )
        await apply_tenant_context(conn, str(viewer), str(tenants[0]))
        result = await repo.read_validation_summary(conn, run_id, now)
        assert result["accepted"]["sources"][0]["title"] == "公开来源"
        assert "PRIVATE" not in str(result)
        for sql in (
            "SELECT packet FROM research.research_validation_artifacts",
            "SELECT outcome FROM research.research_validation_records",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.fetch(sql)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchval(
                    "SELECT research.read_validation_artifacts_admin($1,true)", run_id
                )
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        raw = await repo.read_artifacts_admin(conn, run_id)
        assert raw["complete"] is True and "PRIVATE_RAW" in raw["items"][1]["content"]
        admin = await repo.read_validation_summary(conn, run_id, now, admin=True)
        assert admin["canReplay"] is True and admin["id"] == record["id"]
        await apply_tenant_context(conn, str(actors[1]), str(tenants[1]))
        assert await repo.read_validation_summary(conn, run_id, now) is None
        assert (
            await conn.fetchval(
                "SELECT research.read_validation_artifacts_admin($1,true)", run_id
            )
            is None
        )


async def test_scheduled_initial_is_shared_but_replays_are_tenant_private_and_idempotent(
    monkeypatch,
):
    async with fixture() as (conn, run_id, now, tenants, actors, viewer):
        first = await initial(conn, run_id, now)
        calls = []

        def validate(raw, packet, checks, *, research_id, completed_at):
            calls.append((raw, packet, checks, research_id, completed_at))
            return SimpleNamespace(to_dict=lambda: outcome(run_id, now))

        module = import_module("backend.macro.research_acceptance")
        monkeypatch.setattr(module, "validate_report_outcome", validate)
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        key = uuid4()
        replay = await repo.replay_validation(
            conn, run_id, actors[0], tenants[0], key, now + timedelta(days=1)
        )
        same = await repo.replay_validation(
            conn, run_id, actors[0], tenants[0], key, now + timedelta(days=2)
        )
        assert replay["id"] == same["id"] != first["id"] and len(calls) == 1
        assert calls[0][4] == now, "replay must use original validation time"
        again = await repo.replay_validation(
            conn, run_id, actors[0], tenants[0], uuid4(), now + timedelta(days=2)
        )
        assert again["id"] != replay["id"] and len(calls) == 2
        assert (
            len(
                await repo.list_validation_replays(
                    conn, run_id, now + timedelta(days=3)
                )
            )
            == 2
        )
        await apply_tenant_context(conn, str(actors[1]), str(tenants[1]))
        assert (await repo.read_validation_summary(conn, run_id, now))["id"] == first[
            "id"
        ]
        assert (await repo.read_artifacts_admin(conn, run_id))["complete"] is True
        assert (
            await repo.list_validation_replays(conn, run_id, now + timedelta(days=3))
            == []
        )
        assert (
            await conn.fetchval(
                "SELECT research.read_validation_outcome_admin($1)", UUID(replay["id"])
            )
            is None
        )


async def test_worker_initial_insert_is_immutable_and_cannot_mutate_formal_report():
    async with fixture() as (conn, run_id, now, *_):
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        first = await initial(conn, run_id, now)
        second = await repo.save_validation_outcome(
            conn, run_id, outcome(run_id, now), recorded_at=now + timedelta(seconds=1)
        )
        assert first["id"] == second["id"]
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.daily_research_results WHERE run_id=$1",
                run_id,
            )
            == 0
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE research.research_validation_artifacts SET packet='{}' WHERE run_id=$1",
                    run_id,
                )


async def test_revoked_membership_blocks_raw_even_when_api_role_is_unchanged():
    async with fixture() as (conn, run_id, now, tenants, actors, *_):
        await initial(conn, run_id, now)
        await conn.execute(
            "UPDATE identity.tenant_members SET status='SUSPENDED' WHERE tenant_id=$1 AND user_id=$2",
            tenants[0],
            actors[0],
        )
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        assert await repo.read_validation_summary(conn, run_id, now) is None
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchval(
                    "SELECT research.read_validation_artifacts_admin($1,true)", run_id
                )


async def test_runner_readback_rejects_accidental_direct_raw_api_grant():
    from scripts.migrate import _readback

    async with fixture() as (conn, *_):
        await conn.execute(
            "GRANT SELECT ON research.research_validation_artifacts TO wealthsail_api"
        )
        with pytest.raises(RuntimeError, match="acceptance"):
            await _readback(conn)


async def test_real_eight_direction_two_issue_replay_keeps_old_result_and_original_checks(
    monkeypatch,
):
    from tests.unit.test_research_acceptance import eight_directions
    from tests.unit.test_daily_research import NOW as VALIDATED_AT
    from backend.macro.research_acceptance import validate_report_outcome

    async with fixture(manual=True) as (conn, _unused, _now, tenants, actors, _viewer):
        _, _, run_id = await make_run(conn, tenants[0], actors[0], now=VALIDATED_AT)
        packet, raw, checks = eight_directions()
        raw["valueDirections"][0]["macroCodes"] = ["UNKNOWN_MACRO"]
        raw["valueDirections"][1]["reviewIndustryIds"] = ["MODEL_UNKNOWN"]
        result = validate_report_outcome(
            raw, packet, checks, research_id=str(run_id), completed_at=VALIDATED_AT
        )
        assert result.to_dict()["counts"]["valueDirections"]["acceptedCount"] == 6
        await repo.save_research_artifacts(
            conn, run_id, packet, raw, checks, VALIDATED_AT
        )
        initial_record = await repo.save_validation_outcome(
            conn, run_id, result.to_dict(), recorded_at=VALIDATED_AT
        )
        await conn.execute(
            "INSERT INTO research.daily_research_results(run_id,status,recorded_at,error_class) VALUES($1,'FAILED',$2,'ResearchValidationError')",
            run_id,
            VALIDATED_AT,
        )

        def forbidden(*args, **kwargs):
            raise AssertionError("offline replay must not fetch or run a model")

        monkeypatch.setattr(
            "backend.integrations.codex_research.run_codex_research", forbidden
        )
        monkeypatch.setattr(
            "backend.integrations.daily_sources.verify_research_sources", forbidden
        )
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        replay = await repo.replay_validation(
            conn,
            run_id,
            actors[0],
            tenants[0],
            uuid4(),
            VALIDATED_AT + timedelta(days=100),
        )
        assert replay["counts"]["valueDirections"] == {
            "inputCount": 8,
            "acceptedCount": 6,
            "quarantinedCount": 2,
        }
        assert replay["validatedAt"] == VALIDATED_AT.isoformat()
        assert replay["actionable"] is False and replay["formalPublishable"] is False
        old = await conn.fetchrow(
            "SELECT status,payload FROM research.daily_research_results WHERE run_id=$1",
            run_id,
        )
        assert dict(old) == {"status": "FAILED", "payload": None}
        assert (await repo.read_validation_summary(conn, run_id, VALIDATED_AT))[
            "id"
        ] == initial_record["id"]


async def test_replay_rejects_a_corrupt_inserted_digest_without_a_new_record(monkeypatch):
    import json
    from backend.errors import ValidationError_

    async with fixture() as (conn, run_id, now, tenants, actors, _viewer):
        packet = repo.freeze_artifact({}, repo.PACKET_LIMIT)
        raw = repo.freeze_artifact("{}", repo.RAW_LIMIT, raw=True)
        checks = repo.freeze_artifact({}, repo.CHECKS_LIMIT)
        raw["sha256"] = "0" * 64
        await conn.execute(
            "INSERT INTO research.research_validation_artifacts(run_id,validated_at,packet,raw_output,source_checks) VALUES($1,$2,$3::jsonb,$4::jsonb,$5::jsonb)",
            run_id, now, json.dumps(packet), json.dumps(raw), json.dumps(checks),
        )
        def forbidden(*args, **kwargs):
            raise AssertionError("corrupt artifacts must not reach the validator")

        monkeypatch.setattr("backend.macro.research_acceptance.validate_report_outcome", forbidden)
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        with pytest.raises(ValidationError_, match="校验值"):
            await repo.replay_validation(conn, run_id, actors[0], tenants[0], uuid4(), now)
        assert await conn.fetch("SELECT id FROM research.research_validation_records WHERE run_id=$1", run_id) == []
