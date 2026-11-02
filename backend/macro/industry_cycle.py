"""Evidence-backed industry stages and an explicit counter-evidence audit.

The model proposes classifications; source identities and verification remain
server controlled. A missing search result is a gap, never proof of safety.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta
import re
import unicodedata
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from backend.macro.industry_evidence_time import event_time_verification
from backend.macro.research_diagnostics import RULE_IDS

RISK_CHECKS = {
    "FINANCING_COOLING": "一级市场融资降温/断代",
    "LAYOFFS_CLOSURES": "企业欠薪/裁员/倒闭",
    "ORDER_CUTS": "下游砍单",
    "OVERCAPACITY_PRICE_WAR": "产能过剩/价格战",
    "PRODUCTION_DELAY": "商业化量产推迟",
}
EARNINGS_RISK_FLAGS = frozenset(
    {
        "EARLY_COMMERCIALIZATION",
        "NO_EARNINGS_DELIVERY",
        "NO_CURRENT_EARNINGS_PROOF",
        "FORWARD_PLANS_ONLY",
    }
)
LEGACY_CONFIRMED_RISK_FLAGS = frozenset(
    {"DEBUBBLING", "LEGACY_CONFIRMED_RISK", "CONFIRMED_RISK"}
)
COMPANY_EARNINGS_INDUSTRY_WATCH_NOTE = (
    "个别企业当期盈利已核实，但近14日行业级风险观察尚未到期解除"
)
STAGE_LABELS = {
    "EARNINGS_DELIVERY": "🟢 业绩兑现期",
    "POLICY_CATALYST": "🟡 政策/事件催化期",
    "EARLY_COMMERCIALIZATION": "🟠 商业化早期·去伪存真期",
    "UNKNOWN": "产业兑现阶段待核查",
}


class IndustryCycleValidationError(ValueError):
    """A deterministic industry rule failure, separate from schema failures."""

    def __init__(self, rule_id: str, message: str = ""):
        if not isinstance(rule_id, str) or rule_id not in RULE_IDS:
            raise ValueError("invalid industry diagnostic rule id")
        super().__init__(message or rule_id)
        self.rule_id = rule_id


# Scope aliases include common suppliers and customers; they identify the claim's
# subject, not its maturity, valuation or investment merits.
_INDUSTRY_SCOPE_TERMS = {
    "ROBOT": (
        "机器人",
        "具身",
        "人形",
        "减速器",
        "丝杠",
        "灵巧手",
        "伺服",
        "robot",
        "robots",
        "robotics",
        "embodied",
    ),
    "ROBOTICS_MOBILITY": (
        "机器人",
        "具身",
        "人形",
        "减速器",
        "丝杠",
        "灵巧手",
        "伺服",
        "智能驾驶",
        "自动驾驶",
        "智驾",
        "robot",
        "robotics",
        "embodied",
        "autonomous driving",
    ),
    "SEMICONDUCTOR_AI": (
        "半导体",
        "芯片",
        "晶圆",
        "光刻",
        "先进封装",
        "算力",
        "semiconductor",
        "semiconductors",
        "chip",
        "chips",
        "wafer",
        "foundry",
        "ai computing",
    ),
    "AI": (
        "人工智能",
        "算力",
        "大模型",
        "数据中心",
        "ai",
        "artificial intelligence",
        "large language model",
        "data center",
        "gpu",
    ),
    "RARE_EARTH": (
        "稀土",
        "永磁",
        "磁材",
        "有色金属",
        "rare earth",
        "rare-earth",
        "permanent magnet",
        "nonferrous",
    ),
    "FINANCIAL": (
        "银行",
        "券商",
        "证券",
        "保险",
        "金融",
        "bank",
        "banks",
        "banking",
        "brokerage",
        "insurance",
        "financial",
    ),
    "FINANCE_POLICY": (
        "银行",
        "券商",
        "证券",
        "保险",
        "金融政策",
        "金融",
        "bank",
        "banking",
        "brokerage",
        "insurance",
        "financial policy",
    ),
    "DRUG": (
        "创新药",
        "生物医药",
        "制药",
        "医药",
        "药物",
        "biopharma",
        "pharmaceutical",
        "drug",
        "drugs",
        "biotech",
    ),
    "HEALTHCARE_CLINICAL": (
        "医疗",
        "医药",
        "临床",
        "创新药",
        "生物医药",
        "医疗器械",
        "clinical",
        "healthcare",
        "medical",
        "biopharma",
    ),
    "GOLD_ENERGY": (
        "黄金",
        "能源",
        "原油",
        "油气",
        "石油",
        "天然气",
        "gold",
        "energy",
        "crude oil",
        "oil",
        "natural gas",
    ),
    "AEROSPACE": (
        "航空航天",
        "航空",
        "航天",
        "军工",
        "国防",
        "aerospace",
        "aviation",
        "defense",
        "defence",
    ),
    "CONSUMPTION": (
        "消费",
        "食品",
        "饮料",
        "白酒",
        "乳业",
        "零售",
        "家电",
        "consumer",
        "consumption",
        "retail",
        "beverage",
        "liquor",
    ),
    "MEMORY": ("存储", "存储芯片", "内存", "闪存", "memory", "dram", "nand", "hbm"),
    "PCB": (
        "印制电路板",
        "印刷电路板",
        "覆铜板",
        "电路板",
        "pcb",
        "printed circuit",
        "copper clad laminate",
    ),
    "POWER": (
        "电力",
        "电网",
        "发电",
        "输配电",
        "电气设备",
        "光伏",
        "风电",
        "power grid",
        "electricity",
        "electric power",
        "photovoltaic",
        "solar",
    ),
    "SOLID_BATTERY": (
        "固态电池",
        "固态锂电",
        "固态电解质",
        "锂电池",
        "solid state battery",
        "solid-state battery",
        "solid electrolyte",
        "lithium battery",
    ),
    "SATELLITE": (
        "卫星",
        "卫星互联网",
        "商业航天",
        "低轨",
        "satellite",
        "satellites",
        "space internet",
    ),
    "LOW_ALTITUDE": (
        "低空",
        "无人机",
        "飞行汽车",
        "通航",
        "low altitude",
        "low-altitude",
        "evtol",
        "drone",
        "drones",
    ),
    "HK_TECH": (
        "港股科技",
        "恒生科技",
        "港股互联网",
        "香港科技",
        "hong kong tech",
        "hang seng tech",
    ),
    "DIVIDEND": (
        "红利",
        "高股息",
        "低波",
        "dividend",
        "dividends",
        "high yield",
        "low volatility",
    ),
    "TRADITIONAL_MEDICINE": (
        "中药",
        "中医药",
        "中成药",
        "traditional chinese medicine",
        "chinese medicine",
        "tcm",
    ),
    "COAL": (
        "煤炭",
        "焦煤",
        "焦炭",
        "动力煤",
        "煤矿",
        "coal",
        "coking coal",
        "thermal coal",
    ),
    "GAMING": (
        "游戏",
        "电竞",
        "手游",
        "端游",
        "gaming",
        "video game",
        "video games",
        "esports",
    ),
    "CHEMICAL": (
        "化工",
        "化学",
        "石化",
        "农化",
        "聚氨酯",
        "氟化工",
        "chemical",
        "chemicals",
        "petrochemical",
        "agrochemical",
    ),
}


def _scope_term_present(text, term):
    if term.isascii():
        return bool(
            re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text, re.I)
        )
    return term in text


def _expected_scope_terms(expected_industries):
    expected = (
        [expected_industries]
        if isinstance(expected_industries, str)
        else (expected_industries or [])
    )
    codes = [
        name.strip().upper()
        for name in expected
        if isinstance(name, str) and name.strip().upper() in _INDUSTRY_SCOPE_TERMS
    ]
    if codes:
        # Structured sector IDs take precedence over prose titles with aliases
        # shared by broader combined themes (for example ROBOTICS_MOBILITY).
        return {term for code in codes for term in _INDUSTRY_SCOPE_TERMS[code]}
    terms = set()
    for name in expected:
        if not isinstance(name, str) or not name.strip():
            continue
        name = unicodedata.normalize("NFKC", name).strip()
        if name.upper() in _INDUSTRY_SCOPE_TERMS:
            terms.update(_INDUSTRY_SCOPE_TERMS[name.upper()])
            continue
        matched = [
            aliases
            for aliases in _INDUSTRY_SCOPE_TERMS.values()
            if any(_scope_term_present(name, term) for term in aliases)
        ]
        if matched:
            terms.update(term for aliases in matched for term in aliases)
        else:
            # Keep whole Chinese roots, never one-character or generic labels.
            terms.update(
                root
                for root in re.split(
                    r"产业链|行业|产业|赛道|方向|机会|主题|价值|投资|基金|[\s、/·:：()（）与和]+",
                    name,
                )
                if re.fullmatch(r"[\u4e00-\u9fff]{2,}", root)
            )
    return terms


def _industry_subjects(quote):
    # Scan in order, consuming each suffix before looking for the next subject.
    # Bare 业 in 企业/商业/营业 or an organization name is not a sector marker.
    return re.findall(
        r"([\u4e00-\u9fff]{1,8}?)(产业链|行业|产业|赛道|领域|(?<![行产企商营])业)"
        r"(?!股份|有限|集团|公司)",
        quote,
    )


def _industry_scope_verified(source, expected_industries):
    """Verify a claim's industry; an unrelated title cannot override its subject.

    Callers pass the actual supporting statement as quote. Only an explicit
    context reference can use a title; an unknown named industry cannot.
    """
    terms = _expected_scope_terms(expected_industries)
    if not terms:
        return False
    quote = unicodedata.normalize("NFKC", str(source.get("quote") or ""))
    if any(
        not subject.endswith(("该", "本", "上述", "这一", "这个", "此"))
        and not any(_scope_term_present(subject + suffix, term) for term in terms)
        for subject, suffix in _industry_subjects(quote)
    ):
        return False
    if any(_scope_term_present(quote, term) for term in terms):
        if any(
            term not in terms
            and not any(_scope_term_present(target, term) for target in terms)
            and _scope_term_present(quote, term)
            for aliases in _INDUSTRY_SCOPE_TERMS.values()
            for term in aliases
        ):
            return False
        return True
    if any(
        _scope_term_present(quote, term)
        for aliases in _INDUSTRY_SCOPE_TERMS.values()
        for term in aliases
    ):
        return False
    if not re.search(
        r"(?:该|本|上述|这一|这个|此)(?:行业|产业)|\b(?:this|the same) industry\b",
        quote,
        re.I,
    ):
        return False
    return any(
        _scope_term_present(str(source.get("title") or ""), term) for term in terms
    )


class IndustryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal[
        "PROFIT_GROWTH",
        "CASH_COLLECTION",
        "PAID_ORDERS",
        "POLICY_EVENT",
        "COMMERCIALIZATION_EARLY",
        "EARNINGS_NOT_DELIVERED",
        "EXPANSION_PLAN",
        "FRAMEWORK_AGREEMENT",
    ]
    summary: str = Field(min_length=1, max_length=240)
    sourceIds: list[str] = Field(min_length=1, max_length=8)
    companyName: str | None = Field(default=None, min_length=1, max_length=120)
    entityId: str | None = Field(default=None, min_length=1, max_length=80)
    reportPeriod: str | None = Field(default=None, min_length=1, max_length=40)


class IndustryRiskCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal[
        "FINANCING_COOLING",
        "LAYOFFS_CLOSURES",
        "ORDER_CUTS",
        "OVERCAPACITY_PRICE_WAR",
        "PRODUCTION_DELAY",
    ]
    status: Literal["FOUND", "NOT_FOUND", "UNVERIFIED"]
    query: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=300)
    sourceIds: list[str] = Field(max_length=8)


class IndustryCycleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stage: Literal[
        "EARNINGS_DELIVERY", "POLICY_CATALYST", "EARLY_COMMERCIALIZATION", "UNKNOWN"
    ]
    evidenceSummary: str = Field(min_length=1, max_length=600)
    sourceIds: list[str] = Field(max_length=8)
    evidence: list[IndustryEvidence] = Field(max_length=8)
    riskChecks: list[IndustryRiskCheck] = Field(min_length=5, max_length=5)


class IndustryCycleReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    industry: str = Field(min_length=1, max_length=100)
    sourceIds: list[str] = Field(max_length=8)
    riskChecks: list[IndustryRiskCheck] = Field(min_length=5, max_length=5)


def _source_recency(source, now):
    raw = source.get("publishedDate") or source.get("publishedAt")
    try:
        published = date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return "UNVERIFIED"
    if now is None:
        return "DATED"
    today = now.date() if isinstance(now, datetime) else now
    age = (today - published).days
    return "RECENT" if 0 <= age <= 90 else "HISTORICAL" if age > 90 else "FUTURE"


_CLAUSE_CONNECTORS = (
    r"但是|然而|不过|其中|与此同时|同时|此外|另外|反观|相比之下|但|而(?!非)|却"
    r"|\b(?:but|however|whereas|while|meanwhile)\b"
)


def _separate_industry_facts(quote):
    # A completed predicate distinguishes joined facts from joined subjects.
    # Keep e.g. "机器人与半导体行业" and "丝杠与减速器产业链" intact.
    def separate(match):
        fact = match["fact"]
        complete = "景气向好" in fact or any(
            pattern.search(fact) for pattern in _RISK_QUOTE_PATTERNS.values()
        )
        return fact + ("，" if complete else "与")

    return re.sub(
        r"(?P<fact>(?:行业|产业|赛道|领域)[^，,。；;\n与]+)与"
        r"(?=[\u4e00-\u9fff]{2,8}(?:行业|产业|赛道|领域|业))",
        separate,
        str(quote or ""),
    )


def _quote_clauses(quote):
    return re.split(
        r"[，。；;\n]|(?<!\d),(?!\d)|(?<=[A-Za-z])\.(?=\s+[A-Z])"
        + "|"
        + _CLAUSE_CONNECTORS,
        _separate_industry_facts(quote),
        flags=re.I,
    )


def _profit_growth_supported(clause):
    if re.search(
        r"下降|下滑|下跌|减少|亏损|负增长|缩水|预亏|续亏|首亏|暴跌|腰斩|转亏|由盈转亏|\b(?:fell|fall\w*|declin\w*|decreas\w*|drop\w*|shrunk|slump\w*|loss(?:es)?)\b",
        clause,
        re.I,
    ):
        return False
    if re.search(r"(?:增长|增加|grew|increas\w*).{0,8}[-−]\s*\d", clause, re.I):
        return False
    for match in re.finditer(
        r"(?:归母净利润|扣非净利润|净利润|归母净利|扣非净利|净利|营业利润|利润总额|利润|net profit|net income|\bprofit\b|\bearnings\b)"
        r"(?P<between>[^。；;\n]{0,24}?)(?:增长|增加|扭亏|上升|\bgrew\b|\bgrowth\b|\brose\b|\bincreased?\b)",
        clause,
        re.I,
    ):
        if not re.search(
            r"营收|营业收入|营业额|销售收入|销售额|现金流|毛利率|产能|产量|总资产|资产|总负债|负债|\b(?:revenue|sales|turnover|margin|capacity|production|assets|liabilities)\b|cash flow",
            match["between"],
            re.I,
        ):
            return True
    return False


def _earnings_fact_clauses(source, kind, now):
    if source.get("verification") != "SOURCE_CHECKED" or _source_recency(
        source, now
    ) not in {"RECENT", "DATED"}:
        return []
    if re.search(
        r"否认|不属实|传闻不实|\bdenied\b", str(source.get("quote") or ""), re.I
    ):
        return []
    # Validate a minimal factual statement in the checked quote, rather than
    # trusting an LLM's evidence.kind or its paraphrased summary.
    matches = []
    for index, clause in enumerate(_quote_clauses(source.get("quote"))):
        if re.search(
            r"预计|预期|预测|计划|拟|有望|力争|目标|将|明年|未来|尚无|尚未|未实现|未收到|未回款|无实际|没有|未增长|否认|不属实|并非|传闻不实"
            r"|\b(?:expect\w*|forecast\w*|planned|planning|will|aims|could)\b|\bnot\b|\bno cash\b",
            clause,
            re.I,
        ):
            continue
        if kind == "PROFIT_GROWTH" and _profit_growth_supported(clause):
            matches.append((index, clause.strip()))
        if kind == "CASH_COLLECTION" and re.search(
            r"实际回款|已回款|已.{0,6}收到.{0,12}货款|收到.{0,12}客户.{0,8}货款|客户.{0,12}已.{0,8}回款"
            r"|销售商品.{0,12}收到.{0,8}现金|cash (?:collected|received)|collected.{0,20}(?:cash|payment)|paid customer orders",
            clause,
            re.I,
        ):
            matches.append((index, clause.strip()))
    return matches


def _earnings_fact(source, kind, now):
    return bool(_earnings_fact_clauses(source, kind, now))


def _report_periods(text):
    """Recognize explicit reporting periods, without inferring one from dates."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    periods = set()
    for match in re.finditer(
        r"(?<!\d)(20\d{2})\s*(?:[- ]?Q([1-4])|年?第?([一二三四1-4])季度)",
        text,
        re.I,
    ):
        number = match[2] or match[3]
        quarter = number if number.isdigit() else str("一二三四".index(number) + 1)
        periods.add(f"{match[1]}Q{quarter}")
    for match in re.finditer(
        r"(?<!\d)(20\d{2})\s*(?:[- ]?H([12])|年?(上|下)半年)", text, re.I
    ):
        half = match[2] or ("1" if match[3] == "上" else "2")
        periods.add(f"{match[1]}H{half}")
    for match in re.finditer(
        r"(?<!\d)(20\d{2})\s*(?:[- ]?FY|年?(?:全年|年度))", text, re.I
    ):
        periods.add(f"{match[1]}FY")
    return periods


def _fact_identity(row, clause):
    """Require identity and period in the matched fact, not another firm's text.

    Conservative explicit facts are intentional: ambiguous article-wide headers
    or pronouns cannot establish that two financial statements share an entity.
    """
    name = str(row.get("companyName") or "").strip()
    entity = str(row.get("entityId") or "").strip()
    if name and name.casefold() in {
        "公司",
        "该公司",
        "本公司",
        "企业",
        "该企业",
        "本企业",
        "我司",
        "集团",
        "本集团",
        "该集团",
        "company",
        "the company",
        "corporation",
        "enterprise",
    }:
        return None
    if not name and not entity:
        return None
    if name and (len(name) < 3 or name.casefold() not in clause.casefold()):
        return None
    if name:
        subject_start = clause.casefold().index(name.casefold())
        # A shared suffix inside two longer legal names is not their identity.
        if subject_start and clause[subject_start - 1].isalnum():
            return None
        subject_end = subject_start + len(name)
        metric = re.search(
            r"净利润|净利|营业利润|利润总额|利润|net profit|net income|\bprofit\b|\bearnings\b"
            if row["kind"] == "PROFIT_GROWTH"
            else r"实际回款|已回款|回款|货款|现金|cash|payment|paid customer orders",
            clause,
            re.I,
        )
        if (
            metric is None
            or metric.start() < subject_end
            or re.search(
                r"公司|企业|集团|\b(?:company|corporation|inc|ltd)\b",
                clause[subject_end : metric.start()],
                re.I,
            )
        ):
            return None
    if entity:
        identifier = re.search(
            r"(?<![\w])" + re.escape(entity) + r"(?![\w])", clause, re.I
        )
        metric = re.search(
            r"净利润|净利|营业利润|利润总额|利润|net profit|net income|\bprofit\b|\bearnings\b"
            if row["kind"] == "PROFIT_GROWTH"
            else r"实际回款|已回款|回款|货款|现金|cash|payment|paid customer orders",
            clause,
            re.I,
        )
        if (
            len(entity) < 4
            or identifier is None
            or metric is None
            or identifier.end() > metric.start()
        ):
            return None
    proposed_periods = _report_periods(row.get("reportPeriod"))
    if len(proposed_periods) != 1 or _report_periods(clause) != proposed_periods:
        return None
    return name, entity, next(iter(proposed_periods))


def _same_earnings_identity(profit, cash):
    name, entity, period = profit
    other_name, other_entity, other_period = cash
    if period != other_period:
        return False
    # Two stated, conflicting identities must never be joined via one alias.
    if name and other_name and name.casefold() != other_name.casefold():
        return False
    if entity and other_entity and entity.casefold() != other_entity.casefold():
        return False
    return bool(
        (name and other_name and name.casefold() == other_name.casefold())
        or (entity and other_entity and entity.casefold() == other_entity.casefold())
    )


_RISK_QUOTE_PATTERNS = {
    "FINANCING_COOLING": re.compile(
        r"(?:融资|募资).{0,16}(?:降温|断代|断层|收缩|减少|下降|下滑|萎缩|放缓|低迷|困难|枯竭|中断|锐减|骤降|回落)"
        r"|(?:funding|financing|fundrais\w*).{0,35}(?:fell|fall\w*|drop\w*|declin\w*|cool\w*|slow\w*|contract\w*|dried)",
        re.I,
    ),
    "LAYOFFS_CLOSURES": re.compile(
        r"欠薪|拖欠(?:工资|薪资)|裁员|倒闭|破产|停业|关停|layoffs?|laying off|unpaid wages|wage arrears|bankrupt\w*|shut(?:ting)? down",
        re.I,
    ),
    "ORDER_CUTS": re.compile(
        r"砍单|削单|(?:订单|采购).{0,12}(?:取消|减少|下降|下滑|回落)|取消.{0,10}订单|(?:cancel\w*|cut|reduc\w*).{0,20}orders|orders.{0,20}(?:cut|cancel\w*|fell|declin\w*)",
        re.I,
    ),
    "OVERCAPACITY_PRICE_WAR": re.compile(
        r"产能过剩|过剩产能|价格战|降价竞争|price wars?|overcapacity|excess capacity",
        re.I,
    ),
    "PRODUCTION_DELAY": re.compile(
        r"(?:量产|商业化|交付|验收).{0,12}(?:推迟|延期|延后|延迟)|(?:推迟|延期|延后|延迟).{0,12}(?:量产|商业化|交付|验收)|(?:production|commerciali[sz]ation|deliver\w*).{0,25}(?:delay\w*|postpon\w*|defer\w*)",
        re.I,
    ),
}


def _old_quote_year(clause, source, now):
    reference = now.year if isinstance(now, (date, datetime)) else None
    if reference is None:
        try:
            reference = date.fromisoformat(str(source.get("publishedDate"))[:10]).year
        except ValueError:
            return False
    years = {
        int(year) for year in re.findall(r"(?<!\d)((?:19|20)\d{2})(?:年|\b)", clause)
    }
    return bool(years) and years.isdisjoint({reference, reference - 1})


def _risk_quote_clauses(source, kind, now=None):
    """Check the quoted claim, separately from checking the original document."""
    pattern = _RISK_QUOTE_PATTERNS.get(kind)
    if pattern is None:
        return []
    if re.search(
        r"否认|传闻不实|不属实|消息不实|说法不实|\bdenied\b|rumou?r.{0,20}(?:false|untrue|denied)",
        str(source.get("quote") or ""),
        re.I,
    ):
        return []
    matches = []
    for clause in _quote_clauses(source.get("quote")):
        if re.search(
            r"预计|预期|预测|可能|或将|担忧|拟|计划|传闻|网传|谣言|\b(?:expect\w*|forecast\w*|could|may|might|potential|rumou?r\w*)\b",
            clause,
            re.I,
        ):
            continue
        if re.search(
            r"个别|单家|一家(?:公司|企业)|某家|\bisolated\b|\ba single\b|\bone company\b",
            clause,
            re.I,
        ):
            continue
        if re.search(
            r"没有|不存在|否认|并非|并未|未曾|未(?:出现|发生|有|见|遭遇|下降|降温|减少|下滑|砍单|推迟|延期|裁员|欠薪|倒闭)|\b(?:no|not|denied?|without)\b",
            clause,
            re.I,
        ):
            continue
        if kind == "FINANCING_COOLING" and re.search(
            r"融资(?:成本|利率|利息|费用)", clause
        ):
            continue
        if pattern.search(clause):
            matches.append(clause.strip())
    return matches


def _risk_quote_supported(source, kind, now=None):
    return bool(_risk_quote_clauses(source, kind, now))


def _risk_event_contexts(source, kind, now):
    # A date-only header immediately before a claim is its context. A later
    # product announcement, including one after a comma, cannot date that claim.
    contexts = []
    for sentence in re.split(
        r"[。；;\n]|(?<=[A-Za-z])\.(?=\s+[A-Z])",
        _separate_industry_facts(source.get("quote")),
    ):
        parts = [
            part.strip()
            for part in re.split(
                r"，|(?<!\d),(?!\d)|" + _CLAUSE_CONNECTORS, sentence, flags=re.I
            )
            if part.strip()
        ]
        for index, part in enumerate(parts):
            if not _risk_quote_supported({**source, "quote": part}, kind, now):
                continue
            previous = parts[index - 1].strip() if index else ""
            if previous and re.fullmatch(
                r"[\d\s年月日第季度一二三四上下本近期近日至到~～/\-]+", previous
            ):
                contexts.append(previous + "，" + part)
            elif re.match(r"(?:该|本|上述|这一|这个|此)(?:行业|产业|赛道|领域)", part):
                # Resolve an explicit same-industry reference from its adjacent
                # subject/date header, without borrowing unrelated predicates.
                antecedent = re.match(r".+(?:行业|产业|赛道|领域)", previous)
                contexts.append((antecedent[0] + "，" if antecedent else "") + part)
            else:
                contexts.append(part)
    return contexts


def _early_quote_clauses(source, kind, now):
    # Stage remains an evidence-backed classification with half protection;
    # it is not a confirmed industry downturn claim.
    if (
        kind not in {"COMMERCIALIZATION_EARLY", "EARNINGS_NOT_DELIVERED"}
        or source.get("verification") != "SOURCE_CHECKED"
        or _source_recency(source, now) != "RECENT"
    ):
        return []
    quote = str(source.get("quote") or "")
    if re.search(r"否认|不属实|传闻不实|并非|\bdenied\b", quote, re.I):
        return []
    pattern = (
        r"(?:仍处|处于|尚处|正在|仍在).{0,10}(?:试制|验证|试验|试产|商业化早期)"
        r"|(?:尚未|还未|未能|未实现).{0,8}(?:规模量产|量产|商业化)"
        if kind == "COMMERCIALIZATION_EARLY"
        else r"(?:尚未|还未|未能|未实现|尚无|没有|未).{0,8}(?:盈利|利润|实际回款|回款)"
    )
    matches = []
    for clause in _quote_clauses(quote):
        if _old_quote_year(clause, source, now) or re.search(
            r"预计|预期|预测|计划|拟|有望|力争|目标|将|未来|\b(?:expect\w*|plan\w*|will)\b",
            clause,
            re.I,
        ):
            continue
        if re.search(
            r"(?:不|未)(?:处于|处在|属于)|已经?完成.{0,8}(?:规模量产|商业化)", clause
        ):
            continue
        if re.search(pattern, clause, re.I):
            matches.append(clause.strip())
    return matches


def _early_quote_supported(source, kind, now):
    return bool(_early_quote_clauses(source, kind, now))


def _title_fingerprint(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(
        r"^(?:(?:转载|转发|快讯|独家|reprint|reposted)\s*[:：\-]?\s*)+", "", text
    )
    return "".join(char for char in text if char.isalnum())


def _independent_source_count(refs, sources, now):
    """Count distinct hosts and distinct titles, not syndication or URL aliases."""
    by_host = {}
    for ref in refs:
        source = sources.get(ref, {})
        if _source_recency(source, now) in {"HISTORICAL", "FUTURE"}:
            continue
        try:
            uri = urlsplit(source.get("url", ""))
            if (
                uri.scheme != "https"
                or not uri.hostname
                or uri.username
                or uri.password
                or uri.port not in {None, 443}
            ):
                continue
            host = uri.hostname.lower().rstrip(".").removeprefix("www.")
        except (ValueError, TypeError):
            continue
        title = _title_fingerprint(source.get("title"))
        # Missing titles cannot establish two independent originals.
        by_host.setdefault(host, set()).add(title or "__UNKNOWN_TITLE__")
    # Maximum host/title matching prevents greedy order from undercounting a
    # publisher with both an independent story and a shared syndicated title.
    title_hosts = {}

    def assign(host, visited):
        for title in sorted(by_host[host]):
            if title in visited:
                continue
            visited.add(title)
            if title not in title_hosts or assign(title_hosts[title], visited):
                title_hosts[title] = host
                return True
        return False

    return sum(assign(host, set()) for host in sorted(by_host))


_RISK_QUERY_TERMS = {
    "FINANCING_COOLING": ("融资", "降温", "断代", "funding", "financing"),
    "LAYOFFS_CLOSURES": (
        "欠薪",
        "裁员",
        "倒闭",
        "破产",
        "layoff",
        "closure",
        "bankrupt",
    ),
    "ORDER_CUTS": (
        "砍单",
        "订单",
        "削单",
        "order cut",
        "cancelled orders",
        "canceled orders",
    ),
    "OVERCAPACITY_PRICE_WAR": ("产能过剩", "价格战", "overcapacity", "price war"),
    "PRODUCTION_DELAY": (
        "量产",
        "商业化",
        "推迟",
        "延期",
        "commercialization",
        "production delay",
    ),
}


def _valid_not_found_query(check):
    query = unicodedata.normalize("NFKC", check.get("query", "")).casefold()
    if sum(char.isalnum() for char in query) < 4:
        return False
    terms = _RISK_QUERY_TERMS.get(check["kind"], ())
    if not any(term in query for term in terms):
        return False
    residual = query
    for term in terms:
        residual = residual.replace(term, " ")
    residual = re.sub(
        r"近\s*\d+\s*天|\d+|近期|最近|近期|today|recent\w*|past|days|months|years|\band\b|\bor\b",
        " ",
        residual,
    )
    return sum(char.isalnum() for char in residual) >= 2


def normalize_industry_cycle(
    raw,
    *,
    source_aliases=None,
    sources=None,
    require_checks=False,
    now=None,
    expected_industries=None,
):
    """Normalize a model proposal using only current validated source identities.

    ``sources`` contains the report validator's server-produced source proofs.
    Media evidence remains usable with its actual verification state; a green
    earnings label needs checked profit *and* checked cash collection evidence.
    """
    if raw is None:
        if require_checks:
            raise IndustryCycleValidationError(
                "INDUSTRY_RISK_CHECK_MISSING",
                "each direction requires industry counter-evidence checks",
            )
        return {
            "stage": "UNKNOWN",
            "label": STAGE_LABELS["UNKNOWN"],
            "riskFlags": [],
            "sourceIds": [],
            "evidence": [],
            "riskChecks": [],
            "evidenceSummary": "旧报告未执行产业周期反方核查，等待本轮研究补齐。",
            "researchStatus": "INCOMPLETE",
            "auditStructureStatus": "INCOMPLETE",
            "evidenceGaps": list(RISK_CHECKS),
            "searchExecutionVerified": False,
            **industry_risk_policy({"riskLevel": "NONE"}),
        }
    cycle = IndustryCycleProposal.model_validate(raw).model_dump()
    aliases = source_aliases or {}
    sources = sources or {}

    def references(refs):
        if any(ref not in aliases for ref in refs):
            raise IndustryCycleValidationError(
                "INDUSTRY_CYCLE_SOURCE_INVALID",
                "unknown industry-cycle source reference",
            )
        return sorted({aliases[ref] for ref in refs})

    refs = references(cycle["sourceIds"])
    seen = set()
    flags = set()
    confirmed_flags, suspected_flags = set(), set()
    gaps = []
    for row in [*cycle["evidence"], *cycle["riskChecks"]]:
        row["sourceIds"] = references(row["sourceIds"])
        if any(ref not in refs for ref in row["sourceIds"]):
            raise IndustryCycleValidationError(
                "INDUSTRY_CYCLE_SOURCE_INVALID",
                "industry evidence must cite its direction cycle sources",
            )
        row["verification"] = (
            "SOURCE_CHECKED"
            if row["sourceIds"]
            and all(
                sources.get(ref, {}).get("verification") == "SOURCE_CHECKED"
                for ref in row["sourceIds"]
            )
            else "UNVERIFIED"
        )
        time_states = {
            _source_recency(sources.get(ref, {}), now) for ref in row["sourceIds"]
        }
        row["timeVerification"] = (
            "RECENT"
            if time_states == {"RECENT"}
            else "HISTORICAL"
            if time_states and time_states <= {"HISTORICAL", "FUTURE"}
            else "UNVERIFIED"
        )
        if row["timeVerification"] == "HISTORICAL":
            row["historicalEvidence"] = True
        row["industryScopeVerified"] = False
        if row["kind"] in {"PROFIT_GROWTH", "CASH_COLLECTION"}:
            row["currentFactVerified"] = any(
                _earnings_fact(sources.get(ref, {}), row["kind"], now)
                for ref in row["sourceIds"]
            )
            row["factIdentityVerified"] = any(
                _fact_identity(row, clause) is not None
                for ref in row["sourceIds"]
                for _, clause in _earnings_fact_clauses(
                    sources.get(ref, {}), row["kind"], now
                )
            )
            row["industryScopeVerified"] = bool(expected_industries) and any(
                _fact_identity(row, clause) is not None
                and _industry_scope_verified(
                    {**sources.get(ref, {}), "quote": clause}, expected_industries
                )
                for ref in row["sourceIds"]
                for _, clause in _earnings_fact_clauses(
                    sources.get(ref, {}), row["kind"], now
                )
            )
        elif row["kind"] in {"COMMERCIALIZATION_EARLY", "EARNINGS_NOT_DELIVERED"}:
            row["industryScopeVerified"] = bool(expected_industries) and any(
                _industry_scope_verified(
                    {**sources.get(ref, {}), "quote": clause}, expected_industries
                )
                for ref in row["sourceIds"]
                for clause in _early_quote_clauses(
                    sources.get(ref, {}), row["kind"], now
                )
            )
        else:
            row["industryScopeVerified"] = bool(expected_industries) and any(
                sources.get(ref, {}).get("verification") == "SOURCE_CHECKED"
                and _industry_scope_verified(sources.get(ref, {}), expected_industries)
                for ref in row["sourceIds"]
            )
    for check in cycle["riskChecks"]:
        kind = check["kind"]
        if kind in seen:
            raise IndustryCycleValidationError(
                "INDUSTRY_RISK_CHECK_DUPLICATE",
                "each industry risk category must be checked exactly once",
            )
        seen.add(kind)
        check["queryValidation"] = (
            "BASIC_SYNTAX_ONLY"
            if _valid_not_found_query(check)
            else "INSUFFICIENT_QUERY"
        )
        check["searchExecutionVerified"] = False
        if (
            check["status"] == "NOT_FOUND"
            and check["queryValidation"] == "INSUFFICIENT_QUERY"
        ):
            check["status"] = "UNVERIFIED"
        supported_refs = [
            ref
            for ref in check["sourceIds"]
            if _source_recency(sources.get(ref, {}), now) == "RECENT"
            and _risk_quote_supported(sources.get(ref, {}), kind, now)
        ]
        check["claimSupported"] = (
            bool(supported_refs) and check["timeVerification"] == "RECENT"
        )
        check["claimSourceIds"] = supported_refs
        event_claims = (
            [
                (
                    ref,
                    clause,
                    event_time_verification(
                        clause,
                        sources.get(ref, {}).get("publishedDate")
                        or sources.get(ref, {}).get("publishedAt"),
                        now,
                    ),
                )
                for ref in supported_refs
                for clause in _risk_event_contexts(sources.get(ref, {}), kind, now)
            ]
            if isinstance(now, datetime)
            else []
        )
        check["industryScopeVerified"] = bool(expected_industries) and any(
            event["eventTimeVerification"] == "RECENT"
            and sources.get(ref, {}).get("verification") == "SOURCE_CHECKED"
            and _industry_scope_verified(
                {**sources.get(ref, {}), "quote": clause}, expected_industries
            )
            for ref, clause, event in event_claims
        )
        # The event clock is read only from the actual negative statement.
        # Recent publication or a separate product announcement cannot renew it.
        best_event = max(
            (event for _, _, event in event_claims),
            key=lambda row: {
                "UNVERIFIED": 0,
                "FUTURE": 1,
                "HISTORICAL": 2,
                "RECENT": 3,
            }[row["eventTimeVerification"]],
            default={
                "eventTimeVerification": "UNVERIFIED",
                "eventPeriod": None,
                "eventPeriodStart": None,
                "eventPeriodEnd": None,
            },
        )
        check.update(best_event)
        check["independentSourceCount"] = _independent_source_count(
            supported_refs, sources, now
        )
        check["strength"] = "NONE"
        if check["status"] == "FOUND" and check["timeVerification"] == "HISTORICAL":
            check["historicalEvidence"] = True
        if check["status"] == "FOUND":
            if not check["sourceIds"]:
                raise IndustryCycleValidationError(
                    "INDUSTRY_RISK_SOURCE_MISSING",
                    "found industry risk requires a current cited source",
                )
            flags.add(kind)
            if (
                check["claimSupported"]
                and check["verification"] == "SOURCE_CHECKED"
                and check["eventTimeVerification"] == "RECENT"
                and (not expected_industries or check["industryScopeVerified"])
            ):
                check["strength"] = "CONFIRMED"
                confirmed_flags.add(kind)
            else:
                check["strength"] = "SUSPECTED"
                suspected_flags.add(kind)
            if expected_industries and not check["industryScopeVerified"]:
                gaps.append("INDUSTRY_SCOPE_UNVERIFIED:" + kind)
        if check["status"] != "FOUND" or check["verification"] != "SOURCE_CHECKED":
            gaps.append(kind)
    if seen != set(RISK_CHECKS):
        raise IndustryCycleValidationError(
            "INDUSTRY_RISK_CHECK_MISSING",
            "five industry counter-evidence checks required",
        )
    kinds = {
        row["kind"]
        for row in cycle["evidence"]
        if row.get("timeVerification") != "HISTORICAL"
    }
    stage_candidates = {
        row["kind"]
        for row in cycle["evidence"]
        if row["kind"] in {"COMMERCIALIZATION_EARLY", "EARNINGS_NOT_DELIVERED"}
        and row.get("timeVerification") == "RECENT"
        and any(
            _early_quote_supported(sources.get(ref, {}), row["kind"], now)
            for ref in row["sourceIds"]
        )
    }
    stage_kinds = {
        row["kind"]
        for row in cycle["evidence"]
        if row["kind"] in stage_candidates
        and row.get("timeVerification") == "RECENT"
        and (not expected_industries or row["industryScopeVerified"])
    }
    facts = {kind: [] for kind in ("PROFIT_GROWTH", "CASH_COLLECTION")}
    for row in cycle["evidence"]:
        if row["kind"] in facts:
            facts[row["kind"]].extend(
                (
                    ref,
                    index,
                    clause,
                    identity,
                    bool(expected_industries)
                    and _industry_scope_verified(
                        {**sources.get(ref, {}), "quote": clause}, expected_industries
                    ),
                )
                for ref in row["sourceIds"]
                for index, clause in _earnings_fact_clauses(
                    sources.get(ref, {}), row["kind"], now
                )
                if (identity := _fact_identity(row, clause)) is not None
            )
    earnings_pairs = [
        (profit, cash)
        for profit in facts["PROFIT_GROWTH"]
        for cash in facts["CASH_COLLECTION"]
        if (profit[0] != cash[0] or profit[1] != cash[1])
        and profit[2] != cash[2]
        and _same_earnings_identity(profit[3], cash[3])
    ]
    scoped_pair = next(
        (pair for pair in earnings_pairs if pair[0][4] and pair[1][4]), None
    )
    earnings_pair = scoped_pair or next(iter(earnings_pairs), None)
    earnings_verified = earnings_pair is not None
    earnings_scope_verified = bool(scoped_pair) if expected_industries else None
    stage = cycle["stage"]
    if stage == "EARNINGS_DELIVERY" and not earnings_recovery_verified(
        {
            "earningsVerified": earnings_verified,
            "earningsIndustryScopeVerified": earnings_scope_verified,
        }
    ):
        stage = "UNKNOWN"
        gaps.append("CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING")
        if not all(facts.values()) or not earnings_pair:
            gaps.append("EARNINGS_ENTITY_OR_PERIOD_UNVERIFIED")
        flags.add("NO_CURRENT_EARNINGS_PROOF")
    if earnings_verified and earnings_scope_verified is False:
        gaps.append("EARNINGS_INDUSTRY_SCOPE_UNVERIFIED")
    if stage == "POLICY_CATALYST" and "POLICY_EVENT" not in kinds:
        stage = "UNKNOWN"
    if (
        stage == "POLICY_CATALYST"
        and expected_industries
        and not any(
            row["kind"] == "POLICY_EVENT"
            and row.get("timeVerification") != "HISTORICAL"
            and row["industryScopeVerified"]
            for row in cycle["evidence"]
        )
    ):
        stage = "UNKNOWN"
        gaps.append("STAGE_INDUSTRY_SCOPE_UNVERIFIED")
    if stage == "EARLY_COMMERCIALIZATION" and not stage_kinds:
        stage = "UNKNOWN"
        if stage_candidates and expected_industries:
            gaps.append("STAGE_INDUSTRY_SCOPE_UNVERIFIED")
    if "COMMERCIALIZATION_EARLY" in stage_kinds:
        flags.add("EARLY_COMMERCIALIZATION")
    if "EARNINGS_NOT_DELIVERED" in stage_kinds:
        flags.add("NO_EARNINGS_DELIVERY")
    if (
        cycle["stage"] != "UNKNOWN"
        and stage == "UNKNOWN"
        and cycle["stage"] != "EARNINGS_DELIVERY"
    ):
        gaps.append("CURRENT_INDUSTRY_STAGE_EVIDENCE_MISSING")
    forward_only = bool(kinds) and kinds <= {"EXPANSION_PLAN", "FRAMEWORK_AGREEMENT"}
    if forward_only:
        flags.add("FORWARD_PLANS_ONLY")
    summary = cycle["evidenceSummary"]
    if earnings_verified:
        summary = (
            summary.rstrip("。")
            + "。业绩证据范围为公司级披露，不代表全行业或基金实际持仓均已兑现。"
        )
    result = {
        **cycle,
        "stage": stage,
        "label": STAGE_LABELS[stage],
        "sourceIds": refs,
        "riskFlags": sorted(flags),
        "confirmedRiskFlags": sorted(confirmed_flags),
        "suspectedRiskFlags": sorted(suspected_flags),
        "riskLevel": "NONE",
        "researchStatus": "COMPLETE",
        "auditStructureStatus": "COMPLETE",
        "evidenceGaps": gaps,
        "earningsVerified": earnings_verified,
        "earningsIndustryScopeVerified": earnings_scope_verified,
        "earningsEntity": (earnings_pair[0][3][0] or earnings_pair[0][3][1])
        if earnings_pair
        else None,
        "earningsReportPeriod": earnings_pair[0][3][2] if earnings_pair else None,
        "earningsEvidenceScope": "COMPANY_LEVEL" if earnings_verified else "UNVERIFIED",
        "evidenceSummary": summary,
        "forwardPlansOnly": forward_only,
        "searchExecutionVerified": False,
    }
    result.update(industry_risk_policy(result))
    if now is not None and result["confirmedRiskFlags"] and isinstance(now, datetime):
        result["confirmedRiskAt"] = {
            flag: now.isoformat() for flag in result["confirmedRiskFlags"]
        }
        result["confirmedAt"] = now.isoformat()
    return result


def earnings_recovery_verified(cycle):
    """Company facts relieve industry doubts only when applicable to its scope.

    Reports written before explicit scope grading retain their legacy behavior.
    """
    return (
        isinstance(cycle, dict)
        and cycle.get("earningsVerified") is True
        and cycle.get("earningsIndustryScopeVerified") is not False
    )


def industry_risk_policy(cycle):
    """Derive risk intensity independently of stage and stale saved scale values.

    New server-normalized cycles carry explicit strength lists. Old reports
    without grading retain their protective full discount until re-researched.
    """
    cycle = cycle if isinstance(cycle, dict) else {}
    flags = set(cycle.get("riskFlags") or [])
    confirmed = set(cycle.get("confirmedRiskFlags") or []) & (
        set(RISK_CHECKS) | LEGACY_CONFIRMED_RISK_FLAGS
    )
    suspected = set(cycle.get("suspectedRiskFlags") or [])
    declared = cycle.get("riskLevel")
    recovered = earnings_recovery_verified(cycle)
    if recovered:
        flags.difference_update(EARNINGS_RISK_FLAGS)
        suspected.difference_update(EARNINGS_RISK_FLAGS)
    unproven = flags & EARNINGS_RISK_FLAGS
    if not recovered and cycle.get("forwardPlansOnly") is True:
        unproven.add("FORWARD_PLANS_ONLY")
    if not recovered and "CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING" in (
        cycle.get("evidenceGaps") or []
    ):
        unproven.add("NO_CURRENT_EARNINGS_PROOF")
    legacy = declared not in {"NONE", "UNPROVEN", "SUSPECTED", "CONFIRMED"}
    if legacy:
        confirmed.update(flags & (set(RISK_CHECKS) | LEGACY_CONFIRMED_RISK_FLAGS))
    confirmed.update(flags & LEGACY_CONFIRMED_RISK_FLAGS)
    if declared == "CONFIRMED" and not confirmed:
        confirmed.update(flags & set(RISK_CHECKS))
    early = not recovered and cycle.get("stage") == "EARLY_COMMERCIALIZATION"
    flags.update(confirmed | suspected | unproven)
    if confirmed:
        level, scale = "CONFIRMED", 1.0
    elif (
        suspected
        or "PERSISTED_RISK_WATCH" in flags
        or (declared == "SUSPECTED" and flags)
    ):
        level, scale = "SUSPECTED", 0.5
    elif early or unproven or flags:
        level, scale = "UNPROVEN", 0.5
    else:
        level, scale = "NONE", 0.0
    labels = {
        "FINANCING_COOLING": "一级市场融资降温",
        "LAYOFFS_CLOSURES": "企业欠薪/裁员/倒闭线索",
        "ORDER_CUTS": "下游砍单",
        "OVERCAPACITY_PRICE_WAR": "产能过剩/价格战",
        "PRODUCTION_DELAY": "商业化量产推迟",
        "EARLY_COMMERCIALIZATION": "商业化早期/等待订单兑现",
        "DEBUBBLING": "行业去泡沫/等待订单期",
        "NO_EARNINGS_DELIVERY": "缺乏当期盈利兑现",
        "FORWARD_PLANS_ONLY": "仅见远期扩产/框架意向，尚无当期盈利兑现",
        "NO_CURRENT_EARNINGS_PROOF": "当期利润增长/实际回款证据不足",
        "PERSISTED_RISK_WATCH": COMPANY_EARNINGS_INDUSTRY_WATCH_NOTE
        if recovered
        else "延续近14日已确认的行业降温观察，等待该类风险的恢复证据",
    }
    messages = [
        label + ("（待核实）" if flag in suspected and flag not in confirmed else "")
        for flag, label in labels.items()
        if flag in flags
    ]
    if early and "EARLY_COMMERCIALIZATION" not in flags:
        messages.append(labels["EARLY_COMMERCIALIZATION"])
    if level == "SUSPECTED":
        messages.append("存在待核实的行业降温线索")
    elif level == "UNPROVEN":
        messages.append("产业兑现证据仍待核实")
    warning = (
        "⚠️ " + "；".join(messages or ["行业风险已确认"]) + " · 谨防左侧接飞刀"
        if scale
        else None
    )
    return {
        "riskLevel": level,
        "leftDiscountScale": scale,
        "riskFlags": sorted(flags),
        "confirmedRiskFlags": sorted(confirmed),
        "suspectedRiskFlags": sorted(suspected),
        "industryRiskWarning": warning,
    }


def requires_left_discount(cycle):
    """A compatibility boolean; pricing consumes the full graded policy."""
    return industry_risk_policy(cycle)["leftDiscountScale"] > 0


industry_cycle_requires_discount = requires_left_discount


def merge_industry_cycles(primary, review):
    """Merge server-normalized evidence; a found review is a risk floor.

    Source proofs must already have passed ``normalize_industry_cycle``. Never
    feed this combined public object back into the model proposal validator.
    """
    if not isinstance(review, dict):
        result = (
            deepcopy(primary)
            if isinstance(primary, dict)
            else normalize_industry_cycle(None)
        )
        result = _with_watch_note(_clear_earnings_risks(result))
        result.update(industry_risk_policy(result))
        return result
    if not isinstance(primary, dict):
        result = deepcopy(review)
        result = _with_watch_note(_clear_earnings_risks(result))
        result.update(industry_risk_policy(result))
        return result
    result = deepcopy(primary)
    policies = [industry_risk_policy(value) for value in (primary, review)]
    result["sourceIds"] = sorted(
        set(primary.get("sourceIds") or []) | set(review.get("sourceIds") or [])
    )
    result["riskFlags"] = sorted(
        set(policies[0]["riskFlags"]) | set(policies[1]["riskFlags"])
    )
    for field in ("confirmedRiskFlags", "suspectedRiskFlags", "persistedRiskFlags"):
        result[field] = sorted(
            set(primary.get(field) or [])
            | set(review.get(field) or [])
            | (set(policies[0].get(field) or []) | set(policies[1].get(field) or []))
        )
    intensity = {"NONE": 0, "UNPROVEN": 1, "SUSPECTED": 2, "CONFIRMED": 3}
    result["riskLevel"] = max(
        (policy["riskLevel"] for policy in policies), key=intensity.get
    )
    evidence = list(result.get("evidence") or [])
    for row in review.get("evidence") or []:
        if row not in evidence:
            evidence.append(deepcopy(row))
    result["evidence"] = evidence
    by_kind = {row["kind"]: deepcopy(row) for row in result.get("riskChecks") or []}
    rank = {"NOT_FOUND": 0, "UNVERIFIED": 1, "FOUND": 2}

    def priority(row):
        return (
            rank.get(row.get("status"), -1),
            intensity.get(row.get("strength"), 0),
            row.get("verification") == "SOURCE_CHECKED",
        )

    for row in review.get("riskChecks") or []:
        old = by_kind.get(row["kind"])
        if old is None or priority(row) > priority(old):
            by_kind[row["kind"]] = deepcopy(row)
        elif old and priority(row) == priority(old) and row.get("status") == "FOUND":
            old["sourceIds"] = sorted(
                set(old.get("sourceIds") or []) | set(row.get("sourceIds") or [])
            )
            if row.get("verification") != "SOURCE_CHECKED":
                old["verification"] = "UNVERIFIED"
            old["independentSourceCount"] = max(
                old.get("independentSourceCount", 0),
                row.get("independentSourceCount", 0),
            )
    result["riskChecks"] = [by_kind[kind] for kind in RISK_CHECKS if kind in by_kind]
    result["evidenceGaps"] = [
        kind
        for kind in RISK_CHECKS
        if kind not in by_kind
        or by_kind[kind].get("status") != "FOUND"
        or by_kind[kind].get("verification") != "SOURCE_CHECKED"
    ] + [gap for gap in primary.get("evidenceGaps") or [] if gap not in RISK_CHECKS]
    if review.get("riskFlags"):
        summaries = list(result.get("reviewEvidenceSummaries") or [])
        summary = {
            "industry": review.get("industry"),
            "summary": review.get("evidenceSummary", "候选行业反方证据纳入风险下限。"),
            "sourceIds": list(review.get("sourceIds") or []),
        }
        if summary not in summaries:
            summaries.append(summary)
        result["reviewEvidenceSummaries"] = summaries
    for field in ("persistedEvidence",):
        items = deepcopy(primary.get(field) or [])
        for item in review.get(field) or []:
            if item not in items:
                items.append(deepcopy(item))
        if items:
            result[field] = items
    dates = {
        **(review.get("confirmedRiskAt") or {}),
        **(primary.get("confirmedRiskAt") or {}),
    }
    if dates:
        result["confirmedRiskAt"] = dates
    if not result.get("confirmedAt") and review.get("confirmedAt"):
        result["confirmedAt"] = review["confirmedAt"]
    result = _with_watch_note(_clear_earnings_risks(result))
    result.update(industry_risk_policy(result))
    return result


def _aware_stamp(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return (
            value
            if value.tzinfo is not None and value.utcoffset() is not None
            else None
        )
    except (TypeError, ValueError):
        return None


def _clear_earnings_risks(cycle):
    result = deepcopy(cycle)
    if not earnings_recovery_verified(result):
        return result
    for field in (
        "riskFlags",
        "confirmedRiskFlags",
        "suspectedRiskFlags",
        "persistedRiskFlags",
    ):
        result[field] = sorted(set(result.get(field) or []) - EARNINGS_RISK_FLAGS)
    result["forwardPlansOnly"] = False
    result["evidenceGaps"] = [
        gap
        for gap in result.get("evidenceGaps") or []
        if gap != "CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING"
    ]
    result["confirmedRiskAt"] = {
        flag: stamp
        for flag, stamp in (result.get("confirmedRiskAt") or {}).items()
        if flag not in EARNINGS_RISK_FLAGS
    }
    result["persistedEvidence"] = [
        proof
        for proof in result.get("persistedEvidence") or []
        if proof.get("flag") not in EARNINGS_RISK_FLAGS
    ]
    if not result["persistedRiskFlags"]:
        result["riskFlags"] = sorted(
            set(result["riskFlags"]) - {"PERSISTED_RISK_WATCH"}
        )
    return result


def _with_watch_note(cycle):
    if cycle.get("persistedRiskFlags"):
        note = (
            COMPANY_EARNINGS_INDUSTRY_WATCH_NOTE
            if earnings_recovery_verified(cycle)
            else "延续近14日已确认的行业降温观察，等待该类风险的恢复证据"
        )
        if note not in cycle.get("evidenceSummary", ""):
            cycle["evidenceSummary"] = (
                cycle.get("evidenceSummary", "").rstrip("。") + "。" + note + "。"
            )
    return cycle


def persist_industry_risk(current, previous, *, confirmed_at, now):
    """Keep a bounded observation of earlier confirmed risk after a missed search.

    Current source references and search results remain unchanged. Historical
    provenance lives in ``persistedEvidence`` and never impersonates today's
    FOUND result. Original per-risk timestamps prevent a rolling renewal.
    """
    now = _aware_stamp(now)
    if now is None:
        raise ValueError("risk persistence requires an aware current timestamp")
    result = (
        deepcopy(current)
        if isinstance(current, dict)
        else normalize_industry_cycle(None)
    )
    result = _clear_earnings_risks(result)
    saved_watch = set(result.get("persistedRiskFlags") or [])
    if saved_watch:
        dates = result.get("confirmedRiskAt") or {}
        valid_watch = {
            flag
            for flag in saved_watch
            if not (earnings_recovery_verified(result) and flag in EARNINGS_RISK_FLAGS)
            and (stamp := _aware_stamp(dates.get(flag))) is not None
            and timedelta(0) <= now - stamp <= timedelta(days=14)
        }
        result["persistedRiskFlags"] = sorted(valid_watch)
        result["persistedEvidence"] = [
            item
            for item in result.get("persistedEvidence") or []
            if item.get("flag") in valid_watch
        ]
        result["confirmedRiskAt"] = {
            flag: stamp
            for flag, stamp in dates.items()
            if flag not in saved_watch or flag in valid_watch
        }
        if not valid_watch:
            result["riskFlags"] = sorted(
                set(result.get("riskFlags") or []) - {"PERSISTED_RISK_WATCH"}
            )
            result["riskLevel"] = (
                "CONFIRMED"
                if result.get("confirmedRiskFlags")
                else "SUSPECTED"
                if result.get("suspectedRiskFlags")
                else "UNPROVEN"
                if result["riskFlags"]
                else "NONE"
            )
            if not result["confirmedRiskAt"]:
                result.pop("confirmedAt", None)
    result.update(industry_risk_policy(result))
    if not isinstance(previous, dict):
        return _with_watch_note(result)
    prior_policy = industry_risk_policy(previous)
    current_policy = industry_risk_policy(result)
    old_flags = set(prior_policy["confirmedRiskFlags"]) | set(
        previous.get("persistedRiskFlags") or []
    )
    if earnings_recovery_verified(result):
        old_flags.difference_update(EARNINGS_RISK_FLAGS)
    current_checks = {row["kind"]: row for row in result.get("riskChecks") or []}
    stored_dates = previous.get("confirmedRiskAt") or {}
    dates = deepcopy(result.get("confirmedRiskAt") or {})
    persisted, provenance = (
        set(result.get("persistedRiskFlags") or []),
        deepcopy(result.get("persistedEvidence") or []),
    )
    for flag in sorted(old_flags):
        if flag in current_policy["confirmedRiskFlags"]:
            continue
        check = current_checks.get(flag)
        missed = check and check.get("status") in {"NOT_FOUND", "UNVERIFIED"}
        if flag not in RISK_CHECKS:
            missed = bool(current_checks) and all(
                row.get("status") in {"NOT_FOUND", "UNVERIFIED"}
                for row in current_checks.values()
            )
        if not missed:
            continue
        # A persisted state without its original clock must not acquire a new
        # 14-day window from the later report that happened to carry it.
        fallback = (
            None if flag in (previous.get("persistedRiskFlags") or []) else confirmed_at
        )
        original = (
            _aware_stamp(stored_dates.get(flag))
            or _aware_stamp(previous.get("confirmedAt"))
            or _aware_stamp(fallback)
        )
        if original is None or not timedelta(0) <= now - original <= timedelta(days=14):
            continue
        prior_current = _aware_stamp(dates.get(flag))
        if (
            prior_current is not None
            and prior_current >= original
            and flag in persisted
        ):
            continue
        persisted.add(flag)
        dates[flag] = original.isoformat()
        old_proof = next(
            (
                item
                for item in previous.get("persistedEvidence") or []
                if item.get("flag") == flag
            ),
            None,
        )
        proof = (
            deepcopy(old_proof)
            if old_proof
            else {
                "flag": flag,
                "confirmedAt": original.isoformat(),
                "sourceIds": deepcopy(previous.get("sourceIds") or []),
                "summary": previous.get("evidenceSummary", "此前行业降温证据已确认。"),
            }
        )
        if isinstance(previous.get("sources"), list) and not old_proof:
            proof["sources"] = deepcopy(previous["sources"])
        if proof not in provenance:
            provenance.append(proof)
    if persisted:
        result["persistedRiskFlags"] = sorted(persisted)
        result["persistedEvidence"] = provenance
        result["confirmedRiskAt"] = dates
        result["confirmedAt"] = min(dates[flag] for flag in persisted)
        result["riskFlags"] = sorted(
            set(result.get("riskFlags") or []) | {"PERSISTED_RISK_WATCH"}
        )
        _with_watch_note(result)
        result.update(industry_risk_policy(result))
    return result


def expire_industry_risk(cycle, now):
    """Expire only carried observations, retaining today's independent evidence."""
    if not isinstance(cycle, dict):
        return deepcopy(cycle)
    if not cycle.get("persistedRiskFlags") and "PERSISTED_RISK_WATCH" not in (
        cycle.get("riskFlags") or []
    ):
        return deepcopy(cycle)
    result = persist_industry_risk(cycle, None, confirmed_at=None, now=now)
    # A marker without its original per-risk clock cannot start a new window.
    if not result.get("persistedRiskFlags") and "PERSISTED_RISK_WATCH" in (
        result.get("riskFlags") or []
    ):
        result["riskFlags"] = sorted(
            set(result["riskFlags"]) - {"PERSISTED_RISK_WATCH"}
        )
        result["persistedEvidence"] = []
        result["riskLevel"] = (
            "CONFIRMED"
            if industry_risk_policy(result)["confirmedRiskFlags"]
            else "SUSPECTED"
            if result.get("suspectedRiskFlags")
            else "UNPROVEN"
            if result["riskFlags"]
            else "NONE"
        )
        result.update(industry_risk_policy(result))
    return result


def expire_report_industry_risks(report, now):
    """Prune all report copies before live repricing or sector-risk merging."""
    if not isinstance(report, dict):
        return deepcopy(report)
    from backend.indicators.actionable_zones import apply_industry_cycle

    result = deepcopy(report)

    def refresh(row):
        if not isinstance(row, dict):
            return
        cycle = row.get("industryCycle")
        if isinstance(cycle, dict):
            expired = expire_industry_risk(cycle, now)
            if expired != cycle:
                if isinstance(row.get("tracks"), list):
                    updated = apply_industry_cycle(row, expired)
                    row.clear()
                    row.update(updated)
                else:
                    row["industryCycle"] = expired
                    warning = industry_risk_policy(expired).get("industryRiskWarning")
                    if warning:
                        row["industryRiskWarning"] = warning
                    else:
                        row.pop("industryRiskWarning", None)
        if isinstance(row.get("industryCycleReviews"), list):
            row["industryCycleReviews"] = [
                expire_industry_risk(review, now)
                for review in row["industryCycleReviews"]
            ]
        for field in (
            "actionablePlans",
            "actionableZones",
            "themeActionableZones",
            "discoveryActionableZones",
            "valueDirections",
            "plans",
            "quantitativeEvidence",
        ):
            for item in row.get(field, []) if isinstance(row.get(field), list) else []:
                refresh(item)
        scan = row.get("quantitativeScan")
        if isinstance(scan, dict):
            for item in scan.get("items", []):
                refresh(item)

    refresh(result)
    return result
