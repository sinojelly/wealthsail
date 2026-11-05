"""Bounded official documents and EIA/FRED observations, never causal proof.

Quote verification establishes only that text occurs in the cited original.
Publication day, observation day and actual retrieval time stay separate.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import math
import re
import unicodedata
from itertools import zip_longest
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_BYTES = 2 * 1024 * 1024
SOURCE_TIMEOUT_SECONDS = 45
MAX_RESEARCH_SOURCE_CHECKS = 24
MAX_INDUSTRY_CYCLE_CANDIDATES = 10
OFFICIAL_HOSTS = frozenset(
    {
        "ofac.treasury.gov",
        "home.treasury.gov",
        "fiscaldata.treasury.gov",
        "www.treasury.gov",
        "treasury.gov",
        "www.federalreserve.gov",
        "federalreserve.gov",
        "www.stats.gov.cn",
        "stats.gov.cn",
        "www.pbc.gov.cn",
        "pbc.gov.cn",
        "www.gov.cn",
        "www.safe.gov.cn",
        "www.ndrc.gov.cn",
        "www.mof.gov.cn",
        "www.eia.gov",
        "eia.gov",
        "fred.stlouisfed.org",
        "www.fec.gov",
        "fec.gov",
        "www.ecb.europa.eu",
        "www.bankofengland.co.uk",
        "www.boj.or.jp",
        "www.imf.org",
        "www.worldbank.org",
        "www.bea.gov",
        "www.bls.gov",
        "www.whitehouse.gov",
        "www.commerce.gov",
        "www.census.gov",
        "www.iea.org",
        "www.opec.org",
        "www.consilium.europa.eu",
        # Named exchange/official disclosure originals for industry earnings.
        # The same bounded HTML/text, redirect and quote checks still apply.
        "www.sse.com.cn",
        "www.szse.cn",
        "www.cninfo.com.cn",
    }
)
ENERGY = {
    "DCOILBRENTEU": ("布伦特原油现货", "USD/barrel", "DAILY", 10),
    "GASDESW": ("美国柴油零售价格", "USD/gallon", "WEEKLY", 21),
}
INDEXES = {
    "OFAC": "https://ofac.treasury.gov/recent-actions",
    "FED": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "NBS": "https://www.stats.gov.cn/sj/zxfb/",
    # Publisher-declared subscriptions; see BLS help/hlp_rss.htm and
    # EIA todayinenergy/about.php and tools/rssfeeds/.
    "BLS": "https://www.bls.gov/feed/bls_latest.rss",
    "EIA": "https://www.eia.gov/rss/todayinenergy.xml",
}
PUBLICATION_META = frozenset(
    {
        "pubdate",
        "publishdate",
        "article:published_time",
        "dcterms.date",
        "dc.date",
        "date",
    }
)


def industry_cycle_research_tasks(packet, now, *, allocation_version=None):
    """Build live-search jobs from this run's actual sectors, without invented news.

    Codex public research already runs with ``--search``. These queries extend
    that collector to financing/operating-cycle sources outside official macro
    feeds. Results must still use the ordinary report source-validation path.
    """
    from backend.macro.research_topics import (
        ALLOCATION_VERSION,
        FrozenDiscoveryCompatibilityError,
        frozen_manifest,
    )

    if (
        allocation_version is not None
        and (
            not isinstance(allocation_version, str)
            or allocation_version not in {"industry-allocation-v1", ALLOCATION_VERSION}
        )
    ):
        raise FrozenDiscoveryCompatibilityError()

    frozen = frozen_manifest(packet)
    if frozen is not None:
        from copy import deepcopy

        return deepcopy(frozen["candidateContract"])
    if allocation_version in {None, "industry-allocation-v1"}:
        from backend.integrations.industry_allocation_legacy import (
            industry_cycle_research_tasks_legacy,
        )

        return industry_cycle_research_tasks_legacy(packet, now)
    from backend.macro.industry_cycle import RISK_CHECKS
    from backend.macro.industry_contract import (
        NEWS_RELATED_INDUSTRIES,
        STANDARD_INDUSTRY_CODES,
        plan_industry_ids,
    )
    from backend.macro.research_topics import (
        digest,
        is_named_industry_scope,
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
    eligible_quantitative = set()
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
        if (
            candidate
            and candidate not in candidates
            and not any(key[0] == candidate for key in excluded)
        ):
            candidates.append(candidate)

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
        plan_industries.update(required)

    previous_risks = set()
    risk_order = []
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
            if industry not in previous_risks:
                risk_order.append(industry)
                previous_risks.add(industry)
    excluded = {}

    def exclude(identity, term, reason):
        # Only query allocation is withheld. The original packet and every
        # historical risk remain intact, with an explicit auditable exclusion.
        excluded[(identity, reason)] = {
            "industryId": identity,
            "searchTerm": term,
            "reasonCode": reason,
            "riskPreserved": identity in previous_risks,
        }
        if identity in candidates:
            candidates.remove(identity)

    pending_scopes = set()
    for row in packet.get("pendingTopicScopes", []):
        if not isinstance(row, dict):
            continue
        identity, term = row.get("industryId"), row.get("searchTerm")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 100:
            continue
        if not isinstance(term, str) or not term.strip():
            if identity in STANDARD_INDUSTRY_CODES and identity in search_names:
                term = search_names.get(identity, identity)
            else:
                exclude(
                    identity,
                    term,
                    "UNKNOWN_PRODUCT_SCOPE"
                    if identity in plan_industries
                    else "SCOPE_SEARCH_TERM_MISSING",
                )
                continue
        if len(term) > 100:
            exclude(identity, term, "SCOPE_SEARCH_TERM_INVALID")
            continue
        if identity.startswith("TOPIC_SCOPE:"):
            if identity != "TOPIC_SCOPE:" + digest(term)[:32]:
                exclude(identity, term, "DYNAMIC_SCOPE_HASH_MISMATCH")
                continue
            if not is_named_industry_scope(term):
                exclude(identity, term, "NARRATIVE_SCOPE_NOT_AUDITABLE")
                continue
        pending_scopes.add(identity)
        search_names[identity] = search_names.get(identity, term)

    # A server product annotation proves which obligation must be satisfied,
    # not that an unregistered code names an auditable industry. A validated
    # named dynamic scope has its own explicit, hash-bound search identity.
    def auditable_product_scope(industry):
        if not isinstance(industry, str) or not industry.strip():
            return False
        if (industry in STANDARD_INDUSTRY_CODES and industry in search_names) or (
            industry.startswith("TOPIC_SCOPE:") and industry in pending_scopes
        ):
            return True
        exclude(industry, search_names.get(industry), "UNKNOWN_PRODUCT_SCOPE")
        return False

    for industry in plan_industries:
        auditable_product_scope(industry)

    for industry in risk_order:
        if industry.startswith("TOPIC_SCOPE:"):
            if industry in pending_scopes:
                add(industry)
            elif not any(key[0] == industry for key in excluded):
                exclude(industry, None, "SCOPE_SEARCH_TERM_MISSING")
        elif industry in STANDARD_INDUSTRY_CODES or (
            is_named_industry_scope(industry)
            and not re.fullmatch(r"[A-Z][A-Z0-9_]*", industry)
        ):
            add(industry)
        else:
            exclude(
                industry,
                industry,
                "UNKNOWN_HISTORICAL_SCOPE"
                if re.fullmatch(r"[A-Z][A-Z0-9_]*", industry)
                else "NARRATIVE_SCOPE_NOT_AUDITABLE",
            )
    for row in packet.get("pendingTopicScopes", []):
        if (
            isinstance(row, dict)
            and isinstance(row.get("industryId"), str)
            and row["industryId"] in pending_scopes
        ):
            add(row["industryId"])
    for required in plan_requirements.values():
        for sector in sorted(required):
            add(sector)

    for row in (packet.get("quantitativeScan") or {}).get("items", []):
        if isinstance(row, dict):
            exact_sectors = plan_industry_ids(packet, row)
            sectors = exact_sectors
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
            candidate = any(
                item.get("candidate") is True or item.get("triggerState") == "CANDIDATE"
                for item in signals
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
            priority = (not ready, not candidate, min(distances, default=math.inf))
            for sector in sorted(sectors):
                if not auditable_product_scope(sector):
                    continue
                add(sector)
                quantitative[sector] = min(quantitative.get(sector, priority), priority)
            if (
                exact_sectors
                and row.get("historyQuality") == "READY"
                and signals
                and not any(
                    row.get(field) is True
                    for field in (
                        "entryBlocked",
                        "subscriptionRestricted",
                        "inSellZone",
                    )
                )
                and any(
                    not any(
                        signal.get(field) is True
                        for field in ("entryBlocked", "inSellZone")
                    )
                    for signal in signals
                )
            ):
                eligible_quantitative.update(
                    sector
                    for sector in exact_sectors
                    if auditable_product_scope(sector)
                )
    for row in packet.get("discoveryUniverse", []):
        if isinstance(row, dict):
            sector = row.get("discoverySector") or row.get("sector")
            if auditable_product_scope(sector):
                add(sector)
    for sector in sorted(news):
        add(sector)
    order = {sector: index for index, sector in enumerate(candidates)}
    historical = [sector for sector in candidates if sector in previous_risks]
    quant = sorted(
        (plan_industries & set(candidates)) - previous_risks,
        key=lambda sector: (
            sector not in eligible_quantitative,
            *quantitative.get(sector, (True, True, math.inf)),
            order[sector],
        ),
    )
    pending = [
        sector
        for sector in candidates
        if sector in pending_scopes
        and sector not in previous_risks
        and sector not in plan_industries
    ]
    fresh_news = [
        sector
        for sector in candidates
        if sector in news
        and sector not in previous_risks
        and sector not in plan_industries
        and sector not in pending_scopes
    ]
    rotation = [
        value
        for pair in zip_longest(pending, fresh_news)
        for value in pair
        if value is not None
    ]
    selected_order = historical[:MAX_INDUSTRY_CYCLE_CANDIDATES]
    remaining = MAX_INDUSTRY_CYCLE_CANDIDATES - len(selected_order)
    rotate_count = min(2, len(rotation), max(0, remaining - bool(quant)))
    quant_count = min(6, len(quant), remaining - rotate_count)
    selected_order += quant[:quant_count] + rotation[:rotate_count]
    leftovers = (
        historical[MAX_INDUSTRY_CYCLE_CANDIDATES:]
        + quant[quant_count:]
        + rotation[rotate_count:]
        + sorted(
            candidates,
            key=lambda sector: (
                0 if sector in plan_industries else 1 if sector in news else 2,
                *quantitative.get(sector, (True, True, math.inf)),
                order[sector],
            ),
        )
    )
    candidates = list(dict.fromkeys([*selected_order, *leftovers]))
    all_industries = list(candidates)
    omitted_industries = candidates[MAX_INDUSTRY_CYCLE_CANDIDATES:]
    candidates = candidates[:MAX_INDUSTRY_CYCLE_CANDIDATES]
    selected = set(candidates)
    return {
        "allocationVersion": ALLOCATION_VERSION,
        "excludedScopes": list(excluded.values()),
        "excludedScopeCount": len(excluded),
        "selectionReasons": [
            {
                "industryId": industry,
                "selected": industry in selected,
                "reasonCode": "HISTORICAL_RISK_PRIORITY"
                if industry in previous_risks
                else "QUANTITATIVE_FUND_PRIORITY"
                if industry in eligible_quantitative
                else "SERVER_FUND_SCOPE_PRIORITY"
                if industry in plan_industries
                else "BACKLOG_ROTATION"
                if industry in pending_scopes
                else "NEWS_ROTATION"
                if industry in news
                else "CATALOG_FALLBACK",
            }
            for industry in all_industries
        ],
        "required": True,
        "execution": "PUBLIC_WEB_SEARCH",
        "asOf": now.isoformat(),
        "lookbackDays": 90,
        "maximumCandidates": MAX_INDUSTRY_CYCLE_CANDIDATES,
        "omittedCandidateCount": len(omitted_industries),
        "omittedRiskCandidateCount": len(previous_risks - selected),
        "omittedIndustries": omitted_industries,
        "allIndustries": all_industries,
        "scopeSearchTerms": {
            industry: search_names.get(industry, industry)
            for industry in all_industries
        },
        "deferredPages": [
            omitted_industries[index : index + 10]
            for index in range(0, len(omitted_industries), 10)
        ],
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


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timezone-aware collection time required")
    return value.astimezone(SHANGHAI)


def official_url(uri):
    parts = urlsplit(uri)
    if (
        parts.scheme != "https"
        or parts.hostname not in OFFICIAL_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 443}
    ):
        raise ValueError("named public official HTTPS source required")
    query = urlencode(
        [
            (k, v)
            for k, v in sorted(parse_qsl(parts.query, keep_blank_values=True))
            if not k.lower().startswith("utm_")
        ]
    )
    return urlunsplit(("https", parts.hostname, parts.path or "/", query, ""))


async def fetch_text(client, uri):
    """Check every redirect and streamed byte, including with injected clients."""
    current = official_url(uri)
    for _ in range(3):
        async with client.stream(
            "GET", current, follow_redirects=False, timeout=20
        ) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                current = official_url(
                    urljoin(current, response.headers.get("location", ""))
                )
                continue
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and int(length) > MAX_BYTES:
                raise ValueError("document exceeds size limit")
            mime = response.headers.get("content-type", "text/plain").split(";")[0]
            if not (
                mime.startswith("text/")
                or mime
                in {
                    "application/xml",
                    "application/xhtml+xml",
                    "application/rss+xml",
                    "application/atom+xml",
                    "application/csv",
                }
            ):
                raise ValueError("unsupported document type")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("document exceeds size limit")
            # All selected publishers expose UTF-8; never parse arbitrary binary.
            return bytes(body).decode("utf-8-sig", errors="replace")
    raise ValueError("redirect limit exceeded")


def _soup(text):
    soup = BeautifulSoup(text, "html.parser")
    for node in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        node.decompose()
    return soup


def _compact(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def _date_in_text(text, value):
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    variants = (
        day.isoformat(),
        day.strftime("%m/%d/%Y"),
        day.strftime("%Y/%m/%d"),
        day.strftime("%B %d, %Y"),
        f"{day.year}年{day.month}月{day.day}日",
        f"{day.strftime('%B')} {day.day}, {day.year}",
        f"{day.day} {day.strftime('%B')} {day.year}",
    )
    compact = _compact(text).casefold()
    return any(_compact(value).casefold() in compact for value in variants)


def _publication_visible(text, now):
    # A known future instant cannot become current evidence by dropping its
    # hour. Naive/date-only metadata never acquires an invented timezone.
    for node in _soup(text).find_all("meta"):
        if (
            node.get("name") or node.get("property") or ""
        ).lower() not in PUBLICATION_META:
            continue
        try:
            stamp = datetime.fromisoformat(node.get("content", "").strip())
        except ValueError:
            continue
        if stamp.tzinfo is not None and stamp.utcoffset() is not None and stamp > now:
            return False
    return True


def _document(text, uri, retrieved):
    soup = _soup(text)
    title = next(
        (
            _compact(node.get_text(" "))
            for node in soup.find_all(["h1", "title"])
            if _compact(node.get_text(" "))
        ),
        "",
    )
    if not title:
        title_meta = soup.find(
            "meta", attrs={"name": re.compile(r"^ArticleTitle$", re.I)}
        )
        title = (
            _compact(title_meta.get("content", "")) if title_meta else "官方公开原文"
        )
    title = title[:200]
    day = None
    # Only publication-bearing metadata or a page date; never infer from URL.
    for node in soup.find_all("meta"):
        name = (node.get("name") or node.get("property") or "").lower()
        if name in PUBLICATION_META:
            match = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", node.get("content", ""))
            if match:
                day = "-".join(match.groups())
                break
    plain = soup.get_text(" ", strip=True)
    if day is None:
        match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", plain)
        if match:
            month, number, year = match.groups()
            day = f"{year}-{month}-{number}"
    if day is None:
        match = re.search(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
            plain,
        )
        if match:
            day = datetime.strptime(match.group(), "%B %d, %Y").date().isoformat()
    body = (
        soup.select_one(".TRS_Editor, .TRS_UEDITOR")
        or soup.find("main")
        or soup.find("article")
        or soup
    )
    paragraphs = [_compact(node.get_text(" ")) for node in body.find_all("p")]
    quote = next((value for value in paragraphs if len(value) >= 30), title)[:240]
    uri = official_url(uri)
    return {
        "id": "official-" + hashlib.sha256(uri.encode()).hexdigest()[:16],
        "url": uri,
        "title": title,
        "quote": quote,
        "publishedDate": day,
        "publishedAt": None,
        "retrievedAt": _aware(retrieved).isoformat(),
        "verification": "SOURCE_CHECKED",
        "contentHash": hashlib.sha256(text.encode()).hexdigest(),
    }


async def verify_research_sources(sources, *, now, client=None, clock=None):
    now = _aware(now)
    clock = clock or (lambda: datetime.now(SHANGHAI))
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await verify_research_sources(
                sources, now=now, client=owned, clock=clock
            )
    semaphore = asyncio.Semaphore(3)

    async def check(source):
        base = {
            "verification": "UNVERIFIED",
            "quoteMatched": False,
            "url": source.get("url"),
            "publishedDate": None,
            "publishedAt": None,
        }
        try:
            uri = official_url(source.get("url", ""))
        except (TypeError, ValueError):
            return source.get("id"), {**base, "reason": "SOURCE_NOT_ALLOWLISTED"}
        try:
            async with semaphore, asyncio.timeout(25):
                raw = await fetch_text(client, uri)
            retrieved = _aware(clock())
            doc = _document(raw, uri, retrieved)
            soup = _soup(raw)
            plain = _compact(soup.get_text(" ", strip=True)).casefold()
            quote = _compact(source.get("quote", "")).casefold()
            matched = len(quote) >= 16 and quote in plain
            requested_day = source.get("publishedDate")
            if requested_day:
                day_ok = (
                    date.fromisoformat(requested_day) <= retrieved.date()
                    and requested_day == doc["publishedDate"]
                    and _date_in_text(raw, requested_day)
                )
            else:
                day_ok = (
                    not doc["publishedDate"]
                    or date.fromisoformat(doc["publishedDate"]) <= retrieved.date()
                )
            if not matched or not day_ok or not _publication_visible(raw, retrieved):
                return source.get("id"), {
                    **base,
                    "title": doc["title"],
                    "retrievedAt": retrieved.isoformat(),
                    "contentHash": doc["contentHash"],
                    "reason": "QUOTE_OR_PUBLICATION_UNCONFIRMED",
                }
            return source.get("id"), {
                **base,
                "verification": "SOURCE_CHECKED",
                "quoteMatched": True,
                "title": doc["title"],
                "publishedDate": requested_day or doc["publishedDate"],
                "retrievedAt": retrieved.isoformat(),
                "contentHash": doc["contentHash"],
            }
        except (httpx.HTTPError, ValueError, TimeoutError, UnicodeError):
            return source.get("id"), {
                **base,
                "verification": "UNAVAILABLE",
                "reason": "SOURCE_FETCH_FAILED",
            }

    return dict(
        await asyncio.gather(
            *(check(source) for source in sources[:MAX_RESEARCH_SOURCE_CHECKS])
        )
    )


def parse_energy_csv(text, code, now):
    now = _aware(now)
    label, unit, frequency, max_age = ENERGY[code]
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames not in (["observation_date", code], ["DATE", code]):
        raise ValueError("unexpected energy series identity")
    days = {}
    for row in reader:
        raw_value = row.get(code)
        if raw_value in {".", "", None}:
            continue
        day = date.fromisoformat(row.get("observation_date", row.get("DATE", "")))
        value = float(raw_value)
        if day > now.date() or not math.isfinite(value) or value <= 0:
            raise ValueError("invalid energy observation")
        if day in days and days[day] != value:
            raise ValueError("conflicting energy observation")
        days[day] = value
    if not days:
        raise ValueError("no observed energy values")
    ordered = sorted(days.items())
    latest_day, latest = ordered[-1]
    prev_day, previous = ordered[-2] if len(ordered) >= 2 else (None, None)
    return {
        "id": code,
        "code": code,
        "label": label,
        "state": "READY" if (now.date() - latest_day).days <= max_age else "STALE",
        "unit": unit,
        "frequency": frequency,
        "value": latest,
        "asOf": latest_day.isoformat(),
        "previousValue": previous,
        "previousDate": prev_day.isoformat() if prev_day else None,
        "change": latest - previous if previous is not None else None,
        "changePct": (latest / previous - 1) * 100 if previous is not None else None,
        "sampleCount": len(ordered),
        "sourceUri": f"https://fred.stlouisfed.org/series/{code}",
        "retrievedAt": now.isoformat(),
        "publishedAt": None,
        "timeBasis": "OBSERVATION_ONLY",
    }


def _safe_error(error):
    if isinstance(error, httpx.HTTPStatusError):
        return "HTTP_" + str(error.response.status_code)
    return type(error).__name__


def _document_links(key, raw, uri, now):
    links = []
    if key in {"FED", "BLS", "EIA"}:
        root = ElementTree.fromstring(raw)
        candidates = []
        for item in root.findall(".//item"):
            published = item.findtext("pubDate")
            if published:
                try:
                    stamp = parsedate_to_datetime(published.strip())
                    if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > now:
                        continue
                except (ValueError, TypeError, OverflowError):
                    continue
            candidates.append(item.findtext("link"))
    else:
        pattern = (
            r"/recent-actions/\d{8}" if key == "OFAC" else r"/sj/zxfb/\d{6}/t\d{8}_"
        )
        candidates = [
            urljoin(uri, node["href"])
            for node in _soup(raw).find_all("a", href=True)
            if re.search(pattern, urljoin(uri, node["href"]))
        ]
    for candidate in candidates:
        try:
            link = official_url(urljoin(uri, candidate))
        except (TypeError, ValueError):
            continue
        if link not in links:
            links.append(link)
        if len(links) == 8:
            break
    return links


def _select_documents(records):
    """Exact duplicates only: a metadata audit pool and a small fair model pool."""
    unique, seen_urls, seen_hashes = [], set(), set()
    for row in records:
        if row["url"] in seen_urls or row["contentHash"] in seen_hashes:
            continue
        seen_urls.add(row["url"])
        seen_hashes.add(row["contentHash"])
        unique.append(row)
    by_source = {}
    for row in unique:
        by_source.setdefault(row["sourceKey"], []).append(row)
    for rows in by_source.values():
        rows.sort(key=lambda row: row.get("publishedDate") or "", reverse=True)
    documents = []
    for offset in range(2):
        for rows in by_source.values():
            if len(rows) > offset and len(documents) < 12:
                documents.append(rows[offset])
    fields = (
        "id",
        "url",
        "title",
        "publishedDate",
        "retrievedAt",
        "contentHash",
        "sourceKey",
    )
    return documents, [{field: row[field] for field in fields} for row in unique[:40]]


async def collect_daily_sources(now, *, client=None, clock=None):
    now = _aware(now)
    clock = clock or (lambda: datetime.now(SHANGHAI))
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await collect_daily_sources(now, client=owned, clock=clock)
    result = {
        "documents": [],
        "candidateIndex": [],
        "energy": [],
        "sourceResults": {},
        "industryCycleResearch": industry_cycle_research_tasks({}, now),
    }
    semaphore = asyncio.Semaphore(3)

    async def collect(key, uri):
        # Keep successfully retrieved originals even if the remaining source
        # work exhausts its total budget. Cancellation itself still propagates.
        records, error_class = [], None
        try:
            async with semaphore, asyncio.timeout(SOURCE_TIMEOUT_SECONDS):
                raw = await fetch_text(client, uri)
                if key in ENERGY:
                    record = parse_energy_csv(raw, key, _aware(clock()))
                    return key, [record], None
                links = _document_links(key, raw, uri, now)
                for link in links:
                    try:
                        text = await fetch_text(client, link)
                        retrieved = _aware(clock())
                        if not _publication_visible(text, retrieved):
                            raise ValueError("future publication metadata")
                        doc = _document(text, link, retrieved)
                        published = doc["publishedDate"]
                        if published and not (
                            now.date() - timedelta(days=14)
                            <= date.fromisoformat(published)
                            <= retrieved.date()
                        ):
                            continue
                        records.append({**doc, "sourceKey": key})
                    except (httpx.HTTPError, ValueError, TimeoutError) as error:
                        error_class = _safe_error(error)
                        continue
                if not records:
                    raise ValueError("no confirmed recent official documents")
                return key, records, error_class
        except (
            httpx.HTTPError,
            ValueError,
            TimeoutError,
            ElementTree.ParseError,
        ) as error:
            return key, records, _safe_error(error)

    energy_uris = {
        code: "https://fred.stlouisfed.org/graph/fredgraph.csv?"
        + urlencode(
            {
                "id": code,
                "cosd": (now.date() - timedelta(days=90)).isoformat(),
            }
        )
        for code in ENERGY
    }
    outputs = await asyncio.gather(
        *(collect(key, uri) for key, uri in {**INDEXES, **energy_uris}.items())
    )
    candidates = []
    for key, records, error in outputs:
        result["sourceResults"][key] = (
            {"status": "FAILED", "errorClass": error}
            if error
            else {"status": "SUCCESS"}
        )
        if key in ENERGY:
            result["energy"].extend(records)
        else:
            candidates.extend(records)
    result["documents"], result["candidateIndex"] = _select_documents(candidates)
    return result
