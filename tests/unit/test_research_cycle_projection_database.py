"""Real stored acceptance summaries can close the observation industry audit."""

import os

import pytest

from backend.investment.fund_observation import build_fund_observation
from backend.macro import research_acceptance_repository as repo
from backend.macro.research_topics import freeze_discovery_packet
from tests.unit.test_fund_observation import case
from tests.unit.test_research_acceptance_database import fixture, outcome
from tests.unit.test_research_cycle_projection_audit import review


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("INVESTMENT_TEST_DATABASE_URL"), reason="owned acceptance DB required")
async def test_saved_and_reloaded_risk_checks_verify_the_real_fund_observation():
    async with fixture() as (conn, run_id, now, *_):
        value = outcome(run_id, now)
        value["accepted"]["industryCycleReviews"] = [review()]
        await repo.save_validation_outcome(conn, run_id, value, recorded_at=now)
        stored = await repo.read_validation_summary(conn, run_id, now)
        packet = case(1)
        packet["asOf"] = packet["quantitativeScan"]["asOf"] = now.isoformat()
        for row in [*packet["discoveryActionableZones"], *packet["quantitativeScan"]["items"]]:
            row.update(asOf=now.date().isoformat(), retrievedAt=now.isoformat(), availableAt=now.isoformat())
        packet["discoveryUniverse"][0]["identityVerifiedAt"] = now.isoformat()
        result = build_fund_observation(
            freeze_discovery_packet(packet, now),
            now,
            research_id=str(run_id),
            validated_at=now,
            industry_reviews=stored["accepted"]["industryCycleReviews"],
            coverage={"searchExecutionVerified": True},
        )
        assert result["items"][0]["industry"]["auditStatus"] == "STRUCTURE_AND_EXECUTION_VERIFIED"
        assert result["items"][0]["industry"]["missingChecks"] == []
