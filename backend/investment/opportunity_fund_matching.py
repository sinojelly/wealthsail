"""Pure, explainable topic search hints and fund share-family identities."""

import re
import math

from backend.investment.discovery_limits import OPPORTUNITY_KEYWORDS_PER_TOPIC
from backend.investment.dynamic_discovery import _THEMES

ASSOCIATION_ORDER = {
    "DIRECT_INDEX": 0,
    "CATEGORY_MATCH": 1,
    "NAME_CANDIDATE": 2,
    "BROAD_FALLBACK": 3,
}


def fund_price_priority(fund):
    """Current technical position precedes descriptive association strength."""
    position = fund.get("pricePosition") or {}
    status = position.get("status")
    score = fund.get("quantitativeScore")
    score = score if type(score) in (int, float) and math.isfinite(score) else None
    category = (
        0 if status == "ENTRY_ZONE" else
        1 if status == "NEAR_ENTRY_ZONE" else
        3 if status == "OVERHEATED" else
        2 if status not in (None, "DATA_PENDING") and score is not None and score >= 70 else
        4
    )
    distance = position.get("distanceToEntryPct")
    distance = distance if type(distance) in (int, float) and math.isfinite(distance) else math.inf
    return (category, distance if category == 1 else 0, -(score if score is not None else -1))


def displayed_funds(funds, *, automatic_limit=3):
    """All explicit narrative identities survive the automatic recall quota."""
    if automatic_limit is None:
        return list(funds)
    result, automatic = [], 0
    for fund in funds:
        if fund.get("narrativeMention") is True:
            result.append(fund)
        elif automatic < automatic_limit:
            result.append(fund)
            automatic += 1
    return result
_SYNONYMS = (
    (r"光模块|光通信|\bCPO\b", ("光通信", "通信", "人工智能")),
    (r"电网设备|电网|特高压", ("电网设备", "特高压", "电力")),
    (r"固态电池|锂电|动力电池", ("电池", "锂电", "新能源车")),
    (r"创新药|生物医药|\bESMO\b", ("创新药", "医药生物", "生物医药")),
    (r"低空|无人机|\beVTOL\b", ("通用航空", "低空", "军工")),
    (r"人形机器人|具身智能|机器人", ("机器人", "高端装备")),
)
# Search hints cover the global taxonomy, including industries omitted by a
# particular run's collection/audit budget. They describe candidate products,
# never certify their portfolio exposure.
_INDUSTRY_HINTS = {
    "SEMICONDUCTOR_AI": ("半导体", "芯片", "算力"),
    "BANK": ("银行",), "BROKER": ("证券", "券商"),
    "FINTECH": ("金融科技",), "ACTIVE_GROWTH": ("成长", "科技"),
    "NONFERROUS": ("有色金属", "有色"), "DEFENCE": ("军工", "国防"),
    "COMMUNICATION": ("通信", "光通信"), "INNOVATIVE_DRUG": ("创新药", "生物医药"),
    "GOLD": ("黄金",), "GLOBAL_ENERGY": ("油气", "原油", "能源"),
    "US_TECH": ("纳斯达克", "美国科技"), "ROBOTICS_MOBILITY": ("机器人", "智能驾驶"),
    "RARE_EARTH_METALS": ("稀土", "有色"), "FINANCE_POLICY": ("银行", "证券"),
    "HEALTHCARE_CLINICAL": ("医药", "创新药"), "BROAD": ("沪深300", "中证A500"),
}
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

# Match the subject in the title before letting narrative context suggest
# funds. Explicit related industries remain research context, not exposure.
_TITLE_SCOPES = (
    (r"(?:海外|美国|美股|全球|纳斯达克|纳指).*?(?:科技|技术|成长|QDII)|纳斯达克|纳指", "US_TECH", ("美国科技", "美股科技", "纳斯达克", "纳指", "全球科技", "海外科技", "标普信息科技")),
    (r"港股科技|恒生科技", "HK_TECH", ("港股科技", "恒生科技")),
    (r"AI硬件|算力硬件|半导体|芯片|存储器|HBM", "SEMICONDUCTOR_AI", ("半导体", "芯片", "算力", "存储", "人工智能")),
    (r"金融科技|金融IT", "FINTECH", ("金融科技", "金融IT")),
    (r"银行", "BANK", ("银行",)),
    (r"券商|证券公司|证券保险", "BROKER", ("券商", "证券公司", "证券保险", "证券指数", "证券ETF")),
    (r"光模块|光通信|CPO|网络通信|通信", "COMMUNICATION", ("光模块", "光通信", "通信", "CPO")),
    (r"电网|特高压|电力", "POWER", ("电网", "特高压", "电力")),
    (r"汽车|新能源车|智能驾驶", "AUTOMOTIVE", ("汽车", "新能源车", "智能驾驶")),
    (r"机器人|具身智能", "ROBOT", ("机器人", "具身智能", "高端装备")),
    (r"消费|白酒|食品饮料", "CONSUMPTION", ("消费", "白酒", "食品饮料")),
    (r"宽基|沪深300|中证A500", "BROAD", ("沪深300", "中证A500", "中证500", "全指")),
)


def primary_topic_scope(topic):
    """Resolve the actual named subject, independent of examples in the thesis."""
    title = str(topic.get("title") or "")[:200]
    found = [(m.start(), scope, roots) for pattern, scope, roots in _TITLE_SCOPES
             if (m := re.search(pattern, title, re.IGNORECASE))]
    match = topic.get("match") if isinstance(topic.get("match"), dict) else {}
    industries = set(texts(match.get("industryIds")) or texts(topic.get("industryClaims")))
    if found:
        _, scope, roots = min(found, key=lambda row: row[0])
        # A registered storage subtype is inside the semiconductor subject.
        # Other claims may merely be a comparison or mentioned risk.
        subtypes = {"MEMORY"} if scope == "SEMICONDUCTOR_AI" else set()
        return {scope} | (industries & subtypes), roots
    # Sanitization may map an unrecognized claim to BROAD as an observation.
    # The placeholder alone cannot establish a fund's thematic exposure.
    industries.discard("BROAD")
    roots = tuple(dict.fromkeys(
        [word for scope, words in _INDUSTRY_HINTS.items() if scope in industries for word in words]
        + [word for scope, _, words in _THEMES if scope in industries for word in words]
    ))
    return industries, roots


def matches_primary_scope(scope, *, name, sector=None, index_name=None):
    industries, roots = scope
    if not industries and not roots:
        return False
    identity_text = f"{name or ''} {index_name or ''}".casefold()
    if "US_TECH" in industries and not any(root.casefold() in identity_text for root in roots):
        return False
    return sector in industries or any(root.casefold() in identity_text for root in roots)


def restrict_cached_primary_funds(recalled, topics, packet):
    """Recheck archived recall IDs before price enrichment; old caches stay usable."""
    sectors = _fund_sectors(packet)
    result = {**recalled}
    for topic in topics:
        key = topic.get("topicId") or topic.get("key")
        row = recalled.get(key)
        scope = primary_topic_scope(topic)
        if not isinstance(row, dict) or not any(scope):
            continue
        primary = []
        for fund in row.get("funds", []):
            # Old receipts encode verified index names in this server-generated
            # explanation. Narrative and name candidates cannot borrow it.
            index = re.search(r"匹配已核验跟踪指数「([^」]+)」", str(fund.get("matchReason") or "")) if fund.get("associationLevel") == "DIRECT_INDEX" else None
            if fund.get("primaryTopicMatch") is True or matches_primary_scope(
                scope, name=fund.get("name"), sector=sectors.get(fund.get("fundCode")),
                index_name=index[1] if index else None,
            ):
                primary.append(fund)
        result[key] = {**row, "funds": primary}
    return result


def _fund_sectors(packet):
    sectors = {}
    for row in (packet or {}).get("discoveryUniverse", []):
        if not isinstance(row, dict) or not isinstance(row.get("fundCode"), str):
            continue
        sector = row.get("discoverySector")
        if isinstance(sector, str):
            sectors.setdefault(row["fundCode"], set()).add(sector)
    return {code: next(iter(values)) for code, values in sectors.items() if len(values) == 1}


def annotate_topic_primary_funds(report, packet=None):
    """Project display associations without editing the saved price plans."""
    sectors = _fund_sectors(packet)

    def annotate(topic):
        if not isinstance(topic, dict):
            return topic
        scope = primary_topic_scope(topic)
        codes = []
        for plan in topic.get("plans") or topic.get("fundPlans") or []:
            if not isinstance(plan, dict):
                continue
            code = plan.get("fundCode") or plan.get("subjectId")
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
                continue
            sector = sectors.get(code, plan.get("discoverySector"))
            # Only a server-verified same-scope recall can rescue a product
            # whose name/sector does not spell out the tracked index.
            if plan.get("primaryTopicMatch") is True or matches_primary_scope(
                scope, name=plan.get("name") or plan.get("label"), sector=sector
            ):
                codes.append(code)
        return {**topic, "primaryFundCodes": list(dict.fromkeys(codes))}

    return {
        **report,
        **{field: [annotate(topic) for topic in report[field]]
           for field in ("researchTopics", "topicCatalog") if isinstance(report.get(field), list)},
    }


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


def mentioned_codes(topic):
    """Exact narrative codes are identity lookup hints, never product evidence."""
    from backend.macro.research_mentions import mentioned_fund_codes

    return [row["fundCode"] for row in mentioned_fund_codes({"topicCatalog": [topic]})]


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
    industry_hints = [
        word for code in texts(match.get("industryIds"))
        for word in _INDUSTRY_HINTS.get(code, ())
    ]
    hints = [
        keyword
        for code, pattern, keywords in _THEMES
        if re.search(pattern, text, re.IGNORECASE | re.ASCII)
        or code in texts(match.get("industryIds"))
        for keyword in keywords
    ]
    if re.search(r"芯片|半导体|SCI_TECH_CHIP", text, re.IGNORECASE):
        hints += ["芯片", "半导体"]
    candidates = roots + explicit + industry_hints + hints + _SPLIT.split(str(topic.get("title") or ""))
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
