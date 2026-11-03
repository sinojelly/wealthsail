"""Real exported evidence, cloned only inside a rollback-only disposable DB.

The source identity matches the export. No production connection or historical
record is touched, and neither the candidate nor evidence is checked into Git.
"""

from datetime import datetime, timezone
import json
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.macro import research_acceptance_repository as acceptance
from backend.macro import research_history_repair_repository as repository
from backend.macro.research_acceptance import validate_report_outcome
from backend.macro.research_frozen_bundle import read_frozen_bundle
from tests.unit.test_research_repair_frozen_case import EXPORT, HASHES, RUN

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DSN or not EXPORT.is_file(),
        reason="owned repair101 DB and original export required",
    ),
]


async def test_real_frozen_material_can_generate_review_and_publish_history_only(
    monkeypatch,
):
    from backend.macro import research_acceptance_replay as child

    target = urlsplit(DSN)
    assert target.hostname in {"127.0.0.1", "localhost", "::1"}
    assert target.port == 29034 and target.path in {
        "/wealthsail_acceptance_repair101_v1",
        "/wealthsail_acceptance_repair101_v2",
    }
    frozen = read_frozen_bundle(EXPORT, RUN, observed_at=datetime.now(timezone.utc))
    assert frozen.material_hashes == HASHES
    exported = json.loads(EXPORT.read_text("utf-8-sig"))
    original = validate_report_outcome(
        frozen.raw,
        frozen.packet,
        frozen.source_checks,
        research_id=RUN,
        completed_at=frozen.validated_at,
    ).to_dict()
    assert original["decision"] == "PARTIAL"
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    run_id, tenant, creator, reviewer, viewer = (
        UUID(RUN),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    try:
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM research.daily_research_runs WHERE run_id=$1)",
            run_id,
        )
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'frozen repair rollback fixture')",
            tenant,
        )
        for actor, role in (
            (creator, "OWNER"),
            (reviewer, "ADMIN"),
            (viewer, "VIEWER"),
        ):
            await conn.execute(
                "INSERT INTO identity.profiles(id,nickname) VALUES($1,'offline frozen fixture')",
                actor,
            )
            await conn.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,$3)",
                tenant,
                actor,
                role,
            )
        dispatched = datetime.fromisoformat(frozen.packet["asOf"])
        await conn.execute(
            "INSERT INTO research.daily_research_runs(run_id,slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet) VALUES($1,($2 AT TIME ZONE 'Asia/Shanghai')::date,$2,$2,$3,'offline-clone-original-v16',$4::jsonb)",
            run_id,
            dispatched,
            HASHES["PACKET"],
            acceptance._encode(frozen.packet),
        )
        await conn.execute(
            "INSERT INTO research.daily_research_results(run_id,status,recorded_at,error_class) VALUES($1,'FAILED',$2,'ResearchValidationError')",
            run_id,
            frozen.validated_at,
        )
        # Preserve exact exported byte descriptors rather than reserialize bytes.
        await acceptance._save_frozen_artifacts(
            conn,
            run_id,
            frozen.validated_at,
            [
                exported["artifacts"][kind]
                for kind in ("packet", "raw_output", "source_checks")
            ],
        )
        await acceptance.save_validation_outcome(
            conn, run_id, original, recorded_at=frozen.validated_at
        )
        snapshots = {}
        for table in (
            "daily_research_runs",
            "daily_research_results",
            "research_validation_records",
            "research_validation_artifacts",
        ):
            snapshots[table] = await conn.fetchval(
                f"SELECT jsonb_agg(to_jsonb(t)) FROM research.{table} t WHERE run_id=$1",
                run_id,
            )
        original_runner, calls = child.run_offline_repair, []

        async def counted(payload):
            calls.append(payload["researchId"])
            return await original_runner(payload)

        monkeypatch.setattr(child, "run_offline_repair", counted)
        await apply_tenant_context(conn, str(creator), str(tenant))
        observed = datetime.now(timezone.utc)
        preview = await repository.preview_history_repair(
            conn, run_id, creator, tenant, observed
        )
        assert preview["strictPass"] and preview["historicalPublishAllowed"]
        assert preview["autoPublishAllowed"] is False
        assert (
            json.loads(
                await conn.fetchval(
                    "SELECT research.list_history_repairs($1,true,20)", run_id
                )
            )
            == []
        )
        command = uuid4()
        draft = await repository.generate_history_repair(
            conn, run_id, creator, tenant, command, observed
        )
        same = await repository.generate_history_repair(
            conn, run_id, creator, tenant, command, observed
        )
        assert draft["id"] == same["id"] and draft["state"] == "DRAFT"
        assert calls == [RUN, RUN]  # one preview + one generation; retry does no work
        assert draft["retained"]["counts"]["fundPlans"] == 8
        details = await repository.read_history_repair_details(
            conn, UUID(draft["id"]), observed
        )
        assert details["sourceDigests"] == HASHES
        assert len(details["privateResult"]["formalReport"]["valueDirections"]) == 6
        assert (
            details["privateResult"]["formalReport"]["valueDirections"][0].get(
                "opportunityScore"
            )
            is not None
        )
        await apply_tenant_context(conn, str(reviewer), str(tenant))
        await repository.review_history_repair(
            conn, UUID(draft["id"]), reviewer, tenant, uuid4(), "APPROVE", observed
        )
        await repository.publish_history_repair(
            conn, UUID(draft["id"]), reviewer, tenant, uuid4(), observed
        )
        await apply_tenant_context(conn, str(viewer), str(tenant))
        public = await repository.read_history_repair(conn, UUID(draft["id"]), observed)
        assert (
            public["state"] == "PUBLISHED"
            and public["publicationScope"] == "HISTORICAL_READ_ONLY"
        )
        assert public["actionable"] is False and len(public["quarantined"]) == 5
        assert any(item.get("risks") for item in public["quarantined"])
        assert not any(
            field in json.dumps(public)
            for field in (
                "entryZoneMin",
                "currentPrice",
                "privateResult",
                "candidateRaw",
                "originalItem",
            )
        )
        await conn.execute("RESET ROLE")
        for table, value in snapshots.items():
            assert (
                await conn.fetchval(
                    f"SELECT jsonb_agg(to_jsonb(t)) FROM research.{table} t WHERE run_id=$1",
                    run_id,
                )
                == value
            )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.daily_research_results WHERE run_id=$1 AND status='READY'",
                run_id,
            )
            == 0
        )
    finally:
        await tx.rollback()
        await conn.close()
