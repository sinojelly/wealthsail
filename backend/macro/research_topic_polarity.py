"""Explainable display polarity for archived theses, never trading authority."""

from collections import Counter
import re

VERSION = "research-topic-polarity-v1"
_POSITIVE = re.compile(
    r"增长|大增|改善|突破|获批|投产|扭亏|盈利兑现|业绩兑现|需求回暖|订单回升|"
    r"政策利好|利好.{0,12}(?:权益|股市|行业|基金)|提振|支持.{0,12}(?:需求|采购)|"
    r"(?:需求|采购).{0,12}支持|(?:利润|订单|收入|价格).{0,8}上涨"
)
_NEGATIVE = re.compile(
    r"倒闭|裁员|砍单|产能过剩|价格战|爆雷|暴雷|违约|亏损|盈利恶化|"
    r"(?:利润|订单|收入|需求|盈利).{0,10}(?:下降|下滑|恶化|缩减|取消)|"
    r"(?:削减|取消|缩减).{0,8}订单"
)
_MACRO = re.compile(r"央行|利率|通胀|汇率|GDP|CPI|PMI|宏观|经济数据", re.IGNORECASE)
_NEGATED_CLAUSE = re.compile(
    r"(?:没有(?:发生)?|并未|尚未|未见|未发生|不构成|不能证明|不代表|并不|并非)[^，。；！？\n]{0,80}"
)
_RISK_EASING = re.compile(
    r"(?:产能过剩|价格战|违约|倒闭|裁员)(?:风险)?(?:已|已经|显著|持续|逐步)?(?:下降|缓解|消退|降低|改善)"
)


def classify_topic_polarity(topic, *, sources=()):
    """Classify the topic's own economic claim, keeping source support explicit.

    A linked source's unrelated sentences must not change a topic's direction.
    Unknown text stays unknown; the classifier does not infer fund exposure.
    """
    text = "。".join(
        str(topic.get(key) or "")[:4000] for key in ("title", "summary", "thesis")
    )
    text = _NEGATED_CLAUSE.sub("", text)
    text = _RISK_EASING.sub("", text)
    positive, negative = bool(_POSITIVE.search(text)), bool(_NEGATIVE.search(text))
    counts = Counter(source.get("id") for source in sources if isinstance(source, dict))
    refs = set(topic.get("sourceIds") or [])
    checked = sorted(
        {
            source["id"]
            for source in sources
            if isinstance(source, dict)
            and source.get("id") in refs
            and counts[source["id"]] == 1
            and source.get("verification") == "SOURCE_CHECKED"
        }
    )
    backed = bool(refs) and refs <= set(checked)
    if negative:
        polarity = "MIXED" if positive and backed else "NEGATIVE_RISK"
        reason = (
            "主题同时包含正向假设与经营恶化信号，应并列核对分歧。"
            if polarity == "MIXED"
            else "主题包含经营恶化或负面风险信号，相关基金仅作风险涉及对象。"
        )
        codes = [
            "POSITIVE_AND_NEGATIVE_CLAIMS"
            if polarity == "MIXED"
            else "NEGATIVE_ECONOMIC_CLAIM"
        ]
    elif positive and backed:
        polarity, reason = (
            "POSITIVE_OPPORTUNITY",
            "主题具有明确正向经济假设及可核验来源引用，仍不代表投资资格。",
        )
        codes = ["SOURCED_POSITIVE_HYPOTHESIS"]
    elif _MACRO.search(text):
        polarity, reason = (
            "MACRO_OBSERVATION",
            "主题主要描述宏观变化，尚不自动等同于某类基金的正向机会。",
        )
        codes = ["MACRO_CONTEXT_ONLY"]
    else:
        polarity, reason = (
            "UNDETERMINED",
            "主题的正向受益方向或来源支持尚不明确，保留为研究观察。",
        )
        codes = ["ECONOMIC_DIRECTION_UNDETERMINED"]
    reasons = [reason]
    if not backed:
        codes.append("SOURCE_SUPPORT_PENDING")
        reasons.append("尚缺同主题可核验来源支持，分类仅为文字信号分流。")
    return {
        "topicPolarity": polarity,
        "polarityReasons": reasons,
        "polarityReasonCodes": codes,
        "polarityEvidenceSourceIds": checked,
        "polarityVersion": VERSION,
        "actionable": False,
    }
