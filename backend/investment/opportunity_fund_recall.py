"""Read-only, topic-linked fund recall; recommendations confer no trade authority.

The complete master is searched before per-keyword bounds. The frozen 46-fund
NAV collection budget deliberately does not limit this independent projection.
"""

from copy import deepcopy
from datetime import date, timedelta
import re
from uuid import UUID
from zoneinfo import ZoneInfo

from backend.investment.discovery_limits import (
    OPPORTUNITY_CANDIDATES_PER_KEYWORD,
    OPPORTUNITY_FUNDS_PER_TOPIC,
    OPPORTUNITY_TOPIC_LIMIT,
)
from backend.investment.dynamic_discovery import (
    CORE_FUND_CODES,
    _FOREIGN_CURRENCY,
    _share_priority,
    _stamp,
)
from backend.jobs.opportunity_market import read_fund_subscription_status
from backend.investment.opportunity_fund_matching import (
    ASSOCIATION_ORDER,
    fallback as _fallback,
    fund_group_key,
    regional_bond_terms as _regional_bond_terms,
    share_class_priority,
    terms as _terms,
    mentioned_codes,
    texts as _texts,
)

RECALL_MATCH_VERSION = "topic-fund-discovery-v3"
_DISCOVERY_GROUP_LIMIT = 24
_SHARES_PER_GROUP = 4

_SOURCE = "https://fund.eastmoney.com/js/fundcode_search.js"
_STATUS_MAX_AGE = timedelta(days=3)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_EXCLUDED_RECALL_NAME = re.compile(
    r"后端|后收费|清盘|清算|终止|退市|(?:^|[^A-Za-z])B\s*(?:类|份额)?"
    r"(?:[（(][^（）()]{0,30}[)）])?(?:人民币|美元|港币)?$",
    re.IGNORECASE,
)
_CATALOG_SELECT = r"""SELECT c.*,m.updated_at AS identity_verified_at,
      count(*) OVER(PARTITION BY c.market,c.fund_code) AS mapping_count,
      lower(regexp_replace(regexp_replace(c.fund_name,'[[:space:]]+','','g'),
        '(.*([^A-Za-z]|ETF))[ACEIY](类|份额)?([（(]人民币[)）]|人民币)?$','\1','i')) AS fund_group_key
    FROM reference.research_fund_catalog c
    JOIN reference.fund_master m ON m.fund_code=c.fund_code
    WHERE c.market='FUND_CN' AND c.trade_form IN ('FUND','LOF')
      AND c.fund_code~'^[0-9]{6}$' AND c.currency='CNY' AND m.updated_at<=$1
      AND c.fund_name !~* '清盘|清算|终止|退市|后端|后收费'
      AND c.fund_name !~ '美元|港币|港元|外币|现汇|现钞|欧元|英镑|日元|澳元|加元|瑞郎'
      AND NOT EXISTS(SELECT 1 FROM reference.instrument_aliases a
        JOIN reference.instruments i ON i.id=a.instrument_id
        WHERE a.alias_type='TICKER' AND a.alias_value=c.fund_code
          AND i.asset_type='OFF_EXCHANGE_FUND' AND i.lifecycle_status<>'ACTIVE'
          AND a.effective_from<=($1 AT TIME ZONE 'Asia/Shanghai')::date
          AND (a.effective_to IS NULL OR a.effective_to>=($1 AT TIME ZONE 'Asia/Shanghai')::date))"""

_LOOKUP_SQL = (
    """WITH keywords AS MATERIALIZED (
    SELECT DISTINCT unnest($2::text[]) AS keyword
), name_hits AS MATERIALIZED (
    SELECT m.fund_code,k.keyword FROM keywords k
    JOIN reference.fund_master m ON m.fund_name ILIKE '%'||k.keyword||'%'
    WHERE m.updated_at<=$1
    UNION
    SELECT a.alias_value,k.keyword FROM keywords k
    JOIN reference.instruments i ON i.canonical_name ILIKE '%'||k.keyword||'%'
    JOIN reference.instrument_aliases a ON a.instrument_id=i.id
    WHERE i.asset_type='OFF_EXCHANGE_FUND' AND i.lifecycle_status='ACTIVE'
      AND a.alias_type='TICKER' AND i.primary_market='FUND_CN'
      AND a.effective_from<=($1 AT TIME ZONE 'Asia/Shanghai')::date
      AND (a.effective_to IS NULL OR a.effective_to>=($1 AT TIME ZONE 'Asia/Shanghai')::date)
), index_hits AS MATERIALIZED (
    SELECT DISTINCT ON(p.fund_code,k.keyword) p.fund_code,k.keyword,i.name,p.source_uri
    FROM keywords k JOIN reference.research_indices i ON i.name ILIKE '%'||k.keyword||'%'
    JOIN reference.research_product_index_links p ON p.index_id=i.id
    WHERE p.market='FUND_CN' AND p.relationship_type IN ('TRACKS','ETF_LINK')
      AND p.verified_at<=$1
      AND p.effective_from<=($1 AT TIME ZONE 'Asia/Shanghai')::date
      AND (p.effective_to IS NULL OR p.effective_to>=($1 AT TIME ZONE 'Asia/Shanghai')::date)
    ORDER BY p.fund_code,k.keyword,p.verified_at DESC,i.id
), hit_keywords AS MATERIALIZED (
    SELECT fund_code,keyword FROM name_hits
    UNION SELECT fund_code,keyword FROM index_hits
), candidate_codes AS MATERIALIZED (
    SELECT fund_code FROM hit_keywords UNION SELECT unnest($3::text[])
), catalog AS ("""
    + _CATALOG_SELECT
    + """
    AND c.fund_code IN (SELECT fund_code FROM candidate_codes)
), matched AS (
    SELECT c.*,k.keyword AS matched_keyword,ix.name AS matched_index_name,
      ix.source_uri AS matched_index_source_uri,
      CASE WHEN ix.name IS NOT NULL THEN 0 ELSE 1 END AS association_priority,
      CASE WHEN status.purchase_status IN ('OPEN','LIMITED')
        AND status.retrieved_at BETWEEN $1-interval '3 days' AND $1
        AND CASE WHEN status.daily_purchase_limit ~ '^[0-9]+([.][0-9]+)?$'
          THEN status.daily_purchase_limit::numeric>100
          ELSE status.daily_purchase_limit IS NULL AND status.purchase_status='OPEN' END
        THEN 0 ELSE 1 END AS subscription_priority,
      CASE WHEN c.fund_name ~* 'A(类|份额)?([（(]人民币[)）]|人民币)?$' THEN 0
        WHEN c.fund_name ~* 'C(类|份额)?([（(]人民币[)）]|人民币)?$' THEN 1 ELSE 2 END AS share_priority
    FROM catalog c JOIN hit_keywords k ON k.fund_code=c.fund_code
    LEFT JOIN index_hits ix ON ix.fund_code=c.fund_code AND ix.keyword=k.keyword
    LEFT JOIN LATERAL (
      SELECT s.purchase_status,s.daily_purchase_limit,s.retrieved_at
      FROM reference.fund_subscription_status s WHERE s.instrument_id=c.instrument_id
        AND s.effective_from<=($1 AT TIME ZONE 'Asia/Shanghai')::date
        AND (s.effective_to IS NULL OR s.effective_to>=($1 AT TIME ZONE 'Asia/Shanghai')::date)
        AND (s.retrieved_at IS NULL OR s.retrieved_at<=$1)
      ORDER BY s.effective_from DESC,s.retrieved_at DESC NULLS LAST LIMIT 1
    ) status ON TRUE
    WHERE c.mapping_count=1 AND (c.fund_name ILIKE '%'||k.keyword||'%' OR ix.name IS NOT NULL)
      AND c.fund_name !~* '(^|[^A-Za-z])B[[:space:]]*(类|份额)?([（(][^（）()]{0,30}[)）])?(人民币|美元|港币)?$'
), family_best AS (
    SELECT DISTINCT ON(matched_keyword,fund_group_key)
      matched_keyword,fund_group_key,association_priority,subscription_priority,share_priority,fund_code
    FROM matched ORDER BY matched_keyword,fund_group_key,association_priority,
      subscription_priority,share_priority,fund_code
), ranked AS (
    SELECT *,row_number() OVER(PARTITION BY matched_keyword ORDER BY association_priority,
      subscription_priority,share_priority,fund_code) AS keyword_rank FROM family_best
), selected AS (
    SELECT m.instrument_id,m.fund_code,m.fund_name,m.fund_type,m.trade_form,m.market,m.currency,
      m.identity_verified_at,m.mapping_count,m.matched_keyword,m.matched_index_name,m.matched_index_source_uri
    FROM matched m JOIN ranked r USING(matched_keyword,fund_group_key) WHERE r.keyword_rank<=$4
    UNION ALL
    SELECT instrument_id,fund_code,fund_name,fund_type,trade_form,market,currency,
      identity_verified_at,mapping_count,''::text,NULL::text,NULL::text
    FROM catalog WHERE fund_code=ANY($3::text[]) AND mapping_count=1
) SELECT *, (fund_type ILIKE '%QDII%' OR fund_name ILIKE '%QDII%') AS overseas_calendar_required
  FROM selected ORDER BY fund_code,matched_keyword"""
)

# Cached candidate identities are rechecked by code without fuzzy matching.
_IDENTITY_SQL = (
    "WITH catalog AS ("
    + _CATALOG_SELECT
    + """
    AND c.fund_code=ANY($2::text[])
) SELECT *, (fund_type ILIKE '%QDII%' OR fund_name ILIKE '%QDII%') AS overseas_calendar_required
  FROM catalog WHERE mapping_count=1"""
)


def _identities(rows, now):
    products, bad, owners = {}, set(), {}
    for row in rows:
        code, name = row.get("fund_code"), str(row.get("fund_name") or "")
        verified = _stamp(row.get("identity_verified_at"))
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"[0-9]{6}", code)
            or row.get("market") != "FUND_CN"
            or row.get("currency") != "CNY"
            or row.get("trade_form") not in {"FUND", "LOF"}
            or row.get("mapping_count") != 1
            or verified is None
            or verified > now
            or row.get("lifecycle_status", "ACTIVE") != "ACTIVE"
            or not name.strip()
            or _EXCLUDED_RECALL_NAME.search(name)
            or _FOREIGN_CURRENCY.search(name)
        ):
            if isinstance(code, str) and row.get("mapping_count") != 1:
                bad.add(code)
            continue
        kind = "fund" if row.get("instrument_id") else "nav"
        subject = str(row["instrument_id"]) if kind == "fund" else code
        if kind == "fund":
            try:
                UUID(subject)
            except (ValueError, TypeError):
                bad.add(code)
                continue
        identity = kind, subject
        if code in products and products[code]["identity"] != identity:
            bad.add(code)
        if identity in owners and owners[identity] != code:
            bad.update((code, owners[identity]))
        owners[identity] = code
        product = products.setdefault(
            code,
            {
                "identity": identity,
                "fundCode": code,
                "name": name[:200],
                "subjectKind": kind,
                "subjectId": subject,
                "market": "FUND_CN",
                "identityVerifiedAt": verified.isoformat(),
                "sourceUri": _SOURCE,
                "overseasCalendarRequired": row.get("overseas_calendar_required")
                is True,
                "matches": {},
            },
        )
        keyword = row.get("matched_keyword")
        if isinstance(keyword, str) and keyword:
            # Recheck the returned text, including a real verified index name.
            index = row.get("matched_index_name")
            if isinstance(index, str) and keyword.casefold() in index.casefold():
                product["matches"][keyword] = (
                    "跟踪指数",
                    index,
                    row.get("matched_index_source_uri") or _SOURCE,
                )
            elif (
                keyword.casefold() in name.casefold()
                and product["matches"].get(keyword, (None,))[0] != "跟踪指数"
            ):
                product["matches"][keyword] = ("名称", name, _SOURCE)
    return {code: row for code, row in products.items() if code not in bad}


def _subscription(raw, now):
    retrieved = _stamp(raw.get("retrievedAt")) if isinstance(raw, dict) else None
    if retrieved is None or not now - _STATUS_MAX_AGE <= retrieved <= now:
        return {
            "status": "UNKNOWN",
            "restricted": True,
            "label": "申购状态待核验",
            "dailyLimitCny": None,
            "retrievedAt": None,
        }, False
    purchase = raw["purchaseStatus"]
    can_subscribe = not raw["restricted"] and (
        purchase == "OPEN"
        or purchase == "LIMITED"
        and raw.get("dailyLimitCny") is not None
    )
    return {
        "status": purchase,
        "restricted": raw["restricted"],
        "label": raw.get("label") or ("限购额度待核验" if not can_subscribe else None),
        "dailyLimitCny": raw.get("dailyLimitCny"),
        "retrievedAt": retrieved.isoformat(),
    }, can_subscribe


def _local_prices(packet, now):
    from backend.investment.quantitative_scan import public_quantitative_scan

    try:
        public = public_quantitative_scan(packet.get("quantitativeScan"), now)
    except (TypeError, ValueError):
        return {}, {}
    fresh, stale = {}, {}
    for row in public["items"] if public else []:
        retrieved = _stamp(row.get("retrievedAt"))
        if (
            retrieved is None
            or now - retrieved > timedelta(hours=36)
            or now.astimezone(_SHANGHAI).date() - date.fromisoformat(row["asOf"])
            > timedelta(days=10)
        ):
            stale[row["fundCode"]] = (row["subjectKind"], row["subjectId"])
        else:
            fresh[row["fundCode"]] = row
    return fresh, stale


def _price_rank(price):
    if not price:
        return (True, True, float("inf"))
    signals = price.get("signals", [])
    return (
        False,
        not any(row.get("inEntryZone") is True for row in signals),
        min((row["distanceToEntryPct"] for row in signals), default=float("inf")),
    )


def _quantitative_score(price, packet, now):
    if not price:
        return None, {}
    from backend.investment.opportunity_scoring import score_plan

    scored = score_plan(price, packet, now)
    return scored.get("opportunityScore"), {
        "scoreMethodVersion": scored.get("scoreMethodVersion"),
        "scoreBreakdown": deepcopy(scored.get("scoreBreakdown") or {}),
        "quantitativeRiskPenaltyApplied": -(scored.get("scoreBreakdown") or {}).get(
            "industryCyclePenalty", 0
        ),
    }


def _bounded_candidates(candidates):
    """Retain 24 independent product families and their four main share classes."""
    groups = {}
    for row in candidates:
        groups.setdefault(row["fundGroupKey"], []).append(row)
    ordered = sorted(
        groups.values(),
        key=lambda shares: min(
            (ASSOCIATION_ORDER[row["associationLevel"]], row["fundCode"])
            for row in shares
        ),
    )
    return [
        row
        for shares in ordered[:_DISCOVERY_GROUP_LIMIT]
        for row in sorted(
            shares,
            key=lambda row: (share_class_priority(row["name"]), row["fundCode"]),
        )[:_SHARES_PER_GROUP]
    ]


def _outcome(key, funds, indirect, unavailable=None):
    if funds:
        status, unavailable = "MATCHED", None
        message = "已找到主题相关真实基金；申购状态与立即买入条件分别展示。"
    elif indirect:
        status, unavailable = "INDIRECT_ONLY", "INDIRECT_EXPOSURE_ONLY"
        message = "仅找到可解释的间接资产观察，不作为主题推荐基金。"
    else:
        status = "UNAVAILABLE"
        unavailable = unavailable or "NO_VERIFIED_FUND_MATCH"
        message = (
            "未找到主题直接匹配基金，当前缺少可说明的地域或资产关联，不能用其他地区宽基代替。"
            if unavailable == "NO_EXPLAINABLE_ASSET_MAPPING"
            else "当前真实基金主库中未找到可核验的主题场外基金，请补充基金主数据后重试。"
        )
    return {
        "topicId": key,
        "status": status,
        "funds": funds,
        "indirectFunds": indirect,
        "unavailableReason": unavailable,
        "message": message,
    }


async def discover_topic_funds(conn, topics, packet, now):
    """Search the complete master once and return JSON-safe, stable match facts.

    This phase is suitable for archive-time caching. Subscription availability,
    NAV, scores and trading permissions are intentionally added only at read time.
    """
    from backend.investment.opportunity_fund_matching import has_overseas_region

    now = _stamp(now)
    if now is None:
        raise ValueError("topic fund recall requires an aware clock")
    if not isinstance(topics, list) or len(topics) > OPPORTUNITY_TOPIC_LIMIT:
        raise ValueError("topic fund recall exceeds its topic budget")
    prepared, seen = [], set()
    for row in topics:
        if not isinstance(row, dict):
            continue
        key = row.get("topicId") or row.get("key")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("invalid or duplicate recall topic identity")
        seen.add(key)
        fallback, label = _fallback(row)
        prepared.append((key, row, _terms(row), fallback, label))
    if not prepared:
        return {}
    packet = packet if isinstance(packet, dict) else {}
    sectors = (
        {
            row["fundCode"]: row.get("discoverySector")
            for row in packet.get("discoveryUniverse", [])
            if isinstance(row, dict) and isinstance(row.get("fundCode"), str)
        }
        if isinstance(packet.get("discoveryUniverse"), list)
        else {}
    )
    core_codes = set(CORE_FUND_CODES) | set(sectors)
    narrative_codes = {code for _, row, _, _, _ in prepared for code in mentioned_codes(row)}
    keywords = list(
        dict.fromkeys(
            word for _, _, terms, fallback, _ in prepared for word in terms + fallback
        )
    )
    rows = await conn.fetch(
        _LOOKUP_SQL,
        now,
        keywords,
        sorted(core_codes | narrative_codes),
        OPPORTUNITY_CANDIDATES_PER_KEYWORD,
    )
    products = _identities(rows, now)
    result = {}
    for key, topic, terms, fallback, fallback_label in prepared:
        mentions = set(mentioned_codes(topic))
        match = topic.get("match") if isinstance(topic.get("match"), dict) else {}
        industries = set(_texts(match.get("industryIds")))
        regional_bonds = _regional_bond_terms(topic) is not None
        direct_candidates, indirect_candidates = [], []
        for code, product in products.items():
            direct_matches = [
                product["matches"][word] for word in terms if word in product["matches"]
            ]
            direct = min(
                direct_matches,
                key=lambda found: found[0] != "跟踪指数",
                default=None,
            )
            category = not regional_bonds and sectors.get(code) in industries
            broad = next(
                (
                    product["matches"][word]
                    for word in fallback
                    if word in product["matches"]
                ),
                None,
            )
            title = str(topic.get("title") or key)[:200]
            if code in mentions:
                association = "NAME_CANDIDATE"
                reason = f"主题「{title}」正文提及基金代码{code}，已核验基金主库身份；不代表已核验主题持仓。"
            elif direct and direct[0] == "跟踪指数":
                association = "DIRECT_INDEX"
                reason = f"主题「{title}」匹配已核验跟踪指数「{direct[1]}」。"
            elif category:
                association = "CATEGORY_MATCH"
                reason = f"主题「{title}」与基金已登记行业「{sectors[code]}」一致。"
            elif direct:
                association = "NAME_CANDIDATE"
                reason = f"主题「{title}」匹配基金名称「{direct[1]}」，作为名称候选。"
            elif broad:
                association = "BROAD_FALLBACK"
                reason = f"主题「{title}」未找到直接匹配产品，{fallback_label}「{broad[1]}」仅作间接资产观察。"
            else:
                continue
            level = (
                "BROAD_FALLBACK"
                if association == "BROAD_FALLBACK"
                else "CORE_MATCH"
                if code in core_codes
                else "DYNAMIC_MATCH"
            )
            candidate = {
                **{
                    field: product[field]
                    for field in (
                        "fundCode",
                        "name",
                        "subjectKind",
                        "subjectId",
                        "market",
                        "identityVerifiedAt",
                        "overseasCalendarRequired",
                    )
                },
                "salesChannel": "OFF_EXCHANGE",
                "fundGroupKey": fund_group_key(product["name"]),
                "associationLevel": association,
                "matchLevel": level,
                "matchReason": reason[:600],
                "sourceUri": (direct or broad or (None, None, _SOURCE))[2][:2000],
            }
            target = (
                indirect_candidates
                if association == "BROAD_FALLBACK"
                else direct_candidates
            )
            target.append(candidate)
        # Any specific association excludes broad proxies, irrespective of status.
        funds = _bounded_candidates(direct_candidates)
        indirect = [] if direct_candidates else _bounded_candidates(indirect_candidates)
        unavailable = (
            "NO_EXPLAINABLE_ASSET_MAPPING"
            if regional_bonds or has_overseas_region(topic) and not fallback
            else "NO_VERIFIED_FUND_MATCH"
        )
        result[key] = _outcome(key, funds, indirect, unavailable)
    return result


def _rank_candidate(row, prices):
    return (
        ASSOCIATION_ORDER[row["associationLevel"]],
        not row["canSubscribe"],
        -(row["quantitativeScore"] if row["quantitativeScore"] is not None else -1),
        _price_rank(prices.get(row["fundCode"]))
        if row["confirmedNav"]
        else _price_rank(None),
        _share_priority(row["name"]),
        row["fundCode"],
    )


def _display_groups(candidates, prices, *, limit=OPPORTUNITY_FUNDS_PER_TOPIC):
    groups = {}
    for row in candidates:
        groups.setdefault(row["fundGroupKey"], []).append(row)
    selected = []
    for shares in groups.values():
        # A suspended A share must not hide its currently available C share.
        shares.sort(
            key=lambda row: (
                not row["canSubscribe"],
                share_class_priority(row["name"]),
                _rank_candidate(row, prices),
            )
        )
        preferred = shares[0]
        preferred["alternativeShareClasses"] = [
            {
                field: row[field]
                for field in (
                    "fundCode",
                    "name",
                    "subjectKind",
                    "subjectId",
                    "subscription",
                    "canSubscribe",
                )
            }
            for row in shares[1:]
        ]
        selected.append(preferred)
    return sorted(selected, key=lambda row: _rank_candidate(row, prices))[
        :limit
    ]


async def enrich_topic_funds(conn, discovered, packet, now, *, display_limit=OPPORTUNITY_FUNDS_PER_TOPIC):
    """Recheck cached identities and attach current local trade and price facts.

    Both database reads use the bounded candidate codes. Neither query repeats
    market-wide name matching. A cache is never evidence of current availability.
    """
    now = _stamp(now)
    if now is None:
        raise ValueError("topic fund enrichment requires an aware clock")
    if not isinstance(discovered, dict) or len(discovered) > OPPORTUNITY_TOPIC_LIMIT:
        raise ValueError("topic fund enrichment exceeds its topic budget")
    codes = sorted(
        {
            fund["fundCode"]
            for row in discovered.values()
            if isinstance(row, dict)
            for field in ("funds", "indirectFunds")
            for fund in row.get(field, [])
            if isinstance(fund, dict)
            and isinstance(fund.get("fundCode"), str)
            and re.fullmatch(r"[0-9]{6}", fund["fundCode"])
        }
    )
    if (
        len(codes)
        > OPPORTUNITY_TOPIC_LIMIT * _DISCOVERY_GROUP_LIMIT * _SHARES_PER_GROUP
    ):
        raise ValueError("topic fund enrichment exceeds its candidate budget")
    products = (
        _identities(await conn.fetch(_IDENTITY_SQL, now, codes), now) if codes else {}
    )
    statuses = await read_fund_subscription_status(conn, now, list(products))
    packet = packet if isinstance(packet, dict) else {}
    prices, stale_prices = _local_prices(packet, now)
    result = {}
    for key, discovery in discovered.items():
        enriched = {"funds": [], "indirectFunds": []}
        for field in enriched:
            for cached in discovery.get(field, []):
                code = cached.get("fundCode")
                product = products.get(code)
                if (
                    not product
                    or (cached.get("subjectKind"), cached.get("subjectId"))
                    != product["identity"]
                    or cached.get("name") != product["name"]
                    or cached.get("associationLevel") not in ASSOCIATION_ORDER
                ):
                    continue
                subscription, can_subscribe = _subscription(statuses.get(code), now)
                price = prices.get(code)
                if (
                    price
                    and (price["subjectKind"], price["subjectId"])
                    != product["identity"]
                ):
                    price = None
                score, scoring = _quantitative_score(price, packet, now)
                missing = (
                    ([] if score is not None else ["QUANTITATIVE_SCORE_UNAVAILABLE"])
                    if price
                    else ["LOCAL_QUANTITATIVE_EVIDENCE_MISSING"]
                )
                warnings = [
                    "主题关联仅提供研究候选，买入仍须独立核验净值区间与交易条件。"
                ]
                if stale_prices.get(code) == product["identity"]:
                    missing.append("STALE_QUANTITATIVE_EVIDENCE")
                    warnings.append(
                        "冻结净值或抓取时间已过期，保留主题推荐，当前不展示旧净值区间或评分。"
                    )
                if subscription["status"] == "UNKNOWN":
                    missing.append("CURRENT_SUBSCRIPTION_STATUS_UNVERIFIED")
                    warnings.append("未取得三日内有效申购状态，不能认定目前开放申购。")
                elif not can_subscribe:
                    warnings.append(subscription["label"] or "基金申购受限。")
                if cached["associationLevel"] == "BROAD_FALLBACK":
                    warnings.append(
                        "宽基或大类资产仅提供间接敞口，不代表纯主题持仓或收益。"
                    )
                elif cached["associationLevel"] == "NAME_CANDIDATE":
                    warnings.append("名称关键词匹配不等于已核验基金当前持仓。")
                enriched[field].append(
                    {
                        **deepcopy(cached),
                        "identityVerifiedAt": product["identityVerifiedAt"],
                        "salesChannel": "OFF_EXCHANGE",
                        "subscription": subscription,
                        "canSubscribe": can_subscribe,
                        "canBuyNow": False,
                        "actionable": False,
                        "quantitativeScore": score,
                        **scoring,
                        "missingEvidence": missing,
                        "riskWarnings": warnings,
                        "confirmedNav": {
                            "value": price["currentPrice"],
                            "asOf": price["asOf"],
                            "retrievedAt": price.get("retrievedAt"),
                            "sourceUri": price.get("sourceUri"),
                        }
                        if price
                        else None,
                        "referenceTracks": deepcopy(price["tracks"]) if price else [],
                    }
                )
        funds = _display_groups(enriched["funds"], prices, limit=display_limit)
        # Never reinterpret a formerly specific mapping as broad after live removal.
        indirect = (
            []
            if discovery.get("funds")
            else _display_groups(enriched["indirectFunds"], prices, limit=display_limit)
        )
        unavailable = discovery.get("unavailableReason")
        if unavailable == "INDIRECT_EXPOSURE_ONLY":
            unavailable = "NO_VERIFIED_FUND_MATCH"
        outcome = _outcome(key, funds, indirect, unavailable)
        if funds and not any(row["canSubscribe"] for row in funds):
            states = {row["subscription"]["status"] for row in funds}
            outcome["unavailableReason"] = (
                "SUBSCRIPTION_UNVERIFIED"
                if "UNKNOWN" in states
                else "SUBSCRIPTION_SUSPENDED"
                if states == {"SUSPENDED"}
                else "SUBSCRIPTION_RESTRICTED"
            )
            outcome["message"] = (
                "已展示相关真实基金，但申购状态未知、暂停或额度受限，暂未找到已确认可正常申购的产品。"
            )
        result[key] = outcome
    return result


async def recall_topic_funds(conn, topics, packet, now):
    """Uncached convenience entry point; archived views cache discovery only."""
    discovered = await discover_topic_funds(conn, topics, packet, now)
    return await enrich_topic_funds(conn, discovered, packet, now)
