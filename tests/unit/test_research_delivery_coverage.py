"""Readable content and complete evidence coverage are separate observations."""

import math

import pytest

from backend.errors import ValidationError_
from backend.macro.research_batch import evaluate_research_batch
from backend.macro.research_topic_repository import prepare_topic_registry
from tests.unit.test_research_cost_review75 import prepared, executed_checks, RUN
from tests.unit.test_research_independent_contracts import combined_case
from tests.unit.test_daily_research import NOW
from tests.unit.test_research_topic_repository import registry


def test_formal_fallback_topic_does_not_inflate_discovery_evidence_coverage():
    packet, raw, checks = prepared()
    raw["topicCatalog"] = []
    result = evaluate_research_batch(
        raw, packet, executed_checks(packet, checks), research_id=RUN, validated_at=NOW,
    )
    coverage = result["registry"]["coverage"]
    assert coverage["discoveredTopicCount"] == coverage["evidenceBackedTopicCount"] == 0
    assert coverage["pendingVerificationTopicCount"] == 0
    assert coverage["evidenceBackedTopicRatioPercent"] is None


def test_64_registered_discoveries_expose_checked_and_pending_parts_independently():
    packet, raw, checks = combined_case()
    for row in raw["topicCatalog"][2:]:
        row["sourceIds"] = ["s01"]
    checks["s01"]["verification"] = "UNVERIFIED"
    checks["s01"]["quoteMatched"] = False
    result = evaluate_research_batch(raw, packet, checks, research_id=RUN, validated_at=NOW)
    coverage = result["registry"]["coverage"]
    assert coverage["discoveredTopicCount"] == 64
    assert coverage["evidenceBackedTopicCount"] == 2
    assert coverage["pendingVerificationTopicCount"] == 62
    assert coverage["evidenceBackedTopicRatioPercent"] == 3.1
    projected = prepare_topic_registry(result["registry"], RUN, NOW)
    assert projected["coverage"]["pendingVerificationTopicCount"] == 62
    assert projected["coverage"]["evidenceBackedTopicRatioPercent"] == 3.1


@pytest.mark.parametrize("value", [True, -1, 101, math.nan, math.inf, "100"])
def test_topic_projection_rejects_invalid_evidence_coverage_ratio(value):
    data = registry(RUN, 1)
    data["coverage"]["evidenceBackedTopicRatioPercent"] = value
    with pytest.raises(ValidationError_):
        prepare_topic_registry(data, RUN, NOW)
