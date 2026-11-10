"""Opt-in real frozen evidence regression; never stores evidence in Git or calls AI.

CI without the private, ignored export skips this file. Mandatory synthetic safety
and storage tests remain in the normal suite. This test is executed locally for
the user-provided NAS export and binds the exact three original hashes.
"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from backend.macro.daily_research import ResearchValidationError, diagnose_report
from backend.macro.research_acceptance_replay import run_offline_repair
from backend.macro.research_frozen_bundle import read_frozen_bundle

RUN = "ceaa7c1c-d01f-4afa-b2b3-43d77ef04409"
EXPORT = Path(
    os.environ.get(
        "WEALTHSAIL_REPAIR_CASE_EXPORT",
        "artifacts/research-recovery/ceaa7c1c-frozen.json",
    )
)
HASHES = {
    "PACKET": "c26a02808482bd0fee234fc12ca7c35a043ee7dc784e3e41aadf01a4d241f7c2",
    "RAW_MODEL": "c6a4ac0ccf2a347f6bc188d6a96aac0acd5590bd8b2518850745f2f549c1709c",
    "SOURCE_CHECKS": "9d0cb5c857121aab79a985dcc08eab9ae02d059705edc28307468d30c104937f",
}
pytestmark = pytest.mark.skipif(
    not EXPORT.is_file(),
    reason="original frozen export not present; no production access",
)


@pytest.mark.asyncio
async def test_exact_frozen_case_passes_full_strict_in_fixed_zero_ai_child():
    export_digest = sha256(EXPORT.read_bytes()).hexdigest()
    frozen = read_frozen_bundle(EXPORT, RUN, observed_at=datetime.now(timezone.utc))
    assert frozen.material_hashes == HASHES
    assert frozen.validated_at.isoformat() == "2026-11-03T16:22:28.205399+00:00"
    original = json.loads(frozen.raw)
    before = deepcopy((frozen.packet, frozen.source_checks, original))
    with pytest.raises(ResearchValidationError) as error:
        diagnose_report(
            original,
            frozen.packet,
            frozen.source_checks,
            completed_at=frozen.validated_at,
        )
    assert error.value.rule_id == "DIRECTION_INDUSTRY_REVIEW_UNKNOWN"

    result = await run_offline_repair(frozen.repair_payload())
    assert (
        result["repairVersion"] == "research-repair-v1"
    )  # never replay V1 evidence under V2 coverage rules
    assert result["strictPass"] is True
    # HIGH dependent facts and legacy narrative remain a human-only historical
    # annex. A passing reduced candidate must never auto-publish this old report.
    assert result["autoPublishAllowed"] is False
    assert result["historicalPublishAllowed"] is True
    assert result["retained"]["counts"] == {
        "sources": 23,
        "industryCycleReviews": 10,
        "valueDirections": 6,
        "fundPlans": 8,
        "events": 2,
        "upcoming": 1,
    }
    isolated = result["quarantined"]
    assert {(row["itemType"], row["itemIndex"]) for row in isolated} == {
        ("VALUE_DIRECTION", 6),
        ("VALUE_DIRECTION", 7),
        ("EVENT", 1),
        ("EVENT", 3),
        ("UPCOMING", 1),
    }
    assert all(row["mandatory"] is True for row in isolated)
    assert all(row["sourceEvidence"] for row in isolated)
    assert [row["businessKey"] for row in isolated[:2]] == [
        "ai-hardware-earnings",
        "gold-energy-divergence",
    ]
    assert isolated[0]["risks"] == original["valueDirections"][6]["risks"]
    assert all(
        row["importance"] == "HIGH" for row in isolated if row["itemType"] == "EVENT"
    )
    expected = deepcopy(original)
    expected["valueDirections"] = original["valueDirections"][:6]
    expected["events"] = [original["events"][i] for i in (0, 2)]
    expected["upcoming"] = original["upcoming"][:1]
    assert result["candidateRaw"] == expected
    # The public validator repeats risk merging, dual-track price guards,
    # scoring and reference closure at the exact original evidence clock.
    assert result["formalReport"] == diagnose_report(
        expected, frozen.packet, frozen.source_checks, completed_at=frozen.validated_at
    )
    assert (
        result["candidateRaw"]["industryCycleReviews"]
        == original["industryCycleReviews"]
    )
    assert result["candidateRaw"]["sources"] == original["sources"]
    assert before == (frozen.packet, frozen.source_checks, original)
    assert sha256(EXPORT.read_bytes()).hexdigest() == export_digest
