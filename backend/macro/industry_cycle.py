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

RISK_CHECKS = {
    "FINANCING_COOLING": "一级市场融资降温/断代",
    "LAYOFFS_CLOSURES": "企业欠薪/裁员/倒闭",
    "ORDER_CUTS": "下游砍单",
    "OVERCAPACITY_PRICE_WAR": "产能过剩/价格战",
    "PRODUCTION_DELAY": "商业化量产推迟",
}
STAGE_LABELS = {
    "EARNINGS_DELIVERY": "🟢 业绩兑现期",
    "POLICY_CATALYST": "🟡 政策/事件催化期",
    "EARLY_COMMERCIALIZATION": "🟠 商业化早期·去伪存真期",
    "UNKNOWN": "产业兑现阶段待核查",
}


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


def _earnings_fact_clauses(source, kind, now):
    if source.get("verification") != "SOURCE_CHECKED" or _source_recency(
        source, now
    ) not in {"RECENT", "DATED"}:
        return []
    # Validate a minimal factual statement in the checked quote, rather than
    # trusting an LLM's evidence.kind or its paraphrased summary.
    matches = []
    for index, clause in enumerate(
        re.split(
            r"[，。；;\n]|(?<!\d),(?!\d)|(?<=[A-Za-z])\.(?=\s+[A-Z])",
            str(source.get("quote") or ""),
        )
    ):
        if re.search(
            r"预计|预期|预测|计划|拟|有望|力争|目标|将|明年|未来|尚无|尚未|未实现|未收到|未回款|无实际|没有|未增长"
            r"|\b(?:expect\w*|forecast\w*|planned|planning|will|aims|could)\b|\bnot\b|\bno cash\b",
            clause,
            re.I,
        ):
            continue
        if kind == "PROFIT_GROWTH" and (
            re.search(
                r"净利(?:润)?|归母净利|扣非净利|利润|\bprofit\b|\bearnings\b|net income",
                clause,
                re.I,
            )
            and re.search(
                r"增长|增加|扭亏|上升|\bgrew\b|\bgrowth\b|\brose\b|\bincreased?\b",
                clause,
                re.I,
            )
        ):
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
    raw, *, source_aliases=None, sources=None, require_checks=False, now=None
):
    """Normalize a model proposal using only current validated source identities.

    ``sources`` contains the report validator's server-produced source proofs.
    Media evidence remains usable with its actual verification state; a green
    earnings label needs checked profit *and* checked cash collection evidence.
    """
    if raw is None:
        if require_checks:
            raise ValueError("each direction requires industry counter-evidence checks")
        return {
            "stage": "UNKNOWN",
            "label": STAGE_LABELS["UNKNOWN"],
            "riskFlags": [],
            "sourceIds": [],
            "evidence": [],
            "riskChecks": [],
            "evidenceSummary": "旧报告未执行产业周期反方核查，等待本轮研究补齐。",
            "researchStatus": "INCOMPLETE",
            "evidenceGaps": list(RISK_CHECKS),
            "searchExecutionVerified": False,
            **industry_risk_policy({"riskLevel": "NONE"}),
        }
    cycle = IndustryCycleProposal.model_validate(raw).model_dump()
    aliases = source_aliases or {}
    sources = sources or {}

    def references(refs):
        if any(ref not in aliases for ref in refs):
            raise ValueError("unknown industry-cycle source reference")
        return sorted({aliases[ref] for ref in refs})

    refs = references(cycle["sourceIds"])
    seen = set()
    flags = set()
    confirmed_flags, suspected_flags = set(), set()
    gaps = []
    for row in [*cycle["evidence"], *cycle["riskChecks"]]:
        row["sourceIds"] = references(row["sourceIds"])
        if any(ref not in refs for ref in row["sourceIds"]):
            raise ValueError("industry evidence must cite its direction cycle sources")
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
        if row["kind"] in {"PROFIT_GROWTH", "CASH_COLLECTION"}:
            row["currentFactVerified"] = any(
                _earnings_fact(sources.get(ref, {}), row["kind"], now)
                for ref in row["sourceIds"]
            )
    for check in cycle["riskChecks"]:
        kind = check["kind"]
        if kind in seen:
            raise ValueError("each industry risk category must be checked exactly once")
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
        check["independentSourceCount"] = _independent_source_count(
            check["sourceIds"], sources, now
        )
        check["strength"] = "NONE"
        if check["status"] == "FOUND" and check["timeVerification"] == "HISTORICAL":
            check["status"] = "UNVERIFIED"
            check["historicalEvidence"] = True
        if check["status"] == "FOUND":
            if not check["sourceIds"]:
                raise ValueError("found industry risk requires a current cited source")
            flags.add(kind)
            if (
                check["verification"] == "SOURCE_CHECKED"
                or check["independentSourceCount"] >= 2
            ):
                check["strength"] = "CONFIRMED"
                confirmed_flags.add(kind)
            elif check["independentSourceCount"] == 1:
                check["strength"] = "SUSPECTED"
                suspected_flags.add(kind)
            else:
                check["strength"] = "UNPROVEN"
        if check["status"] != "FOUND" or check["verification"] != "SOURCE_CHECKED":
            gaps.append(kind)
    if seen != set(RISK_CHECKS):
        raise ValueError("five industry counter-evidence checks required")
    kinds = {
        row["kind"]
        for row in cycle["evidence"]
        if row.get("timeVerification") != "HISTORICAL"
    }
    facts = {kind: [] for kind in ("PROFIT_GROWTH", "CASH_COLLECTION")}
    for row in cycle["evidence"]:
        if row["kind"] in facts:
            facts[row["kind"]].extend(
                (ref, index, clause)
                for ref in row["sourceIds"]
                for index, clause in _earnings_fact_clauses(
                    sources.get(ref, {}), row["kind"], now
                )
            )
    earnings_verified = any(
        (profit[0] != cash[0] or profit[1] != cash[1]) and profit[2] != cash[2]
        for profit in facts["PROFIT_GROWTH"]
        for cash in facts["CASH_COLLECTION"]
    )
    stage = cycle["stage"]
    if stage == "EARNINGS_DELIVERY" and not earnings_verified:
        stage = "UNKNOWN"
        gaps.append("CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING")
        flags.add("NO_CURRENT_EARNINGS_PROOF")
    if stage == "POLICY_CATALYST" and "POLICY_EVENT" not in kinds:
        stage = "UNKNOWN"
    if stage == "EARLY_COMMERCIALIZATION" and not (
        "COMMERCIALIZATION_EARLY" in kinds
        or "EARNINGS_NOT_DELIVERED" in kinds
        or flags & {"FINANCING_COOLING", "PRODUCTION_DELAY"}
    ):
        stage = "UNKNOWN"
    if "COMMERCIALIZATION_EARLY" in kinds:
        flags.add("EARLY_COMMERCIALIZATION")
    if "EARNINGS_NOT_DELIVERED" in kinds:
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
        "evidenceGaps": gaps,
        "earningsVerified": earnings_verified,
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


def industry_risk_policy(cycle):
    """Derive risk intensity independently of stage and stale saved scale values.

    New server-normalized cycles carry explicit strength lists. Old reports
    without grading retain their protective full discount until re-researched.
    """
    cycle = cycle if isinstance(cycle, dict) else {}
    flags = set(cycle.get("riskFlags") or [])
    confirmed = set(cycle.get("confirmedRiskFlags") or [])
    suspected = set(cycle.get("suspectedRiskFlags") or [])
    declared = cycle.get("riskLevel")
    unproven = flags & {
        "FORWARD_PLANS_ONLY",
        "NO_CURRENT_EARNINGS_PROOF",
        "NO_EARNINGS_DELIVERY",
    }
    if cycle.get("forwardPlansOnly") is True:
        unproven.add("FORWARD_PLANS_ONLY")
    if "CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING" in (cycle.get("evidenceGaps") or []):
        unproven.add("NO_CURRENT_EARNINGS_PROOF")
    legacy = declared not in {"NONE", "UNPROVEN", "SUSPECTED", "CONFIRMED"}
    if legacy:
        confirmed.update(
            flags
            & (
                set(RISK_CHECKS)
                | {"DEBUBBLING", "EARLY_COMMERCIALIZATION", "NO_EARNINGS_DELIVERY"}
            )
        )
    early = cycle.get("stage") == "EARLY_COMMERCIALIZATION"
    if early and "EARLY_COMMERCIALIZATION" in flags:
        confirmed.add("EARLY_COMMERCIALIZATION")
    flags.update(confirmed | suspected | unproven)
    if confirmed or early or declared == "CONFIRMED":
        level, scale = "CONFIRMED", 1.0
    elif suspected or "PERSISTED_RISK_WATCH" in flags or declared == "SUSPECTED":
        level, scale = "SUSPECTED", 0.5
    elif unproven or flags or declared == "UNPROVEN":
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
        "PERSISTED_RISK_WATCH": "延续近14日已确认的行业降温观察，待盈利恢复证据解除",
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
        result.update(industry_risk_policy(result))
        return result
    if not isinstance(primary, dict):
        result = deepcopy(review)
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
    saved_watch = set(result.get("persistedRiskFlags") or [])
    if saved_watch:
        dates = result.get("confirmedRiskAt") or {}
        valid_watch = {
            flag
            for flag in saved_watch
            if result.get("earningsVerified") is not True
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
    if not isinstance(previous, dict) or result.get("earningsVerified") is True:
        return result
    prior_policy = industry_risk_policy(previous)
    current_policy = industry_risk_policy(result)
    old_flags = set(prior_policy["confirmedRiskFlags"]) | set(
        previous.get("persistedRiskFlags") or []
    )
    if previous.get("stage") == "EARLY_COMMERCIALIZATION":
        old_flags.add("EARLY_COMMERCIALIZATION")
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
        note = "延续近14日已确认的行业降温观察，待盈利恢复证据解除"
        if note not in result.get("evidenceSummary", ""):
            result["evidenceSummary"] = (
                result.get("evidenceSummary", "").rstrip("。") + "。" + note + "。"
            )
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
            if result.get("confirmedRiskFlags")
            or result.get("stage") == "EARLY_COMMERCIALIZATION"
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
