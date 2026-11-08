"""Versioned discovery identities and frozen budgets; matching is not auditing."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.macro.industry_contract import (
    NEWS_RELATED_INDUSTRIES,
    STANDARD_INDUSTRY_CODES,
)

DISCOVERY_VERSION = "research-discovery-v3"
REGISTRY_VERSION = "research-topics-v2"
MAX_REGISTRY_ITEMS = 72
INDUSTRY_REGISTRY_VERSION = "industry-registry-v1"
TOPIC_MATCH_VERSION = "topic-identity-v2"
ALLOCATION_VERSION = "industry-allocation-v3"
BUDGET = {
    "maxTopics": 64,
    "maxFormalDirections": 10,
    "maxScopes": 5,
    "maxQueries": 25,
    "maxSources": 24,
    "maxModelCalls": 1,
    "maxInputBytes": 4 * 1024 * 1024,
    "maxOutputBytes": 2 * 1024 * 1024,
    "maxExecutionSeconds": 1200,
}
# Historical profiles are literals, deliberately independent of the runtime
# writer budget. New semantics require a newly registered discovery version.
_DISCOVERY_CONTRACTS = {
    "research-discovery-v2": {
        "manifestVersion": "research-audit-manifest-v1",
        "registryVersion": "industry-registry-v1",
        "allocationVersions": {None, "industry-allocation-v1", "industry-allocation-v2"},
        "budgetProfiles": (
            {
                "maxTopics": 64,
                "maxFormalDirections": 8,
                "maxScopes": 10,
                "maxQueries": 50,
                "maxSources": 24,
            },
            {
                "maxTopics": 64,
                "maxFormalDirections": 8,
                "maxScopes": 10,
                "maxQueries": 50,
                "maxSources": 24,
                "maxModelCalls": 1,
                "maxInputBytes": 4194304,
                "maxOutputBytes": 2097152,
                "maxExecutionSeconds": 1200,
            },
        ),
        "riskKinds": frozenset(
            {
                "FINANCING_COOLING",
                "LAYOFFS_CLOSURES",
                "ORDER_CUTS",
                "OVERCAPACITY_PRICE_WAR",
                "PRODUCTION_DELAY",
            }
        ),
    },
    "research-discovery-v3": {
        "manifestVersion": "research-audit-manifest-v1",
        "registryVersion": "industry-registry-v1",
        "allocationVersions": {"industry-allocation-v3"},
        "budgetProfiles": ({
            "maxTopics": 64, "maxFormalDirections": 10, "maxScopes": 5,
            "maxQueries": 25, "maxSources": 24, "maxModelCalls": 1,
            "maxInputBytes": 4194304, "maxOutputBytes": 2097152,
            "maxExecutionSeconds": 1200,
        },),
        "riskKinds": frozenset({
            "FINANCING_COOLING", "LAYOFFS_CLOSURES", "ORDER_CUTS",
            "OVERCAPACITY_PRICE_WAR", "PRODUCTION_DELAY",
        }),
    },
}


class FrozenDiscoveryCompatibilityError(ValueError):
    code = "INCOMPATIBLE"

    def __init__(self):
        super().__init__(
            "INCOMPATIBLE: unsupported frozen research contract version or profile"
        )


def _discovery_contract(packet):
    if not isinstance(packet, dict) or "researchDiscovery" not in packet:
        return None
    discovery = packet["researchDiscovery"]
    if not isinstance(discovery, dict):
        raise ValueError("invalid frozen discovery structure")
    version = discovery.get("version")
    contract = _DISCOVERY_CONTRACTS.get(version) if isinstance(version, str) else None
    if contract is None:
        raise FrozenDiscoveryCompatibilityError()
    manifest = discovery.get("auditManifest")
    for field, supported in (
        ("topicMatchVersion", {None, "topic-identity-v1", TOPIC_MATCH_VERSION}),
        ("allocationVersion", contract["allocationVersions"]),
    ):
        version = discovery.get(field)
        frozen_version = manifest.get(field) if isinstance(manifest, dict) else None
        if (
            (version is not None and not isinstance(version, str))
            or (frozen_version is not None and not isinstance(frozen_version, str))
            or version not in supported
            or frozen_version not in supported
            or version != frozen_version
        ):
            raise FrozenDiscoveryCompatibilityError()
    return contract


def _known_budget(contract, budget):
    for profile in contract["budgetProfiles"]:
        if (
            isinstance(budget, dict)
            and budget.keys() == profile.keys()
            and all(
                type(budget[key]) is int and budget[key] == value
                for key, value in profile.items()
            )
        ):
            return profile
    raise FrozenDiscoveryCompatibilityError()


TOPIC_MESSAGES = {
    "FORMAL_VALIDATED": "正式方向已通过完整严格校验，投资许可仍以具体计划的实时保护为准。",
    "OBSERVATION_ONLY": "已保存公开观察，未授予投资许可。",
    "AUDIT_BUDGET_DEFERRED": "本批行业审核预算已用尽，已保留主题及后续待审任务。",
    "AUDIT_INCOMPLETE": "行业核查尚未完成，不能视为已经排除风险。",
    "INDUSTRY_IDENTITY_PENDING": "主题尚未与确定行业身份匹配，不能猜测相邻行业或套用审核。",
    "SOURCE_SCOPE_UNVERIFIED": "来源原文与主题产业范围的关联尚待核实。",
    "SOURCE_REFERENCE_INVALID": "主题引用的来源不可用，已隔离并保留原始材料。",
    "TOPIC_SCHEMA_INVALID": "主题结构不符合契约，已隔离并保留原始材料。",
    "TOPIC_KEY_DUPLICATE": "主题标识冲突，不能选择性合并事实。",
    "FORMAL_DIRECTION_PENDING": "整个方向已转为待审观察，相关计划未获投资许可。",
    "DEPENDENCY_REVIEW_REQUIRED": "关联事实或风险仍待核实，阻止自动正式发布。",
    "REGISTRY_OUTPUT_LIMIT": "主题目录超过保存预算，须查看原始材料，未静默截取主题。",
    "EXECUTION_UNMATCHED": "已观测到未对应本批预设查询的搜索，不能推断其行业归属或关闭待审风险任务；已匹配的核查保持有效。",
    "RECEIPT_SUMMARIZED_SIZE_LIMIT": "远程响应体积受限，执行回执仅保留计数摘要；原研究正文保留，未获证明的任务仍待审。",
    "RECEIPT_OMITTED_SIZE_LIMIT": "远程响应体积受限，执行回执已省略；原研究正文保留，不能把无回执视为未执行或已完成审核。",
}
FORMAL_ELIGIBILITY_REASONS = frozenset(
    {
        "NO_FORMAL_PROPOSAL",
        "FUND_PLAN_MISSING",
        "SOURCE_EVIDENCE_PENDING",
        "INDUSTRY_IDENTITY_PENDING",
        "INDUSTRY_AUDIT_PENDING",
        "FORMAL_VALIDATION_FAILED",
        "PUBLICATION_SAFETY_BLOCKED",
    }
)
# Only reviewed exact names belong here. Related/composite terms are not aliases.
STRICT_ALIASES = {
    "有色金属": "NONFERROUS",
    "nonferrous": "NONFERROUS",
    "稀土": "RARE_EARTH",
    "rare earth": "RARE_EARTH",
    "机器人": "ROBOT",
    "银行": "BANK",
    "黄金": "GOLD",
    "煤炭": "COAL",
    "化工": "CHEMICAL",
    "创新药": "INNOVATIVE_DRUG",
    "通信": "COMMUNICATION",
    "消费": "CONSUMPTION",
}
EXPLICIT_RELATED = {"工业金属": ("NONFERROUS",)}
V2_STRICT_ALIASES = {
    **STRICT_ALIASES,
    "券商": "BROKER",
    "证券公司": "BROKER",
    "金融科技": "FINTECH",
    "存储芯片": "MEMORY",
    "印制电路板": "PCB",
    "固态电池": "SOLID_BATTERY",
    "低空经济": "LOW_ALTITUDE",
    "港股科技": "HK_TECH",
    "中药": "TRADITIONAL_MEDICINE",
    "游戏": "GAMING",
    "国防军工": "DEFENCE",
}
ScopeName = Annotated[str, Field(min_length=1, max_length=100)]
SourceId = Annotated[str, Field(min_length=1, max_length=80)]
TopicNote = Annotated[str, Field(min_length=1, max_length=300)]


class TopicProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=100)
    thesis: str = Field(min_length=1, max_length=600)
    industryClaims: list[ScopeName] = Field(
        max_length=8,
        description="行业身份：只填标准代码或精确行业名，例如ROBOT、BROKER或券商。不是产业判断、风险说明或完整句子；限制和风险写入unknowns、thesis或pendingTasks。",
    )
    relatedIndustryIds: list[ScopeName] = Field(
        max_length=8,
        description="仅相关的标准行业代码；相关不等于主题身份、同范围审核或投资资格。",
    )
    sourceIds: list[SourceId] = Field(max_length=8)
    unknowns: list[TopicNote] = Field(max_length=8)
    pendingTasks: list[TopicNote] = Field(max_length=8)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def is_v2(packet):
    """Compatibility name for the frozen discovery core introduced in v2."""
    return _discovery_contract(packet) is not None


def is_targeted_research(packet):
    manifest = frozen_manifest(packet)
    return manifest is not None and packet["researchDiscovery"]["version"] == "research-discovery-v3"


def direction_limit(packet):
    """An old frozen budget never follows today's wider observation limit."""
    manifest = frozen_manifest(packet)
    return manifest["budget"]["maxFormalDirections"] if manifest is not None else 8


def topic_match_version(packet):
    """Frozen packets choose their original identity policy, never today's default."""
    _discovery_contract(packet)
    discovery = packet.get("researchDiscovery") or {}
    return discovery.get("topicMatchVersion") or "topic-identity-v1"


def is_named_industry_scope(value):
    """Only bounded names can schedule research; prose remains evidence to read."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    return bool(
        2 <= len(value) <= 40
        and re.fullmatch(
            r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff _&+/-]*", value
        )
        and not re.fullmatch(r"[A-Z][A-Z0-9_]*", value)
        and not re.search(
            r"不能|不等于|不足|不代表|不证明|证明|预计|预测|认为|判断|尚未|尚缺|需要|等待|验证|已核对|不属于|不是|未实施|增长|下降|减少|增加|上涨|下跌|过剩|裁员|欠薪|倒闭|砍单|回款|收入|净利润|盈利|身份|股份|份额|安排|传导|公布|宣布|计划|提议|风险",
            value,
        )
        and not re.search(
            r"\b(?:is|are|was|were|not|does|cannot|should|must|will|would|could|shows?|proves?|forecasts?|expected|profit|revenue|growth|decline)\b",
            value,
            re.IGNORECASE,
        )
    )


def match_topic(proposal, *, sources=None, version=None):
    """Resolve exact names only; separately report checked-source scope support."""
    if (version is not None and not isinstance(version, str)) or version not in {
        None,
        "topic-identity-v1",
        TOPIC_MATCH_VERSION,
    }:
        raise FrozenDiscoveryCompatibilityError()
    v2 = version == TOPIC_MATCH_VERSION
    claims = proposal.get("industryClaims") or []
    industries, related, evidence, unresolved = set(), set(), [], []
    alias, composite = False, False
    for claim in claims:
        if not isinstance(claim, str) or not claim.strip():
            unresolved.append("")
            continue
        value = claim.strip()
        canonical = value.upper() if v2 and value.isascii() else value
        code = (
            canonical
            if canonical in STANDARD_INDUSTRY_CODES
            else (V2_STRICT_ALIASES if v2 else STRICT_ALIASES).get(value.casefold())
        )
        if code is None:
            unresolved.append(value)
            related.update(EXPLICIT_RELATED.get(value, ()))
            continue
        industries.add(code)
        alias |= code != canonical
        composite |= code in NEWS_RELATED_INDUSTRIES
        related.update(NEWS_RELATED_INDUSTRIES.get(code, ()))
    # A model-proposed relationship cannot promote an unrecognized identity.
    for value in proposal.get("relatedIndustryIds", []):
        code = (
            value.strip().upper()
            if v2 and isinstance(value, str) and value.isascii()
            else value
        )
        if isinstance(code, str) and code in STANDARD_INDUSTRY_CODES:
            related.add(code)
    status = (
        "NEW_TOPIC"
        if unresolved
        else "UNMATCHED"
        if not industries
        else "COMPOSITE"
        if composite or len(industries) > 1
        else "STRICT_ALIAS"
        if alias
        else "STANDARD"
    )
    evidence.append(
        {
            "STANDARD": "STANDARD_CODE",
            "STRICT_ALIAS": "EXACT_REGISTERED_ALIAS",
            "COMPOSITE": "COMPOSITE_NOT_EQUIVALENT",
            "NEW_TOPIC": "UNRESOLVED_IDENTITY",
            "UNMATCHED": "NO_INDUSTRY_CLAIM",
        }[status]
    )
    verified = None
    proposed = [
        {
            "industryId": "TOPIC_SCOPE:" + digest(value)[:32],
            "searchTerm": value,
            "registryVersion": INDUSTRY_REGISTRY_VERSION,
        }
        for value in dict.fromkeys(unresolved)
        if value and (not v2 or is_named_industry_scope(value))
    ]
    if v2:
        if any(value and not is_named_industry_scope(value) for value in unresolved):
            evidence.append("NARRATIVE_CLAIM_NOT_IDENTITY")
        if proposed:
            evidence.append("NAMED_SCOPE_UNREGISTERED")
        if industries and unresolved:
            evidence.append("PARTIAL_INDUSTRY_IDENTITY")
    if sources is not None:
        from backend.macro.industry_cycle import _industry_scope_verified

        values = list(sources.values()) if isinstance(sources, dict) else list(sources)

        def supports(source, term, dynamic):
            return (
                source.get("verification") == "SOURCE_CHECKED"
                and _industry_scope_verified(source, [term])
                and (
                    not dynamic
                    or term in source.get("quote", "")
                    or term in source.get("title", "")
                )
            )

        verified = bool(industries or proposed) and all(
            any(
                supports(source, industry, dynamic)
                for source in values
                if isinstance(source, dict)
            )
            for industry, dynamic in [
                *((industry, False) for industry in industries),
                *((scope["searchTerm"], True) for scope in proposed),
            ]
        )
        evidence.append(
            "SOURCE_SCOPE_SUPPORTED" if verified else "SOURCE_SCOPE_UNVERIFIED"
        )
    return {
        "status": status,
        "industryIds": sorted(industries),
        "relatedIndustryIds": sorted(related - industries),
        "evidence": evidence,
        "registryVersion": INDUSTRY_REGISTRY_VERSION,
        "scopeVerified": verified,
        "unresolvedClaims": list(dict.fromkeys(unresolved)),
        "proposedScopes": proposed,
    }


def make_task(
    industry,
    kind,
    query,
    window,
    *,
    status="PLANNED",
    reason_code=None,
    search_term=None,
):
    task = {
        "taskId": "audit:" + digest([industry, kind, window])[:32],
        "industryId": industry,
        "kind": kind,
        "query": query,
        "window": deepcopy(window),
        "status": status,
    }
    if reason_code:
        task["reasonCode"] = reason_code
    if search_term is not None:
        task["searchTerm"] = search_term
    return task


def frozen_manifest(packet):
    supported = _discovery_contract(packet)
    if supported is None:
        return None
    discovery = packet["researchDiscovery"]
    manifest = discovery.get("auditManifest")
    if isinstance(manifest, dict) and (
        manifest.get("version") != supported["manifestVersion"]
        or manifest.get("registryVersion") != supported["registryVersion"]
    ):
        raise FrozenDiscoveryCompatibilityError()
    if not isinstance(manifest, dict) or manifest.get("manifestHash") != digest(
        {k: v for k, v in manifest.items() if k != "manifestHash"}
    ):
        raise ValueError("invalid frozen discovery manifest")
    budget = _known_budget(supported, manifest.get("budget"))
    _known_budget(supported, discovery.get("budget"))
    try:
        same_clock = datetime.fromisoformat(manifest["asOf"]) == datetime.fromisoformat(
            packet["asOf"]
        )
    except (ValueError, TypeError, KeyError):
        same_clock = False
    contract = manifest.get("candidateContract") or {}
    if contract.get("allocationVersion") != manifest.get("allocationVersion"):
        raise FrozenDiscoveryCompatibilityError()
    if discovery["version"] == "research-discovery-v3" and (
        contract.get("maximumCandidates") != budget["maxScopes"]
        or contract.get("observationScopes") != manifest.get("deferredScopes")
    ):
        raise ValueError("inconsistent targeted discovery policy")
    if (
        not same_clock
        or discovery.get("budget") != budget
        or [row.get("industry") for row in contract.get("candidates", [])]
        != manifest.get("selectedScopes")
        or contract.get("omittedIndustries") != manifest.get("deferredScopes")
    ):
        raise ValueError("inconsistent frozen discovery manifest")
    selected, tasks = manifest.get("selectedScopes"), manifest.get("tasks")
    if (
        not isinstance(selected, list)
        or len(selected) > budget["maxScopes"]
        or any(not isinstance(scope, str) for scope in selected)
        or len(set(selected)) != len(selected)
        or not isinstance(tasks, list)
        or len(tasks) != len(selected) * len(supported["riskKinds"])
        or len(tasks) > budget["maxQueries"]
        or any(
            not isinstance(row, dict) or not isinstance(row.get("taskId"), str)
            for row in tasks
        )
    ):
        raise ValueError("invalid discovery budget")
    for scope in selected:
        rows = [row for row in tasks if row.get("industryId") == scope]
        if {row.get("kind") for row in rows} != supported["riskKinds"] or len(
            rows
        ) != len(supported["riskKinds"]):
            raise ValueError("invalid discovery risk tasks")
    if len({row.get("taskId") for row in tasks}) != len(tasks):
        raise ValueError("invalid discovery task identity")
    return deepcopy(manifest)


def freeze_discovery_packet(packet, now):
    """Freeze once before prompt/hash/dispatch; replay never re-allocates tasks."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("aware discovery time required")
    result = deepcopy(packet)
    if _discovery_contract(result) is not None:
        frozen_manifest(result)
        return result
    supported = _DISCOVERY_CONTRACTS.get(DISCOVERY_VERSION)
    if supported is None:
        raise FrozenDiscoveryCompatibilityError()
    _known_budget(supported, BUDGET)
    from backend.macro.daily_research import apply_entry_guards
    from backend.integrations.daily_sources import industry_cycle_research_tasks

    stamp = datetime.fromisoformat(result["asOf"]) if result.get("asOf") else now
    if stamp.tzinfo is None or stamp > now:
        raise ValueError("invalid discovery input time")
    result = apply_entry_guards(result, result, stamp)
    contract = industry_cycle_research_tasks(result, stamp, allocation_version=ALLOCATION_VERSION)
    window = {
        "start": (stamp - timedelta(days=90)).date().isoformat(),
        "end": stamp.date().isoformat(),
        "lookbackDays": 90,
    }
    selected = [row["industry"] for row in contract["candidates"]]
    manifest = {
        "version": supported["manifestVersion"],
        "topicMatchVersion": TOPIC_MATCH_VERSION,
        "allocationVersion": ALLOCATION_VERSION,
        "asOf": stamp.isoformat(),
        "window": window,
        "registryVersion": supported["registryVersion"],
        "budget": deepcopy(BUDGET),
        "selectedScopes": selected,
        "deferredScopes": list(contract["omittedIndustries"]),
        "tasks": [
            make_task(row["industry"], query["kind"], query["query"], window)
            for row in contract["candidates"]
            for query in row["queries"]
        ],
        "candidateContract": contract,
    }
    manifest["manifestHash"] = digest(manifest)
    result["researchDiscovery"] = {
        "version": DISCOVERY_VERSION,
        "topicMatchVersion": TOPIC_MATCH_VERSION,
        "allocationVersion": ALLOCATION_VERSION,
        "budget": deepcopy(BUDGET),
        "auditManifest": manifest,
    }
    result["industryCycleResearch"] = deepcopy(contract)
    return result


def topic_tasks(match, manifest):
    from backend.macro.industry_cycle import RISK_CHECKS

    selected = set(manifest["selectedScopes"])
    required = [
        {"industryId": industry, "searchTerm": industry}
        for industry in match["industryIds"]
    ]
    required += match.get("proposedScopes", [])
    if manifest.get("allocationVersion") == "industry-allocation-v3":
        # A display task must keep the exact frozen query and identity. Only
        # status/reason change; observations schedule no additional searches.
        scopes = {scope["industryId"] for scope in required} & selected
        return [
            {
                **deepcopy(task),
                "status": "PLANNED_DEFERRED"
                if task["industryId"].startswith("TOPIC_SCOPE:")
                else "PENDING_REVIEW",
                "reasonCode": "INDUSTRY_IDENTITY_PENDING"
                if task["industryId"].startswith("TOPIC_SCOPE:")
                else "AUDIT_INCOMPLETE",
            }
            for task in manifest["tasks"]
            if task["industryId"] in scopes
        ]
    return [
        make_task(
            scope["industryId"],
            kind,
            f"{scope['searchTerm']} {terms}",
            manifest["window"],
            status="PLANNED_DEFERRED"
            if scope["industryId"].startswith("TOPIC_SCOPE:")
            else "PENDING_REVIEW",
            reason_code="INDUSTRY_IDENTITY_PENDING"
            if scope["industryId"].startswith("TOPIC_SCOPE:")
            else "AUDIT_INCOMPLETE"
            if scope["industryId"] in selected
            else "AUDIT_BUDGET_DEFERRED",
            search_term=scope["searchTerm"],
        )
        for scope in required
        for kind, terms in RISK_CHECKS.items()
    ]
