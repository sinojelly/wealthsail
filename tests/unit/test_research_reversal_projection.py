"""Economic reversals retain fund candidates with the remaining risk visible."""

import pytest

from backend.macro.research_opportunities import project_topic_recommendations
from tests.unit.test_topic_fund_recommendations import NOW, fund, source, topic


@pytest.mark.parametrize(
    "title,summary",
    [
        ("光伏行业亏损收窄", "落后产能加速出清"),
        ("医药集采降幅温和", "悲观预期见底，部分企业仍亏损"),
    ],
)
def test_mixed_reversal_keeps_fund_candidates_and_risk_without_trading_permission(
    title, summary
):
    row = topic(title=title, summary=summary)
    item = project_topic_recommendations(
        [row],
        {row["topicId"]: {"funds": [fund()]}},
        sources=[source()],
        reviews=[],
        coverage={},
        packet={},
        validated_at=NOW,
        now=NOW,
    )[0]

    assert item["topicPolarity"] == "MIXED"
    assert item["recommendationStatus"] == "MATCHED"
    assert item["riskStatus"] == "RISK_ALERT"
    assert item["riskScorePenalty"] == 10
    assert item["riskWarnings"]
    assert item["actionable"] is False
    assert item["relatedRiskFunds"] == []
    candidate = item["recommendedFunds"][0]
    assert candidate["fundCode"] == "123456"
    assert candidate["riskStatus"] == "RISK_ALERT"
    assert candidate["actionable"] is False
    assert candidate["canBuyNow"] is False
