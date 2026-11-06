"""Polarity, confidence and indirect exposure are separate presentation facts."""

from backend.macro.research_opportunities import project_topic_recommendations
from tests.unit.test_topic_fund_recommendations import NOW, fund, source, topic


def project(row, recalled):
    return project_topic_recommendations(
        [row],
        {row["topicId"]: recalled},
        sources=[source()],
        reviews=[],
        coverage={},
        packet={},
        validated_at=NOW,
        now=NOW,
    )[0]


def test_negative_only_story_never_appears_as_a_positive_recommendation():
    row = topic(title="机器人企业倒闭潮", summary="产能过剩导致裁员与下游砍单")
    result = project(row, {"funds": [fund()]})
    assert result["topicPolarity"] == "NEGATIVE_RISK"
    assert result["recommendedFunds"] == []
    assert result["relatedRiskFunds"][0]["fundCode"] == "123456"
    assert result["recommendationStatus"] == "RISK_ONLY"
    assert result["riskStatus"] == "RISK_ALERT"


def test_macro_observation_does_not_count_a_matched_product_as_an_opportunity():
    row = topic(title="央行利率决议", summary="观察通胀与利率传导")
    result = project(row, {"funds": [fund()]})
    assert result["topicPolarity"] == "MACRO_OBSERVATION"
    assert not result["recommendedFunds"]
    assert result["observationFunds"][0]["fundCode"] == "123456"
    assert result["recommendationStatus"] == "OBSERVATION_ONLY"


def test_mixed_thesis_keeps_recommendation_and_explicit_negative_divergence():
    row = topic(title="芯片订单增长与价格战并存", summary="利润改善但部分企业亏损")
    result = project(row, {"funds": [fund()]})
    assert result["topicPolarity"] == "MIXED"
    assert result["recommendedFunds"]
    assert result["riskStatus"] == "RISK_ALERT"
    assert result["riskScorePenalty"] == 10


def test_broad_fallback_is_indirect_even_in_legacy_funds_array():
    row = topic()
    broad = {
        **fund(),
        "matchLevel": "BROAD_FALLBACK",
        "associationLevel": "BROAD_FALLBACK",
    }
    result = project(row, {"funds": [broad]})
    assert not result["recommendedFunds"]
    assert result["indirectFunds"][0]["fundCode"] == "123456"
    assert result["recommendationStatus"] == "INDIRECT_ONLY"
    assert result["priority"]["matchedFundCount"] == 0


def test_index_association_confidence_participates_in_topic_priority():
    low, high = topic("name"), topic("index")
    recalls = {
        low["topicId"]: {"funds": [{**fund(90), "associationLevel": "NAME_CANDIDATE"}]},
        high["topicId"]: {"funds": [{**fund(30), "associationLevel": "DIRECT_INDEX"}]},
    }
    result = project_topic_recommendations(
        [low, high],
        recalls,
        sources=[source()],
        reviews=[],
        coverage={},
        packet={},
        validated_at=NOW,
        now=NOW,
    )
    assert [row["key"] for row in result] == ["index", "name"]
