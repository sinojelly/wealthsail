"""Exact scan freezing and cross-entry deduplication in review44's owned DB."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import os
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.errors import NotFoundError
from backend.investment import study_service as service
from backend.investment import study_repository as repo
from backend.investment.study_schemas import CreateStudyRequest
from backend.macro.daily_repository import read_daily_research
from test_quantitative_scan import NOW, scan, scan_plan
from test_strategy_memory_database import actor_role

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DSN, reason="explicit disposable review44 study DB required"
    ),
]


@pytest.fixture(autouse=True)
def isolate_scan_history_from_live_prices(monkeypatch):
    monkeypatch.setattr(
        repo,
        "_refresh_origin_plan",
        AsyncMock(
            side_effect=lambda _conn, selected, *_args, **_kwargs: deepcopy(selected)
        ),
    )


def guard():
    url = urlsplit(DSN)
    assert (
        url.hostname == "127.0.0.1"
        and url.port in {55434, 55436}
        and url.path
        in {
            "/wealthsail_review44_validation_v1",
            "/wealthsail_review45_validation_v1",
            "/wealthsail_review48_validation_v1",
        }
    )


async def seed(conn, clock):
    tenant, actor, other, run1, run2 = [uuid4() for _ in range(5)]
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'scan44 fixture')", tenant
    )
    for user in (actor, other):
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'scan44 fixture')",
            user,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
            tenant,
            user,
        )
    public_scan = scan(1)
    public_scan["asOf"] = clock.isoformat()
    public_scan["items"] = [
        scan_plan(
            "008281",
            asOf=(clock - timedelta(days=1)).date().isoformat(),
            retrievedAt=clock.isoformat(),
            availableAt=clock.isoformat(),
        )
    ]
    plan = {**deepcopy(public_scan["items"][0]), "selectedTrack": "RIGHT_TREND"}
    report = {
        "asOf": clock.isoformat(),
        "valueDirections": [
            {
                "key": "semiconductor-cycle",
                "title": "芯片研究",
                "thesis": "独立产业观察",
                "sourceIds": ["one", "two"],
                "macroCodes": [],
                "plans": [plan],
            }
        ],
        "sources": [
            {"id": key, "url": "https://example.org/" + key} for key in ("one", "two")
        ],
    }
    for run, stamp, scan_data in (
        (run1, clock, public_scan),
        (run2, clock + timedelta(days=1), deepcopy(public_scan)),
    ):
        payload = deepcopy(report)
        payload["asOf"] = stamp.isoformat()
        scan_data["asOf"] = stamp.isoformat()
        if run == run2:
            scan_data["items"][0]["tracks"][0]["entryZoneMin"] = "0.9800"
        await conn.execute(
            """INSERT INTO research.daily_research_runs(run_id,slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet)
            VALUES($1,$2,$3,$3,'scan44-fixture','scan44-fixture',$4::jsonb)""",
            run,
            stamp.date(),
            stamp,
            json.dumps({"quantitativeScan": scan_data}),
        )
        await conn.execute(
            "INSERT INTO research.daily_research_results(run_id,status,recorded_at,payload) VALUES($1,'READY',$2,$3::jsonb)",
            run,
            stamp,
            json.dumps(payload),
        )
    return tenant, actor, other, run1, run2


def request(run, origin="DAILY_QUANT_SCAN"):
    return CreateStudyRequest.model_validate(
        {
            "originType": origin,
            "reportRunId": str(run),
            "subjectKind": "nav",
            "subjectId": "008281",
            "planTrack": "RIGHT_TREND",
            **(
                {"valueDirectionKey": "semiconductor-cycle"}
                if origin == "DAILY_DIRECTION"
                else {}
            ),
        }
    )


async def public_facts(*args):
    return []


async def test_scan_legacy_report_fallback_freezes_original_and_actor_isolated(
    monkeypatch,
):
    guard()
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    monkeypatch.setattr(service, "load_observations", public_facts)
    clock = NOW.replace(year=2400) + timedelta(days=uuid4().int % 100000)
    try:
        tenant, actor, other, run1, run2 = await seed(conn, clock)
        await actor_role(conn, actor)
        display = await read_daily_research(conn, clock)
        assert display["reportRunId"] == str(run1)
        assert len(display["report"]["quantitativeScan"]["items"]) == 1
        created = await service.create_study(conn, tenant, actor, request(run1), clock)
        first = created["version"]["snapshot"]
        assert first["actionablePlan"]["tracks"][0]["entryZoneMin"] == "0.9900"
        assert first["origin"]["reportRunId"] == str(run1)
        assert first["origin"]["originType"] == "DAILY_QUANT_SCAN"
        again = await service.create_study(
            conn, tenant, actor, request(run2), clock + timedelta(days=1)
        )
        assert again["track"]["id"] == created["track"]["id"]
        assert again["version"]["snapshot"] == first
        assert len(again["versions"]) == 1
        direction = await service.create_study(
            conn, tenant, actor, request(run1, "DAILY_DIRECTION"), clock
        )
        assert direction["track"]["id"] == created["track"]["id"]
        await actor_role(conn, other)
        with pytest.raises(NotFoundError):
            await service.get_detail(conn, tenant, other, created["track"]["id"], clock)
        mine = await service.create_study(conn, tenant, other, request(run1), clock)
        assert mine["track"]["id"] != created["track"]["id"]
    finally:
        await tx.rollback()
        await conn.close()


async def test_scan_and_direction_concurrent_click_share_one_actor_track(monkeypatch):
    guard()
    monkeypatch.setattr(service, "load_observations", public_facts)
    conn = await asyncpg.connect(DSN)
    clock = NOW.replace(year=2800) + timedelta(days=uuid4().int % 100000)
    seeded = None
    try:
        async with conn.transaction():
            seeded = await seed(conn, clock)
        tenant, actor, _, run1, _ = seeded

        async def create(origin):
            other_conn = await asyncpg.connect(DSN)
            try:
                async with other_conn.transaction():
                    await actor_role(other_conn, actor)
                    return await service.create_study(
                        other_conn, tenant, actor, request(run1, origin), clock
                    )
            finally:
                await other_conn.close()

        first, second = await asyncio.gather(
            create("DAILY_QUANT_SCAN"), create("DAILY_DIRECTION")
        )
        assert first["track"]["id"] == second["track"]["id"]
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.study_tracks WHERE tenant_id=$1 AND user_id=$2",
                tenant,
                actor,
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.study_versions WHERE tenant_id=$1 AND user_id=$2",
                tenant,
                actor,
            )
            == 1
        )
    finally:
        if seeded:
            tenant, actor, other, run1, run2 = seeded
            async with conn.transaction():
                # Only the exact disposable DB's admin connection may remove
                # this committed concurrency fixture. Runtime roles retain
                # their append-only protections throughout the actual test.
                await conn.execute("SET LOCAL session_replication_role=replica")
                for table in ("trade_links", "notes", "checks", "versions", "tracks"):
                    await conn.execute(
                        f"DELETE FROM research.study_{table} WHERE tenant_id=$1", tenant
                    )
                await conn.execute(
                    "DELETE FROM research.daily_research_results WHERE run_id=ANY($1::uuid[])",
                    [run1, run2],
                )
                await conn.execute(
                    "DELETE FROM research.daily_research_runs WHERE run_id=ANY($1::uuid[])",
                    [run1, run2],
                )
                await conn.execute(
                    "DELETE FROM identity.tenant_members WHERE tenant_id=$1", tenant
                )
                await conn.execute(
                    "DELETE FROM identity.profiles WHERE id=ANY($1::uuid[])",
                    [actor, other],
                )
                await conn.execute("DELETE FROM identity.tenants WHERE id=$1", tenant)
        await conn.close()
