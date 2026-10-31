"""Evidence-backed industry stages and an explicit counter-evidence audit.

The model proposes classifications; source identities and verification remain
server controlled. A missing search result is a gap, never proof of safety.
"""

from copy import deepcopy
from datetime import date, datetime
import re
from typing import Literal

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


def _earnings_fact(source, kind, now):
    if source.get("verification") != "SOURCE_CHECKED" or _source_recency(
        source, now
    ) not in {"RECENT", "DATED"}:
        return False
    # Validate a minimal factual statement in the checked quote, rather than
    # trusting an LLM's evidence.kind or its paraphrased summary.
    for clause in re.split(r"[。；;\n]", str(source.get("quote") or "")):
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
            return True
        if kind == "CASH_COLLECTION" and re.search(
            r"实际回款|已回款|已.{0,6}收到.{0,12}货款|收到.{0,12}客户.{0,8}货款|客户.{0,12}已.{0,8}回款"
            r"|销售商品.{0,12}收到.{0,8}现金|cash (?:collected|received)|collected.{0,20}(?:cash|payment)|paid customer orders",
            clause,
            re.I,
        ):
            return True
    return False


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
        if check["status"] == "FOUND" and check["timeVerification"] == "HISTORICAL":
            check["status"] = "UNVERIFIED"
            check["historicalEvidence"] = True
        if check["status"] == "FOUND":
            if not check["sourceIds"]:
                raise ValueError("found industry risk requires a current cited source")
            flags.add(kind)
        if check["status"] != "FOUND" or check["verification"] != "SOURCE_CHECKED":
            gaps.append(kind)
    if seen != set(RISK_CHECKS):
        raise ValueError("five industry counter-evidence checks required")
    kinds = {
        row["kind"]
        for row in cycle["evidence"]
        if row.get("timeVerification") != "HISTORICAL"
    }
    checked_kinds = {
        row["kind"]
        for row in cycle["evidence"]
        if row.get("currentFactVerified") is True
    }
    stage = cycle["stage"]
    if (
        stage == "EARNINGS_DELIVERY"
        and not {"PROFIT_GROWTH", "CASH_COLLECTION"} <= checked_kinds
    ):
        stage = "UNKNOWN"
        gaps.append("CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING")
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
    return {
        **cycle,
        "stage": stage,
        "label": STAGE_LABELS[stage],
        "sourceIds": refs,
        "riskFlags": sorted(flags),
        "researchStatus": "COMPLETE",
        "evidenceGaps": gaps,
        "earningsVerified": {"PROFIT_GROWTH", "CASH_COLLECTION"} <= checked_kinds,
        "forwardPlansOnly": bool(kinds)
        and kinds <= {"EXPANSION_PLAN", "FRAMEWORK_AGREEMENT"},
    }


def requires_left_discount(cycle):
    """A conservative overlay based on classified evidence, never sector names."""
    return isinstance(cycle, dict) and (
        cycle.get("stage") == "EARLY_COMMERCIALIZATION"
        or bool(
            set(cycle.get("riskFlags") or [])
            & (
                set(RISK_CHECKS)
                | {"DEBUBBLING", "EARLY_COMMERCIALIZATION", "NO_EARNINGS_DELIVERY"}
            )
        )
    )


industry_cycle_requires_discount = requires_left_discount


def merge_industry_cycles(primary, review):
    """Merge server-normalized evidence; a found review is a risk floor.

    Source proofs must already have passed ``normalize_industry_cycle``. Never
    feed this combined public object back into the model proposal validator.
    """
    if not isinstance(review, dict):
        return (
            deepcopy(primary)
            if isinstance(primary, dict)
            else normalize_industry_cycle(None)
        )
    if not isinstance(primary, dict):
        return deepcopy(review)
    result = deepcopy(primary)
    result["sourceIds"] = sorted(
        set(primary.get("sourceIds") or []) | set(review.get("sourceIds") or [])
    )
    result["riskFlags"] = sorted(
        set(primary.get("riskFlags") or []) | set(review.get("riskFlags") or [])
    )
    evidence = list(result.get("evidence") or [])
    for row in review.get("evidence") or []:
        if row not in evidence:
            evidence.append(deepcopy(row))
    result["evidence"] = evidence
    by_kind = {row["kind"]: deepcopy(row) for row in result.get("riskChecks") or []}
    rank = {"NOT_FOUND": 0, "UNVERIFIED": 1, "FOUND": 2}
    for row in review.get("riskChecks") or []:
        old = by_kind.get(row["kind"])
        if old is None or rank.get(row.get("status"), -1) > rank.get(
            old.get("status"), -1
        ):
            by_kind[row["kind"]] = deepcopy(row)
        elif old and row.get("status") == old.get("status") == "FOUND":
            old["sourceIds"] = sorted(
                set(old.get("sourceIds") or []) | set(row.get("sourceIds") or [])
            )
            if row.get("verification") != "SOURCE_CHECKED":
                old["verification"] = "UNVERIFIED"
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
    return result
