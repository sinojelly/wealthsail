"""Real, locally exported ef47 materials are read-only regression evidence."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.integrations.research_execution import executed_task_ids
from backend.macro.research_batch import evaluate_research_batch
from backend.macro.research_frozen_bundle import read_frozen_bundle

RUN = "ef47f9fd-88a3-424a-8584-a562843805cc"
BUNDLE = Path(__file__).resolve().parents[2] / "artifacts/research-recovery/ef47f9fd-frozen.json"


@pytest.mark.skipif(not BUNDLE.is_file(), reason="private local ef47 export required; synthetic case always runs")
def test_actual_ef47_news_delivery_without_fabricating_legacy_partial_search_proof():
    digest_before = sha256(BUNDLE.read_bytes()).hexdigest()
    frozen = read_frozen_bundle(BUNDLE, RUN, observed_at=datetime.now(timezone.utc))
    raw = json.loads(frozen.raw)
    assert len(raw["topicCatalog"]) == 64 and raw["valueDirections"] == []
    assert len(raw["sources"]) == 24 and len(raw["industryCycleReviews"]) == 10
    assert len(raw["events"]) == 6 and len(raw["upcoming"]) == 3
    manifest = frozen.packet["researchDiscovery"]["auditManifest"]
    binding = frozen.source_checks["__wealthsail_execution_v1"]
    receipt = binding["receipt"]
    assert receipt["version"] == "research-execution-v1" and receipt["coverage"] == "PARTIAL"
    assert len(receipt["queryReceipts"]) == 52
    observed = {row["queryHash"] for row in receipt["queryReceipts"]}
    assert sum(sha256(row["query"].encode()).hexdigest() in observed for row in manifest["tasks"]) == 50
    assert executed_task_ids(receipt, manifest) == set()
    original = deepcopy((frozen.packet, frozen.source_checks))
    result = evaluate_research_batch(
        frozen.raw, frozen.packet, frozen.source_checks,
        research_id=RUN, validated_at=frozen.validated_at,
    )
    assert result["strictPass"] is True
    retained = result["strictFormalReport"]
    assert retained["valueDirections"] == []
    assert len(retained["events"]) == 6 and len(retained["upcoming"]) == 3
    assert len(result["registry"]["items"]) == 64
    assert all(not row["investmentEligible"] for row in result["registry"]["items"])
    assert result["registry"]["coverage"]["executionMatchedTaskCount"] == 0
    assert result["registry"]["coverage"]["searchExecutionVerified"] is False
    assert retained["events"][0]["factVerification"] == "UNVERIFIED"
    assert original == (frozen.packet, frozen.source_checks)
    assert digest_before == sha256(BUNDLE.read_bytes()).hexdigest()
