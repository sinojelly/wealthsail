"""Future topic matching and public evidence IDs follow their frozen policy."""

from backend.macro.research_batch import evaluate_research_batch
from tests.unit.test_research_cost_review75 import prepared, RUN
from tests.unit.test_daily_research import NOW


def test_future_batch_uses_frozen_v2_match_and_canonical_evidence_ids():
    packet, raw, checks = prepared()
    topic = {
        "key": "broker-observation",
        "title": "券商观察",
        "thesis": "仍待核验。",
        "industryClaims": ["broker"],
        "relatedIndustryIds": [],
        "sourceIds": [raw["sources"][0]["id"]],
        "unknowns": [],
        "pendingTasks": [],
    }
    raw["topicCatalog"] = [topic]
    raw["valueDirections"] = []
    original_ids = topic["sourceIds"].copy()
    result = evaluate_research_batch(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    matched = result["registry"]["items"][0]
    assert matched["match"]["status"] == "STANDARD"
    assert matched["match"]["industryIds"] == ["BROKER"]
    public_ids = {row["id"] for row in result["originalOutcome"].accepted["sources"]}
    assert matched["sourceIds"] and set(matched["sourceIds"]) <= public_ids
    assert raw["topicCatalog"][0]["sourceIds"] == original_ids
    assert result["registry"]["coverage"]["evidenceBackedTopicCount"] == int(
        any(
            row["id"] in matched["sourceIds"]
            and row.get("verification") == "SOURCE_CHECKED"
            for row in result["originalOutcome"].accepted["sources"]
        )
    )
