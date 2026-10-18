"""Exact direction snapshots and actor isolation in the named disposable DB."""

from copy import deepcopy
from datetime import timedelta
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.errors import NotFoundError, ValidationError_
from backend.investment import study_service as service, study_repository as repo
from backend.investment.study_schemas import CreateStudyRequest
from test_study_direction import NOW, report, request, plan
from test_strategy_memory_database import actor_role

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="explicit disposable study DB required"),
]


async def test_direction_creation_freezes_exact_report_and_repeated_reports_do_not_overwrite(
    monkeypatch,
):
    from backend.investment import opportunity_service

    url = urlsplit(DSN)
    assert (
        url.hostname == "127.0.0.1"
        and url.port == 55434
        and url.path
        in {"/wealthsail_phase2_validation_v3", "/wealthsail_review07_validation_v6"}
    )
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        tenant, actor, other, run1, run2 = (uuid4() for _ in range(5))
        clock = NOW.replace(year=2400) + timedelta(days=uuid4().int % 100000)
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'direction fixture')",
            tenant,
        )
        for user in (actor, other):
            await conn.execute(
                "INSERT INTO identity.profiles(id,nickname) VALUES($1,'direction fixture')",
                user,
            )
            await conn.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
                tenant,
                user,
            )
        first = report(asOf=clock.isoformat())
        source_plan = first["valueDirections"][0]["plans"][0]
        source_plan.update(
            asOf=(clock - timedelta(days=2)).date().isoformat(),
            retrievedAt=clock.isoformat(),
        )
        second_plan = deepcopy(source_plan)
        second_plan.update(
            subjectId="008281",
            fundCode="008281",
            sourceUri="https://fund.eastmoney.com/008281.html",
            label="另一只实际基金",
        )
        first["valueDirections"][0]["plans"].append(second_plan)
        second = deepcopy(first)
        second["asOf"] = (clock + timedelta(days=1)).isoformat()
        second["valueDirections"][0]["plans"][0]["tracks"][0]["entryZoneMin"] = "0.9980"
        for run, stamp, payload in (
            (run1, clock, first),
            (run2, clock + timedelta(days=1), second),
        ):
            await conn.execute(
                """INSERT INTO research.daily_research_runs(run_id,slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet)
                VALUES($1,$2,$3,$3,'direction-fixture','direction-fixture','{}')""",
                run,
                stamp.date(),
                stamp,
            )
            await conn.execute(
                "INSERT INTO research.daily_research_results(run_id,status,recorded_at,payload) VALUES($1,'READY',$2,$3::jsonb)",
                run,
                stamp,
                json.dumps(payload),
            )

        async def facts(*args):
            return []

        async def forbidden(*args, **kwargs):
            raise AssertionError(
                "direction tracking must not recalculate current research"
            )

        monkeypatch.setattr(service, "load_observations", facts)
        monkeypatch.setattr(opportunity_service, "get_context", forbidden)
        await actor_role(conn, actor)

        async def create(run, code="000001", now=clock):
            return await service.create_study(
                conn,
                tenant,
                actor,
                CreateStudyRequest.model_validate(
                    request(reportRunId=str(run), subjectId=code)
                ),
                now,
            )

        created = await create(run1)
        snapshot = created["version"]["snapshot"]
        assert snapshot["actionablePlan"] == source_plan
        assert snapshot["valueDirection"]["key"] == "healthcare-recovery"
        assert snapshot["origin"]["reportRunId"] == str(run1)
        assert snapshot["scope"]["fundCode"] == "000001"
        repeated = await create(run2, now=clock + timedelta(days=1))
        assert repeated["track"]["id"] == created["track"]["id"]
        assert repeated["version"]["snapshot"]["actionablePlan"] == source_plan
        assert repeated["track"]["generation"] == 1 and len(repeated["versions"]) == 1
        another = await create(run1, "008281")
        assert another["track"]["id"] != created["track"]["id"]
        await actor_role(conn, other)
        with pytest.raises(NotFoundError):
            await service.get_detail(conn, tenant, other, created["track"]["id"], clock)
        owned_other = await service.create_study(
            conn,
            tenant,
            other,
            CreateStudyRequest.model_validate(request(reportRunId=str(run1))),
            clock,
        )
        assert owned_other["track"]["id"] != created["track"]["id"]
    finally:
        await tx.rollback()
        await conn.close()


async def test_direction_registered_fund_scope_requires_exact_catalog_and_market():
    url = urlsplit(DSN)
    assert (
        url.hostname == "127.0.0.1"
        and url.port == 55434
        and url.path
        in {"/wealthsail_phase2_validation_v3", "/wealthsail_review07_validation_v6"}
    )
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        instrument = uuid4()
        code = str(900000 + uuid4().int % 99999)
        while await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM reference.instrument_aliases WHERE alias_value=$1 AND market='FUND_CN')",
            code,
        ):
            code = str(900000 + uuid4().int % 99999)
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','direction scope fixture')",
            instrument,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market) VALUES($1,'TICKER',$2,'FUND_CN')",
            instrument,
            code,
        )
        actual = plan(subjectKind="fund", subjectId=str(instrument), fundCode=code)
        assert await repo.direction_plan_scope(conn, actual) == {
            "instrumentId": str(instrument),
            "fundCode": code,
            "fundMarket": "FUND_CN",
        }
        with pytest.raises(NotFoundError):
            await repo.direction_plan_scope(
                conn, {**actual, "fundCode": "not-this-fund"}
            )
        with pytest.raises(ValidationError_):
            await repo.direction_plan_scope(conn, {**actual, "market": "SSE"})
    finally:
        await tx.rollback()
        await conn.close()
