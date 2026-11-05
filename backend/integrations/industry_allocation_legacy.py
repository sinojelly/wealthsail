"""Frozen legacy allocation: retain pre-policy research obligations verbatim."""

import math

from backend.integrations.daily_sources import _aware

# A future live budget must not change an unversioned historical obligation.
MAX_INDUSTRY_CYCLE_CANDIDATES = 10


def industry_cycle_research_tasks_legacy(packet, now):
    """Build live-search jobs from this run's actual sectors, without invented news.

    Codex public research already runs with ``--search``. These queries extend
    that collector to financing/operating-cycle sources outside official macro
    feeds. Results must still use the ordinary report source-validation path.
    """
    from backend.macro.research_topics import frozen_manifest
    frozen = frozen_manifest(packet)
    if frozen is not None:
        from copy import deepcopy
        return deepcopy(frozen["candidateContract"])
    from backend.macro.industry_cycle import RISK_CHECKS
    from backend.macro.industry_contract import (
        NEWS_RELATED_INDUSTRIES,
        STANDARD_INDUSTRY_CODES,
        plan_industry_ids,
    )

    now = _aware(now)
    candidates = []
    news = {
        sector
        for sector, ids in (
            (packet.get("alphaPublic") or {}).get("sectorCatalysts") or {}
        ).items()
        if ids and sector != "OTHER"
    }
    news.update(
        related
        for sector in list(news)
        for related in NEWS_RELATED_INDUSTRIES.get(sector, ())
    )
    quantitative = {}
    search_names = {
        "ROBOT": "机器人 具身智能",
        "ROBOTICS_MOBILITY": "机器人 智能驾驶",
        "SEMICONDUCTOR_AI": "半导体 AI算力",
        "AI": "人工智能 算力",
        "RARE_EARTH": "稀土 永磁 磁材",
        "NONFERROUS": "有色金属 工业金属 铜 铝",
        "RARE_EARTH_METALS": "稀土 有色金属 产业链",
        "BROAD": "宽基 指数 沪深300",
        "BANK": "银行",
        "BROKER": "证券 券商",
        "FINTECH": "金融科技",
        "ACTIVE_GROWTH": "成长行业 科技产业",
        "DEFENCE": "军工 国防",
        "COMMUNICATION": "通信 5G 光通信",
        "INNOVATIVE_DRUG": "创新药 生物医药",
        "GOLD": "黄金 金矿",
        "GLOBAL_ENERGY": "全球能源 石油 天然气",
        "US_TECH": "美国科技 纳斯达克",
        "FINANCIAL": "银行 券商",
        "FINANCE_POLICY": "金融政策 银行 券商",
        "DRUG": "创新药 生物医药",
        "HEALTHCARE_CLINICAL": "医药 临床",
        "GOLD_ENERGY": "黄金 能源 油气",
        "AEROSPACE": "航天 军工",
        "CONSUMPTION": "消费 食品饮料",
        "MEMORY": "存储芯片",
        "PCB": "印制电路板",
        "POWER": "电力 电网",
        "SOLID_BATTERY": "固态电池",
        "SATELLITE": "卫星互联网",
        "LOW_ALTITUDE": "低空经济",
        "HK_TECH": "港股科技",
        "DIVIDEND": "红利 低波",
        "TRADITIONAL_MEDICINE": "中药",
        "COAL": "煤炭",
        "GAMING": "游戏",
        "CHEMICAL": "化工",
    }

    def add(value):
        candidate = value.strip()[:100] if isinstance(value, str) else ""
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    previous_risks = set()
    for row in packet.get("previousIndustryRiskStates", []):
        if not isinstance(row, dict):
            continue
        cycle = row.get("industryCycle")
        if not isinstance(cycle, dict):
            continue
        risk_fields = (
            "riskFlags",
            "confirmedRiskFlags",
            "suspectedRiskFlags",
            "persistedRiskFlags",
        )
        if not any(
            isinstance(cycle.get(field), list)
            and any(isinstance(flag, str) and flag.strip() for flag in cycle[field])
            for field in risk_fields
        ):
            continue
        industry = row.get("industry")
        if isinstance(industry, str) and industry.strip():
            industry = industry.strip()[:100]
            previous_risks.add(industry)
            add(industry)

    pending_scopes = set()
    for row in packet.get("pendingTopicScopes", []):
        if not isinstance(row, dict):
            continue
        identity, term = row.get("industryId"), row.get("searchTerm")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 100:
            continue
        if not isinstance(term, str) or not term.strip() or len(term) > 100:
            continue
        if identity.startswith("TOPIC_SCOPE:"):
            from backend.macro.research_topics import digest
            if identity != "TOPIC_SCOPE:" + digest(term)[:32]:
                continue
        pending_scopes.add(identity)
        search_names[identity] = term
        add(identity)

    plan_requirements = {}
    plan_industries = set()
    for anchor in packet.get("discoveryActionableZones", []):
        if not isinstance(anchor, dict):
            continue
        key = anchor.get("subjectKind"), anchor.get("subjectId")
        if key[0] not in {"fund", "nav"} or not isinstance(key[1], str):
            continue
        required = plan_industry_ids(packet, anchor)
        plan_requirements.setdefault(key, set()).update(required)
        for sector in sorted(required):
            plan_industries.add(sector)
            add(sector)

    for row in (packet.get("quantitativeScan") or {}).get("items", []):
        if isinstance(row, dict):
            sectors = plan_industry_ids(packet, row)
            if not sectors and isinstance(row.get("discoverySector"), str):
                sectors = {row["discoverySector"]}
            if not sectors:
                continue
            signals = [
                item for item in row.get("signals", []) if isinstance(item, dict)
            ]
            ready = row.get("entryReady") is True or any(
                item.get("entryReady") is True for item in signals
            )
            distances = []
            for item in [row, *signals]:
                value = item.get("distanceToEntryPct")
                if isinstance(value, bool):
                    continue
                try:
                    distance = float(value)
                    if math.isfinite(distance) and distance >= 0:
                        distances.append(distance)
                except (ValueError, TypeError):
                    continue
            priority = (not ready, min(distances, default=math.inf))
            for sector in sorted(sectors):
                add(sector)
                quantitative[sector] = min(quantitative.get(sector, priority), priority)
    for row in packet.get("discoveryUniverse", []):
        if isinstance(row, dict):
            add(row.get("discoverySector") or row.get("sector"))
    for sector in sorted(news):
        add(sector)
    order = {sector: index for index, sector in enumerate(candidates)}
    candidates.sort(
        key=lambda sector: (
            -2
            if sector in previous_risks
            else -1.5
            if sector in pending_scopes
            else -1
            if sector in plan_industries
            else 0
            if sector in news
            else 1
            if sector in quantitative
            else 2,
            *quantitative.get(sector, (True, math.inf)),
            order[sector],
        )
    )
    all_industries = list(candidates)
    omitted_industries = candidates[MAX_INDUSTRY_CYCLE_CANDIDATES:]
    candidates = candidates[:MAX_INDUSTRY_CYCLE_CANDIDATES]
    selected = set(candidates)
    return {
        "required": True,
        "execution": "PUBLIC_WEB_SEARCH",
        "asOf": now.isoformat(),
        "lookbackDays": 90,
        "maximumCandidates": MAX_INDUSTRY_CYCLE_CANDIDATES,
        "omittedCandidateCount": len(omitted_industries),
        "omittedRiskCandidateCount": len(previous_risks - selected),
        "omittedIndustries": omitted_industries,
        "allIndustries": all_industries,
        "scopeSearchTerms": {industry: search_names.get(industry, industry) for industry in all_industries},
        "deferredPages": [omitted_industries[index:index + 10] for index in range(0, len(omitted_industries), 10)],
        "omittedPlanIndustryIds": sorted(plan_industries - selected),
        "standardIndustryCodes": sorted(STANDARD_INDUSTRY_CODES),
        "planReviewEligibility": [
            {
                "subjectKind": kind,
                "subjectId": subject,
                "requiredReviewIndustryIds": sorted(required),
                "industryReviewEligible": bool(required) and required <= selected,
            }
            for (kind, subject), required in plan_requirements.items()
        ],
        "newDirectionRequiresSameChecks": True,
        "candidates": [
            {
                "industry": industry,
                "queries": [
                    {
                        "kind": kind,
                        "query": f"{search_names.get(industry, industry)} {terms} {now.year} 近期 近90天",
                    }
                    for kind, terms in RISK_CHECKS.items()
                ],
            }
            for industry in candidates
        ],
    }
