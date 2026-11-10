"""Real ecb evidence keeps its original clock, contract and zero AI calls."""

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.investment.fund_observation import build_fund_observation
from backend.macro.research_batch import diagnose_research_batch
from backend.macro.research_frozen_bundle import read_frozen_bundle

RUN = "ecbfb663-b677-440f-b7a8-10443be5799b"
FILE = Path("artifacts/research-recovery/ecbfb663-frozen.json")


@pytest.mark.skipif(
    not FILE.is_file(), reason="private ecb frozen export is local only"
)
def test_real_ecb_zero_formal_twenty_one_topics_and_thirty_nine_observations():
    before = sha256(FILE.read_bytes()).hexdigest()
    frozen = read_frozen_bundle(FILE, RUN, observed_at=datetime.now(timezone.utc))
    raw = json.loads(frozen.raw)
    batch = diagnose_research_batch(
        raw,
        frozen.packet,
        frozen.source_checks,
        research_id=RUN,
        validated_at=frozen.validated_at,
    )
    assert batch["originalOutcome"].decision == "COMPLETE"
    assert batch["autoPublishAllowed"] is True
    assert batch["formalReport"]["valueDirections"] == raw["valueDirections"] == []
    registry, accepted = batch["registry"], batch["originalOutcome"].accepted
    assert len(registry["items"]) == 21
    assert Counter(row["reasonCode"] for row in registry["items"]) == {
        "INDUSTRY_IDENTITY_PENDING": 21,
    }
    assert registry["coverage"]["evidenceBackedTopicCount"] == 2
    assert registry["coverage"]["executionVerifiedCheckCount"] == 0
    assert len(accepted["events"]) == 8 and len(accepted["upcoming"]) == 4
    result = build_fund_observation(
        frozen.packet,
        frozen.validated_at,
        research_id=RUN,
        validated_at=frozen.validated_at,
        topics=registry["items"],
        sources=accepted["sources"],
        industry_reviews=accepted["industryCycleReviews"],
        coverage=registry["coverage"],
        formal_report=batch["formalReport"],
    )
    assert result["coverage"] == {
        "catalogFundCount": 46,
        "scannedFundCount": 45,
        "confirmedNavFundCount": 45,
        "pricedFundCount": 39,
        "observableFundCount": 39,
        "unpricedFundCount": 7,
        "highlightCount": 10,
        "selectedScopeCount": 10,
        "deferredScopeCount": 52,
        "executionVerifiedCheckCount": 0,
    }
    assert result["unresolvedRiskNotice"]
    assert all(
        not row["investmentEligible"] and not row["actionable"]
        for row in result["items"]
    )
    assert not any(
        link["relation"] == "CONFIRMED_SCOPE_LINK"
        for row in result["items"]
        for link in row["topicAssociations"]
    )
    assert Counter(row["subscription"]["status"] for row in result["items"]) == {
        "UNKNOWN": 46
    }
    assert sha256(FILE.read_bytes()).hexdigest() == before
