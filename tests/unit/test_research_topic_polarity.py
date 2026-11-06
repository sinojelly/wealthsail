"""A risk headline is not a positive fund recommendation."""

from importlib import import_module, util

import pytest


def classify(title, summary="", *, checked=True):
    assert util.find_spec("backend.macro.research_topic_polarity"), (
        "topic polarity classification missing"
    )
    module = import_module("backend.macro.research_topic_polarity")
    return module.classify_topic_polarity(
        {
            "title": title,
            "summary": summary,
            "sourceIds": ["s1"],
            "match": {"industryIds": []},
        },
        sources=[
            {"id": "s1", "verification": "SOURCE_CHECKED" if checked else "UNVERIFIED"}
        ],
    )


@pytest.mark.parametrize(
    "title,summary,expected",
    [
        ("电网设备出海提速", "海外订单增长支持设备需求", "POSITIVE_OPPORTUNITY"),
        ("机器人企业倒闭潮", "下游砍单，行业产能过剩", "NEGATIVE_RISK"),
        ("创新药获批但商业化承压", "临床获批与收入增长并存，但企业亏损和裁员", "MIXED"),
        ("央行利率决议", "观察利率与通胀变化", "MACRO_OBSERVATION"),
        ("行业年度论坛", "讨论长期产业演进", "UNDETERMINED"),
        ("没有发生裁员和倒闭", "设备订单增长已披露", "POSITIVE_OPPORTUNITY"),
        ("机器人尚未获批", "当前没有增长证据", "UNDETERMINED"),
        ("利润下降与订单增长", "反方证据未消失", "MIXED"),
        ("产能过剩风险下降", "价格战已经缓解，订单增长", "POSITIVE_OPPORTUNITY"),
    ],
)
def test_topic_polarity_tracks_economic_direction_without_trading_permission(
    title, summary, expected
):
    result = classify(title, summary)
    assert result["topicPolarity"] == expected
    assert result["polarityReasons"]
    assert result["actionable"] is False


def test_unverified_positive_story_is_retained_as_undetermined_not_promoted():
    result = classify("电网订单增长", checked=False)
    assert result["topicPolarity"] == "UNDETERMINED"
    assert "SOURCE_SUPPORT_PENDING" in result["polarityReasonCodes"]


def test_unverified_negative_story_still_does_not_become_a_positive_opportunity():
    result = classify("机器人企业倒闭", checked=False)
    assert result["topicPolarity"] == "NEGATIVE_RISK"
    assert "SOURCE_SUPPORT_PENDING" in result["polarityReasonCodes"]


def test_partially_verified_or_duplicate_sources_do_not_promote_positive_thesis():
    module = import_module("backend.macro.research_topic_polarity")
    row = {"title": "电网订单增长", "sourceIds": ["s1", "s2"]}
    for sources in (
        [{"id": "s1", "verification": "SOURCE_CHECKED"}],
        [
            {"id": "s1", "verification": "SOURCE_CHECKED"},
            {"id": "s1", "verification": "SOURCE_CHECKED"},
        ],
    ):
        result = module.classify_topic_polarity(row, sources=sources)
        assert result["topicPolarity"] == "UNDETERMINED"
        assert "SOURCE_SUPPORT_PENDING" in result["polarityReasonCodes"]


@pytest.mark.parametrize(
    "title,summary,expected",
    [
        ("光伏行业亏损收窄", "落后产能加速出清", "MIXED"),
        ("光伏落后产能出清", "供给压力趋缓", "POSITIVE_OPPORTUNITY"),
        ("医药集采降幅温和", "悲观预期见底", "POSITIVE_OPPORTUNITY"),
        ("医药集采降幅温和", "", "POSITIVE_OPPORTUNITY"),
        ("医药悲观预期见底", "", "POSITIVE_OPPORTUNITY"),
        ("光伏供需拐点出现", "", "POSITIVE_OPPORTUNITY"),
        ("光伏盈利触底", "", "POSITIVE_OPPORTUNITY"),
        ("光伏盈利修复", "", "POSITIVE_OPPORTUNITY"),
        ("医药业绩超预期", "", "POSITIVE_OPPORTUNITY"),
        ("补贴政策落地", "", "POSITIVE_OPPORTUNITY"),
        ("稳增长政策落地", "", "POSITIVE_OPPORTUNITY"),
        ("产能过剩风险下降", "", "POSITIVE_OPPORTUNITY"),
    ],
)
def test_sourced_economic_reversal_is_not_lost_to_negative_word_matching(
    title, summary, expected
):
    result = classify(title, summary)
    assert result["topicPolarity"] == expected
    assert result["polarityEvidenceSourceIds"] == ["s1"]
    assert result["actionable"] is False


@pytest.mark.parametrize(
    "title,expected",
    [
        ("光伏亏损扩大，订单收窄", "NEGATIVE_RISK"),
        ("企业裁员出清", "NEGATIVE_RISK"),
        ("违约风险落地", "NEGATIVE_RISK"),
        ("风险落地", "UNDETERMINED"),
        ("医药政策落地", "UNDETERMINED"),
        ("医药亏损超预期", "NEGATIVE_RISK"),
        ("订单超预期下滑", "NEGATIVE_RISK"),
        ("盈利超预期恶化", "NEGATIVE_RISK"),
        ("行业尚未出现拐点", "UNDETERMINED"),
        ("行业不代表修复", "UNDETERMINED"),
        ("行业产能出清尚未发生", "UNDETERMINED"),
        ("行业盈利修复尚未兑现", "UNDETERMINED"),
        ("行业盈利修复尚未得到确认", "UNDETERMINED"),
        ("行业产能出清预期尚未兑现", "UNDETERMINED"),
        ("公司亏损未收窄", "NEGATIVE_RISK"),
    ],
)
def test_reversal_words_require_an_affirmed_improving_economic_claim(title, expected):
    result = classify(title)
    assert result["topicPolarity"] == expected
    assert result["actionable"] is False


@pytest.mark.parametrize(
    "title,expected",
    [("光伏亏损收窄", "NEGATIVE_RISK"), ("光伏产能出清", "UNDETERMINED")],
)
@pytest.mark.parametrize(
    "refs,sources,checked_ids",
    [
        (["s1", "s2"], [{"id": "s1", "verification": "SOURCE_CHECKED"}], ["s1"]),
        (
            ["s1"],
            [
                {"id": "s1", "verification": "SOURCE_CHECKED"},
                {"id": "s1", "verification": "SOURCE_CHECKED"},
            ],
            [],
        ),
        (
            ["s1", "s2"],
            [
                {"id": "s1", "verification": "SOURCE_CHECKED"},
                {"id": "s2", "verification": "UNVERIFIED"},
            ],
            ["s1"],
        ),
    ],
)
def test_reversal_requires_every_referenced_source_to_be_unique_and_checked(
    title, expected, refs, sources, checked_ids
):
    module = import_module("backend.macro.research_topic_polarity")
    result = module.classify_topic_polarity(
        {"title": title, "sourceIds": refs}, sources=sources
    )
    assert result["topicPolarity"] == expected
    assert result["polarityEvidenceSourceIds"] == checked_ids
    assert "SOURCE_SUPPORT_PENDING" in result["polarityReasonCodes"]
