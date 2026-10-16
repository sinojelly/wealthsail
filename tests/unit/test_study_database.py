"""Study lifecycle and privacy using real isolated PostgreSQL runtime roles."""

import asyncio
from datetime import datetime, timedelta
import json
import os
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.common.database import create_pool, tenant_transaction
from backend.errors import NotFoundError, VersionConflictError
from backend.investment import study_repository as repo, study_service as service
from backend.investment.study_schemas import (
    CreateStudyRequest,
    VersionRequest,
    StatusRequest,
    NoteRequest,
    TradeLinkRequest,
)

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated study database required"),
]
TZ = ZoneInfo("Asia/Shanghai")


async def test_real_concurrent_create_version_and_check_are_serialized(monkeypatch):
    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, _, run, now = await seed(pool)
        facts = [fact(now)]

        async def existing(*args):
            return facts

        monkeypatch.setattr(service, "load_observations", existing)

        async def create_once():
            async with tenant_transaction(pool, actor, tenant) as conn:
                return await create(conn, tenant, actor, run, now)

        created = await asyncio.gather(create_once(), create_once())
        assert created[0]["track"]["id"] == created[1]["track"]["id"]
        track = UUID(created[0]["track"]["id"])
        async with tenant_transaction(pool, actor, tenant) as conn:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM research.study_versions WHERE track_id=$1",
                    track,
                )
                == 1
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM research.study_checks WHERE track_id=$1",
                    track,
                )
                == 1
            )

        async def revise(reason):
            async with tenant_transaction(pool, actor, tenant) as conn:
                return await service.append_version(
                    conn,
                    tenant,
                    actor,
                    track,
                    VersionRequest.model_validate(
                        {
                            "expectedGeneration": 1,
                            "reason": reason,
                            "conditions": [
                                {"metric": "PMI", "operator": "GT", "threshold": "50"}
                            ],
                        }
                    ),
                    now + timedelta(minutes=1),
                )

        revised = await asyncio.gather(
            revise("并发修订甲"), revise("并发修订乙"), return_exceptions=True
        )
        assert sum(isinstance(result, dict) for result in revised) == 1
        assert sum(isinstance(result, VersionConflictError) for result in revised) == 1
        async with tenant_transaction(pool, actor, tenant) as conn:
            assert (
                await conn.fetchval(
                    "SELECT generation FROM research.study_tracks WHERE id=$1", track
                )
                == 2
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM research.study_versions WHERE track_id=$1",
                    track,
                )
                == 2
            )

        facts[:] = [fact(now, "51")]

        async def check_once():
            async with tenant_transaction(pool, actor, tenant) as conn:
                return await service.check_study(
                    conn, tenant, actor, track, 2, now + timedelta(minutes=2)
                )

        checked = await asyncio.gather(check_once(), check_once())
        assert sum(result["stored"] for result in checked) == 1
        async with tenant_transaction(pool, actor, tenant) as conn:
            rows = await conn.fetch(
                "SELECT result FROM research.study_checks WHERE track_id=$1 AND generation=2",
                track,
            )
            assert len(rows) == 2  # One baseline, one actual transition.
            assert (
                sum(
                    repo.decode(row["result"]).get("meaningfulChange", False)
                    for row in rows
                )
                == 1
            )
    finally:
        await pool.close()


async def test_real_review_binds_explicit_old_version_and_rejects_other_tracks(
    monkeypatch,
):
    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, _, run, now = await seed(pool)

        async def empty(*args):
            return []

        monkeypatch.setattr(service, "load_observations", empty)
        async with tenant_transaction(pool, actor, tenant) as conn:
            original = await create(conn, tenant, actor, run, now)
            track = UUID(original["track"]["id"])
            revised = await service.append_version(
                conn,
                tenant,
                actor,
                track,
                VersionRequest.model_validate(
                    {
                        "expectedGeneration": 1,
                        "reason": "假设修订",
                        "thesis": "新版本假设",
                    }
                ),
                now + timedelta(minutes=1),
            )
            reviewed = await service.append_note(
                conn,
                tenant,
                actor,
                track,
                {
                    "expectedGeneration": 2,
                    "versionId": original["version"]["id"],
                    "kind": "REVIEW",
                    "verdict": "REFUTED",
                    "reason": "复盘原始假设",
                },
                now + timedelta(minutes=2),
            )
            note = next(row for row in reviewed["notes"] if row["kind"] == "REVIEW")
            assert note["versionId"] == original["version"]["id"]
            assert note["versionId"] != revised["version"]["id"]
            other = await service.create_study(
                conn,
                tenant,
                actor,
                CreateStudyRequest.model_validate(
                    {
                        "originType": "DAILY_EVENT",
                        "reportRunId": str(run),
                        "eventId": "event:original",
                        "scope": {"indexId": "hs300"},
                    }
                ),
                now + timedelta(minutes=3),
            )
            with pytest.raises(NotFoundError):
                await service.append_note(
                    conn,
                    tenant,
                    actor,
                    track,
                    {
                        "expectedGeneration": 2,
                        "versionId": other["version"]["id"],
                        "kind": "DECISION",
                        "action": "IGNORE",
                        "reason": "不能串主题",
                    },
                    now + timedelta(minutes=4),
                )
    finally:
        await pool.close()


async def seed(pool):
    tenant, actor, other, run = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime(2200, 1, 1, 10, tzinfo=TZ) + timedelta(days=uuid4().int % 200000)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'isolated study')", tenant
        )
        for user in (actor, other):
            await conn.execute(
                "INSERT INTO identity.profiles(id,nickname) VALUES($1,'study actor')",
                user,
            )
            await conn.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
                tenant,
                user,
            )
        await conn.execute(
            """INSERT INTO research.daily_research_runs(run_id,slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet)
            VALUES($1,$2,$3,$3,'study-fixture','study-fixture','{}')""",
            run,
            now.date(),
            now,
        )
        payload = {
            "asOf": now.isoformat(),
            "events": [
                {
                    "id": "event:original",
                    "title": "原始观察",
                    "summary": "原始假设",
                    "phase": "ONGOING",
                    "sourceIds": ["source:original"],
                }
            ],
            "sources": [
                {
                    "id": "source:original",
                    "url": "https://www.eia.gov/study-fixture",
                    "verification": "SOURCE_CHECKED",
                }
            ],
        }
        await conn.execute(
            "INSERT INTO research.daily_research_results(run_id,status,recorded_at,payload) VALUES($1,'READY',$2,$3::jsonb)",
            run,
            now,
            json.dumps(payload),
        )
    return tenant, actor, other, run, now


def fact(now, value="49"):
    return {
        "metric": "PMI",
        "value": value,
        "unit": "指数点",
        "asOf": (now.date() - timedelta(days=1)).isoformat(),
        "retrievedAt": now.isoformat(),
        "sourceUri": "https://www.stats.gov.cn/study-fixture",
        "verification": "VERIFIED",
        "state": "READY",
    }


async def create(conn, tenant, actor, run, now):
    request = CreateStudyRequest.model_validate(
        {
            "originType": "DAILY_EVENT",
            "reportRunId": str(run),
            "eventId": "event:original",
        }
    )
    return await service.create_study(conn, tenant, actor, request, now)


async def test_real_actor_lifecycle_dedupe_known_changes_and_ack(monkeypatch):
    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, other, run, now = await seed(pool)
        facts = [fact(now)]

        async def existing_only(*args):
            return facts

        monkeypatch.setattr(service, "load_observations", existing_only)
        async with tenant_transaction(pool, actor, tenant) as conn:
            detail = await create(conn, tenant, actor, run, now)
            track = UUID(detail["track"]["id"])
            assert detail["track"]["unreadCount"] == 0
            assert (await create(conn, tenant, actor, run, now))["track"]["id"] == str(
                track
            )
            assert detail["version"]["snapshot"]["origin"]["reportRunId"] == str(run)
            assert detail["version"]["snapshot"]["thesis"] == "原始假设"
            revised = await service.append_version(
                conn,
                tenant,
                actor,
                track,
                VersionRequest.model_validate(
                    {
                        "expectedGeneration": 1,
                        "reason": "设置明确阈值",
                        "thesis": "修订假设",
                        "conditions": [
                            {
                                "metric": "PMI",
                                "operator": "GT",
                                "threshold": "50",
                                "effect": "TRIGGER",
                            }
                        ],
                    }
                ),
                now + timedelta(minutes=1),
            )
            assert revised["track"]["generation"] == 2
            assert revised["version"]["snapshot"]["thesis"] == "修订假设"
            assert (
                len(revised["versions"]) == 2 and revised["track"]["unreadCount"] == 0
            )
            before_count = await conn.fetchval(
                "SELECT count(*) FROM research.study_checks WHERE track_id=$1", track
            )
            for _ in range(3):
                assert not (
                    await service.check_study(
                        conn, tenant, actor, track, 2, now + timedelta(minutes=2)
                    )
                )["stored"]
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM research.study_checks WHERE track_id=$1",
                    track,
                )
                == before_count
            )
            facts[:] = [fact(now, "51")]
            assert (
                await service.check_study(
                    conn, tenant, actor, track, 2, now + timedelta(minutes=3)
                )
            )["result"]["meaningfulChange"]
            assert (
                await service.get_detail(
                    conn, tenant, actor, track, now + timedelta(minutes=4)
                )
            )["track"]["unreadCount"] == 1
            ack = await service.append_note(
                conn,
                tenant,
                actor,
                track,
                NoteRequest.model_validate({"expectedGeneration": 2, "kind": "ACK"}),
                now + timedelta(minutes=5),
            )
            assert ack["track"]["unreadCount"] == 0
            stopped = await service.set_status(
                conn,
                tenant,
                actor,
                track,
                StatusRequest.model_validate(
                    {"expectedGeneration": 2, "enabled": False}
                ),
                now + timedelta(minutes=6),
            )
            assert (
                stopped["track"]["generation"] == 3
                and stopped["track"]["status"] == "ENDED"
            )
            with pytest.raises(VersionConflictError):
                await service.check_study(
                    conn, tenant, actor, track, 2, now + timedelta(minutes=7)
                )
            resumed = await service.set_status(
                conn,
                tenant,
                actor,
                track,
                StatusRequest.model_validate(
                    {"expectedGeneration": 3, "enabled": True}
                ),
                now + timedelta(minutes=8),
            )
            assert resumed["track"]["generation"] == 4
            assert (
                await service.check_study(
                    conn, tenant, actor, track, 4, now + timedelta(minutes=9)
                )
            )["result"]["baseline"]
        async with tenant_transaction(pool, other, tenant) as conn:
            assert (
                await repo.list_tracks(conn, tenant, other, now + timedelta(days=1))
            )["items"] == []
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM research.study_versions WHERE track_id=$1",
                    track,
                )
                == 0
            )
            with pytest.raises(NotFoundError):
                await service.get_detail(
                    conn, tenant, actor, track, now + timedelta(days=1)
                )
    finally:
        await pool.close()


async def test_real_immutable_history_generation_guard_and_role_permissions(
    monkeypatch,
):
    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, _, run, now = await seed(pool)

        async def empty(*args):
            return []

        monkeypatch.setattr(service, "load_observations", empty)
        async with tenant_transaction(pool, actor, tenant) as conn:
            detail = await create(conn, tenant, actor, run, now)
            track = UUID(detail["track"]["id"])
            for table in (
                "study_versions",
                "study_checks",
                "study_notes",
                "study_trade_links",
            ):
                for operation in ("UPDATE", "DELETE"):
                    assert not await conn.fetchval(
                        "SELECT has_table_privilege(current_user,$1,$2)",
                        "research." + table,
                        operation,
                    )
            for operation in ("INSERT", "UPDATE", "DELETE"):
                assert not await conn.fetchval(
                    "SELECT has_table_privilege('wealthsail_worker','research.study_trade_links',$1)",
                    operation,
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE research.study_versions SET reason='rewrite' WHERE track_id=$1",
                        track,
                    )
            await service.set_status(
                conn,
                tenant,
                actor,
                track,
                StatusRequest.model_validate(
                    {"expectedGeneration": 1, "enabled": False}
                ),
                now + timedelta(minutes=1),
            )
            with pytest.raises(asyncpg.SerializationError):
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO research.study_checks(tenant_id,user_id,track_id,version_id,generation,evaluated_at,input_hash,method_version,result)
                        VALUES($1,$2,$3,$4,1,$5,'late','fixture','{}')""",
                        tenant,
                        actor,
                        track,
                        UUID(detail["version"]["id"]),
                        now + timedelta(minutes=2),
                    )
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
                await conn.execute(
                    "DELETE FROM research.study_versions WHERE track_id=$1", track
                )
    finally:
        await pool.close()


async def test_real_get_detail_and_manual_check_use_only_existing_public_facts():
    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, _, run, now = await seed(pool)
        async with tenant_transaction(pool, actor, tenant) as conn:
            detail = await create(conn, tenant, actor, run, now)
            track = UUID(detail["track"]["id"])
            assert detail["review"]["performance"]["state"] == "UNKNOWN"
            for _ in range(3):
                assert not (
                    await service.check_study(
                        conn, tenant, actor, track, 1, now + timedelta(minutes=1)
                    )
                )["stored"]
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM research.study_checks WHERE track_id=$1",
                    track,
                )
                == 1
            )
            assert (await service.eligible_trades(conn, tenant, actor, track, now))[
                "items"
            ] == []
    finally:
        await pool.close()


async def test_real_checks_preserve_recovery_and_same_period_a_b_a_revisions(
    monkeypatch,
):
    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, _, run, now = await seed(pool)
        facts = [fact(now)]

        async def existing(*args):
            return facts

        monkeypatch.setattr(service, "load_observations", existing)
        async with tenant_transaction(pool, actor, tenant) as conn:
            detail = await create(conn, tenant, actor, run, now)
            track = UUID(detail["track"]["id"])
            detail = await service.append_version(
                conn,
                tenant,
                actor,
                track,
                VersionRequest.model_validate(
                    {
                        "expectedGeneration": 1,
                        "reason": "前向阈值",
                        "conditions": [
                            {"metric": "PMI", "operator": "GT", "threshold": "50"}
                        ],
                    }
                ),
                now + timedelta(minutes=1),
            )
            for minute, value in enumerate((None, "49", "51", "49", "51"), 2):
                facts[:] = [] if value is None else [fact(now, value)]
                checked = await service.check_study(
                    conn, tenant, actor, track, 2, now + timedelta(minutes=minute)
                )
                assert checked["stored"], (
                    f"missing recovery/revision at {minute}: {value}"
                )
                latest = await repo.latest_check(
                    conn,
                    {
                        "tenant_id": tenant,
                        "user_id": actor,
                        "id": track,
                        "generation": 2,
                    },
                    UUID(detail["version"]["id"]),
                    now + timedelta(minutes=minute),
                )
                assert latest["result"]["quality"] == (
                    "UNKNOWN" if value is None else "READY"
                )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM research.study_checks WHERE track_id=$1 AND generation=2",
                    track,
                )
                == 6
            )
            assert not (
                await service.check_study(
                    conn, tenant, actor, track, 2, now + timedelta(minutes=10)
                )
            )["stored"]
    finally:
        await pool.close()


async def test_real_links_only_confirmed_mine_fill_and_preserves_day_precision(
    monkeypatch,
):
    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, other, run, now = await seed(pool)
        instrument, peer = uuid4(), uuid4()
        owned_fills, disallowed = [], []
        async with pool.acquire() as conn:
            for key in (instrument, peer):
                await conn.execute(
                    "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'EXCHANGE_FUND','SSE','study fill fixture')",
                    key,
                )
            for kind in (
                "own",
                "joint",
                "other",
                "collaborator",
                "draft",
                "peer",
                "expired",
            ):
                account, fill = uuid4(), uuid4()
                await conn.execute(
                    "INSERT INTO identity.accounts(id,tenant_id,account_type,name) VALUES($1,$2,'MANUAL',$3)",
                    account,
                    tenant,
                    kind,
                )
                await conn.execute(
                    """INSERT INTO identity.account_members(tenant_id,account_id,user_id,relationship,permission_role,created_by,effective_from,effective_to)
                    VALUES($1,$2,$3,$4,'OWNER',$3,$5,$6)""",
                    tenant,
                    account,
                    other if kind == "other" else actor,
                    "JOINT_OWNER"
                    if kind == "joint"
                    else "COLLABORATOR"
                    if kind == "collaborator"
                    else "BENEFICIAL_OWNER",
                    now.date() - timedelta(days=100),
                    now.date() - timedelta(days=1) if kind == "expired" else None,
                )
                event = None
                if kind != "draft":
                    event = uuid4()
                    await conn.execute(
                        """INSERT INTO ledger.ledger_events(id,tenant_id,account_id,event_type,trade_date,occurred_at,idempotency_key)
                        VALUES($1,$2,$3,'BUY',$4,$5,$6)""",
                        event,
                        tenant,
                        account,
                        now.date(),
                        now,
                        uuid4().hex,
                    )
                await conn.execute(
                    """INSERT INTO ledger.trade_fills(id,tenant_id,account_id,instrument_id,side,quantity,unit_price,fee,trade_date,occurred_at,
                    source_type,status,ledger_event_id,confirmed_at,created_by,confirmed_by,idempotency_key)
                    VALUES($1,$2,$3,$4,'BUY',10,1.3,0.2,$5,$6,'USER_CONFIRMED',$7,$8,$9,$10,$11,$12)""",
                    fill,
                    tenant,
                    account,
                    peer if kind == "peer" else instrument,
                    now.date(),
                    now,
                    "DRAFT" if kind == "draft" else "CONFIRMED",
                    event,
                    None if kind == "draft" else now,
                    actor,
                    None if kind == "draft" else actor,
                    uuid4().hex,
                )
                (owned_fills if kind in {"own", "joint"} else disallowed).append(fill)

        async def empty(*args):
            return []

        monkeypatch.setattr(service, "load_observations", empty)
        async with tenant_transaction(pool, actor, tenant) as conn:
            request = CreateStudyRequest.model_validate(
                {
                    "originType": "DAILY_EVENT",
                    "reportRunId": str(run),
                    "eventId": "event:original",
                    "scope": {"instrumentId": str(instrument)},
                }
            )
            detail = await service.create_study(conn, tenant, actor, request, now)
            track = UUID(detail["track"]["id"])
            candidates = await service.eligible_trades(conn, tenant, actor, track, now)
            assert {row["currency"] for row in candidates["items"]} == {"CNY"}
            assert {UUID(row["fillId"]) for row in candidates["items"]} == set(
                owned_fills
            )
            for fill in disallowed:
                with pytest.raises(NotFoundError):
                    await service.link_trade(
                        conn,
                        tenant,
                        actor,
                        track,
                        TradeLinkRequest.model_validate(
                            {
                                "expectedGeneration": 1,
                                "fillId": str(fill),
                                "reason": "核对实际成交",
                            }
                        ),
                        now,
                    )
            command = TradeLinkRequest.model_validate(
                {
                    "expectedGeneration": 1,
                    "fillId": str(owned_fills[0]),
                    "reason": "关联本人真实成交",
                }
            )
            linked = await service.link_trade(conn, tenant, actor, track, command, now)
            repeated = await service.link_trade(
                conn, tenant, actor, track, command, now
            )
            assert len(repeated["tradeLinks"]) == 1
            assert linked["tradeLinks"][0]["executionTimePrecision"] == "DAY"
            assert linked["tradeLinks"][0]["timingRelation"] == "UNKNOWN"
            assert linked["review"]["performance"]["netReturn"] is None
            assert len(linked["review"]["transactions"]) == 1
            forged = {**candidates["items"][0], "unitPrice": "130"}
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO research.study_trade_links(tenant_id,user_id,track_id,version_id,generation,fill_id,linked_at,reason,timing_relation,fill)
                        VALUES($1,$2,$3,$4,1,$5,$6,'forged','UNKNOWN',$7::jsonb)""",
                        tenant,
                        actor,
                        track,
                        UUID(detail["version"]["id"]),
                        UUID(forged["fillId"]),
                        now,
                        json.dumps(forged),
                    )
    finally:
        await pool.close()
