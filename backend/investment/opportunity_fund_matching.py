"""Pure, explainable topic search hints and fund share-family identities."""

import re

from backend.investment.discovery_limits import OPPORTUNITY_KEYWORDS_PER_TOPIC
from backend.investment.dynamic_discovery import _THEMES

ASSOCIATION_ORDER = {
    "DIRECT_INDEX": 0,
    "CATEGORY_MATCH": 1,
    "NAME_CANDIDATE": 2,
    "BROAD_FALLBACK": 3,
}
_SYNONYMS = (
    (r"光模块|光通信|\bCPO\b", ("光通信", "通信", "人工智能")),
    (r"电网设备|电网|特高压", ("电网设备", "特高压", "电力")),
    (r"固态电池|锂电|动力电池", ("电池", "锂电", "新能源车")),
    (r"创新药|生物医药|\bESMO\b", ("创新药", "医药生物", "生物医药")),
    (r"低空|无人机|\beVTOL\b", ("通用航空", "低空", "军工")),
    (r"人形机器人|具身智能|机器人", ("机器人", "高端装备")),
)
_SPLIT = re.compile(
    r"[\s，,、。:：;；/()（）]+|国产替代|商业化|产业|行业|政策|新增|订单|需求|利好|受益|加速|增长|带动|推动"
)
_REGION = re.compile(
    r"美股|美国|美债|美元债|纳斯达克|标普|港股|香港|恒生|日本|日债|日经|东证|"
    r"欧洲|欧元区|欧债|欧股|德国|德债|法国|英国|英债|海外|国际|全球|境外|印度|"
    r"越南|东南亚|巴西|韩国|台湾|澳大利亚|加拿大|俄罗斯|新兴市场"
)
_FALLBACKS = (
    (r"黄金|贵金属", ("黄金",), "黄金资产"),
    (r"债券|国债|纯债|固收", ("国债", "纯债"), "债券资产"),
    (r"原油|油气|石油", ("油气", "原油"), "能源资产"),
    (r"美股|美国|纳斯达克|标普", ("标普500", "纳斯达克100"), "美国权益宽基"),
    (r"港股|香港|恒生", ("恒生指数", "恒生中国企业"), "港股宽基"),
    (r"日本|日经|东证", ("日经225", "日经指数", "日本东证"), "日本权益宽基"),
    (r"德国|DAX", ("德国DAX",), "德国权益宽基"),
    (r"法国|CAC40", ("法国CAC40",), "法国权益宽基"),
    (r"欧洲|欧元区|欧股", ("欧洲50", "欧洲精选", "欧洲股票"), "欧洲权益"),
)


def texts(value):
    return (
        [v for v in value[:32] if isinstance(v, str)] if isinstance(value, list) else []
    )


def topic_text(topic):
    match = topic.get("match") if isinstance(topic.get("match"), dict) else {}
    return " ".join(
        [
            str(topic.get(field) or "")[:4000]
            for field in ("title", "summary", "thesis", "impact")
        ]
        + texts(topic.get("industryClaims"))
        + texts(match.get("unresolvedClaims"))
    )


def regional_bond_terms(topic):
    """An overseas rate exposure cannot borrow a generic domestic bond link."""
    text = topic_text(topic)
    if not re.search(r"债|固收", text) or not _REGION.search(text):
        return None
    if re.search(r"美国|美债", text) and re.search(r"国债|美债", text):
        return ["美国国债", "美债"]
    return []


def has_overseas_region(topic):
    return bool(_REGION.search(topic_text(topic)))


def terms(topic):
    regional_bonds = regional_bond_terms(topic)
    if regional_bonds is not None:
        return regional_bonds
    match = topic.get("match") if isinstance(topic.get("match"), dict) else {}
    text = topic_text(topic)
    # Canonical roots precede narrative claims so eight long AI phrases cannot
    # crowd out the names actually used by public funds and indices.
    roots = [
        root
        for pattern, roots in _SYNONYMS
        if re.search(pattern, text, re.IGNORECASE | re.ASCII)
        for root in roots
    ]
    explicit = (
        [
            str(row.get("searchTerm") or "")
            for row in match.get("proposedScopes", [])[:8]
            if isinstance(row, dict)
        ]
        if isinstance(match.get("proposedScopes"), list)
        else []
    )
    explicit += texts(match.get("unresolvedClaims")) + texts(
        topic.get("industryClaims")
    )
    hints = [
        keyword
        for code, pattern, keywords in _THEMES
        if re.search(pattern, text, re.IGNORECASE | re.ASCII)
        or code in texts(match.get("industryIds"))
        for keyword in keywords
    ]
    if re.search(r"芯片|半导体|SCI_TECH_CHIP", text, re.IGNORECASE):
        hints += ["芯片", "半导体"]
    candidates = roots + explicit + hints + _SPLIT.split(str(topic.get("title") or ""))
    return list(
        dict.fromkeys(
            word.strip()
            for word in candidates
            if isinstance(word, str)
            and re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff·+ -]{2,32}", word.strip())
            and not word.strip().isdigit()
        )
    )[:OPPORTUNITY_KEYWORDS_PER_TOPIC]


def fallback(topic):
    regional_bonds = regional_bond_terms(topic)
    if regional_bonds is not None:
        return regional_bonds, "美国国债资产" if regional_bonds else None
    text = topic_text(topic)
    for pattern, keywords, label in _FALLBACKS:
        if re.search(pattern, text):
            return list(keywords), label
    if _REGION.search(text):
        return [], None
    domestic = re.search(r"国内|中国|A股|央行|国务院|财政部|人民银行", text)
    policy = re.search(
        r"降准|降息|宽松|货币政策|财政政策|财政刺激|经济刺激|稳增长|资本市场改革|提振股市",
        text,
    )
    positive = re.search(r"降准|降息|宽松|刺激|稳增长|利好|提振|支持|扩大|增加", text)
    negative = re.search(r"收紧|紧缩|加息|暴雷|违约|利空", text)
    if domestic and policy and positive and not negative:
        return ["中证A500", "沪深300", "中证500"], "国内宏观政策支持下的A股宽基"
    return [], None


def fund_group_key(name):
    """Strip share classes only; ETF, 联接 and 发起式 identify real products."""
    compact = re.sub(r"\s+", "", str(name or ""))
    return re.sub(
        r"(.*(?:[^A-Za-z]|ETF))[ACEIY](?:类|份额)?(?:[（(]人民币[)）]|人民币)?$",
        r"\1",
        compact,
        flags=re.IGNORECASE,
    ).casefold()


def share_class_priority(name):
    found = re.search(
        r"[ACEIY](?:类|份额)?(?:[（(]人民币[)）]|人民币)?$", name, re.IGNORECASE
    )
    return "ACEIY".index(found.group()[0].upper()) if found else 5
