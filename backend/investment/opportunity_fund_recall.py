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
    OPPORTUNITY_KEYWORDS_PER_TOPIC,
    OPPORTUNITY_TOPIC_LIMIT,
)
from backend.investment.dynamic_discovery import (
    CORE_FUND_CODES,
    _FOREIGN_CURRENCY,
    _THEMES,
    _share_priority,
    _stamp,
)
from backend.jobs.opportunity_market import read_fund_subscription_status

_SOURCE = "https://fund.eastmoney.com/js/fundcode_search.js"
_STATUS_MAX_AGE = timedelta(days=3)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_EXCLUDED_RECALL_NAME = re.compile(
    r"后端|后收费|清盘|清算|终止|退市|(?:^|[^A-Za-z])B\s*(?:类|份额)?"
    r"(?:[（(][^（）()]{0,30}[)）])?(?:人民币|美元|港币)?$",
    re.IGNORECASE,
)
_SPLIT = re.compile(
    r"[\s，,、。:：;；/()（）]+|国产替代|商业化|产业|行业|政策|新增|订单|需求|利好|受益|加速|增长|带动|推动"
)
_LEVEL = {"CORE_MATCH": 0, "DYNAMIC_MATCH": 1, "BROAD_FALLBACK": 2}

# These are explainable exposure search hints, never fabricated product links.
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


def _texts(value):
    return (
        [v for v in value[:32] if isinstance(v, str)] if isinstance(value, list) else []
    )


def _topic_text(topic):
    match = topic.get("match") if isinstance(topic.get("match"), dict) else {}
    return " ".join(
        [
            str(topic.get(field) or "")[:4000]
            for field in ("title", "summary", "thesis", "impact")
        ]
        + _texts(topic.get("industryClaims"))
        + _texts(match.get("unresolvedClaims"))
    )


def _regional_bond_terms(topic):
    """An overseas rate exposure cannot borrow a generic domestic bond link."""
    text = _topic_text(topic)
    if not re.search(r"债|固收", text) or not re.search(
        r"美国|美债|美元债|日本|日债|欧洲|欧元区|欧债|德国|德债|法国|英国|英债|"
        r"海外|国际|全球|境外|印度|越南|东南亚|巴西|韩国|台湾|澳大利亚|加拿大|俄罗斯|新兴市场",
        text,
    ):
        return None
    if re.search(r"美国|美债", text) and re.search(r"国债|美债", text):
        return ["美国国债", "美债"]
    return []


def _terms(topic):
    regional_bonds = _regional_bond_terms(topic)
    if regional_bonds is not None:
        return regional_bonds
    match = topic.get("match") if isinstance(topic.get("match"), dict) else {}
    text = _topic_text(topic)
    explicit = (
        [
            str(row.get("searchTerm") or "")
            for row in match.get("proposedScopes", [])[:8]
            if isinstance(row, dict)
        ]
        if isinstance(match.get("proposedScopes"), list)
        else []
    )
    explicit += _texts(match.get("unresolvedClaims")) + _texts(
        topic.get("industryClaims")
    )
    hints = [
        keyword
        for code, pattern, keywords in _THEMES
        if re.search(pattern, text, re.IGNORECASE | re.ASCII)
        or code in _texts(match.get("industryIds"))
        for keyword in keywords
    ]
    if re.search(r"芯片|半导体|SCI_TECH_CHIP", text, re.IGNORECASE):
        hints += ["芯片", "半导体"]
    candidates = explicit + hints + _SPLIT.split(str(topic.get("title") or ""))
    return list(
        dict.fromkeys(
            word.strip()
            for word in candidates
            if isinstance(word, str)
            and re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff·+ -]{2,32}", word.strip())
            and not word.strip().isdigit()
        )
    )[:OPPORTUNITY_KEYWORDS_PER_TOPIC]


def _fallback(topic):
    regional_bonds = _regional_bond_terms(topic)
    if regional_bonds is not None:
        return regional_bonds, "美国国债资产" if regional_bonds else None
    text = _topic_text(topic)
    for pattern, keywords, label in _FALLBACKS:
        if re.search(pattern, text):
            return list(keywords), label
    if re.search(
        r"海外|国际|全球|境外|英国|印度|越南|东南亚|巴西|韩国|台湾|澳大利亚|加拿大|俄罗斯|新兴市场",
        text,
    ):
        return [], None
    return ["中证A500", "沪深300", "中证500"], "A股宽基"


_LOOKUP_SQL = """WITH catalog AS (
    SELECT c.*,m.updated_at AS identity_verified_at,
      count(*) OVER(PARTITION BY c.market,c.fund_code) AS mapping_count
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
          AND (a.effective_to IS NULL OR a.effective_to>=($1 AT TIME ZONE 'Asia/Shanghai')::date))
), matched AS (
    SELECT c.*,k AS matched_keyword,ix.name AS matched_index_name,
      ix.source_uri AS matched_index_source_uri,
      CASE WHEN status.purchase_status IN ('OPEN','LIMITED')
        AND status.retrieved_at BETWEEN $1-interval '3 days' AND $1
        AND CASE WHEN status.daily_purchase_limit ~ '^[0-9]+([.][0-9]+)?$'
          THEN status.daily_purchase_limit::numeric>100
          ELSE status.daily_purchase_limit IS NULL AND status.purchase_status='OPEN' END
        THEN 0 ELSE 1 END AS subscription_priority,
      CASE WHEN c.fund_name ~* 'ETF.*联接.*A([（(].*[)）])?$' THEN 0
        WHEN c.fund_name ~* '(指数|ETF).*A([（(].*[)）])?$' THEN 1
        WHEN c.fund_name ~* 'A([（(].*[)）])?$' THEN 2 ELSE 3 END AS share_priority
    FROM catalog c CROSS JOIN unnest($2::text[]) k
    LEFT JOIN LATERAL (
      SELECT i.name,p.source_uri FROM reference.research_product_index_links p
      JOIN reference.research_indices i ON i.id=p.index_id
      WHERE p.fund_code=c.fund_code AND p.market=c.market
        AND p.relationship_type IN ('TRACKS','ETF_LINK') AND p.verified_at<=$1
        AND p.effective_from<=($1 AT TIME ZONE 'Asia/Shanghai')::date
        AND (p.effective_to IS NULL OR p.effective_to>=($1 AT TIME ZONE 'Asia/Shanghai')::date)
        AND i.name ILIKE '%'||k||'%'
      ORDER BY p.verified_at DESC,i.id LIMIT 1
    ) ix ON TRUE
    LEFT JOIN LATERAL (
      SELECT s.purchase_status,s.daily_purchase_limit,s.retrieved_at
      FROM reference.fund_subscription_status s WHERE s.instrument_id=c.instrument_id
        AND s.effective_from<=($1 AT TIME ZONE 'Asia/Shanghai')::date
        AND (s.effective_to IS NULL OR s.effective_to>=($1 AT TIME ZONE 'Asia/Shanghai')::date)
        AND (s.retrieved_at IS NULL OR s.retrieved_at<=$1)
      ORDER BY s.effective_from DESC,s.retrieved_at DESC NULLS LAST LIMIT 1
    ) status ON TRUE
    WHERE c.mapping_count=1 AND (c.fund_name ILIKE '%'||k||'%' OR ix.name IS NOT NULL)
      AND c.fund_name !~* '(^|[^A-Za-z])B[[:space:]]*(类|份额)?([（(][^（）()]{0,30}[)）])?(人民币|美元|港币)?$'
), ranked AS (
    SELECT *,row_number() OVER(PARTITION BY matched_keyword
      ORDER BY subscription_priority,share_priority,fund_code) AS keyword_rank
    FROM matched
), selected AS (
    SELECT instrument_id,fund_code,fund_name,fund_type,trade_form,market,currency,
      identity_verified_at,mapping_count,matched_keyword,matched_index_name,matched_index_source_uri
    FROM ranked WHERE keyword_rank<=$4
    UNION ALL
    SELECT instrument_id,fund_code,fund_name,fund_type,trade_form,market,currency,
      identity_verified_at,mapping_count,''::text,NULL::text,NULL::text
    FROM catalog WHERE fund_code=ANY($3::text[]) AND mapping_count=1
) SELECT *, (fund_type ILIKE '%QDII%' OR fund_name ILIKE '%QDII%') AS overseas_calendar_required
  FROM selected ORDER BY fund_code,matched_keyword"""


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
            if keyword.casefold() in name.casefold():
                product["matches"][keyword] = ("名称", name, _SOURCE)
            elif isinstance(index, str) and keyword.casefold() in index.casefold():
                product["matches"][keyword] = (
                    "跟踪指数",
                    index,
                    row.get("matched_index_source_uri") or _SOURCE,
                )
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


async def recall_topic_funds(conn, topics, packet, now):
    """Return real fund matches keyed by topicId (or key), with trade facts apart.

    Two database reads, no network or mutation. 72 topics * 8 direct keywords,
    12 candidates per keyword, and 3 displayed funds per topic bound the cost.
    Oversize input is rejected explicitly rather than silently dropping topics.
    """
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
    keywords = list(
        dict.fromkeys(
            word for _, _, terms, fallback, _ in prepared for word in terms + fallback
        )
    )
    rows = await conn.fetch(
        _LOOKUP_SQL,
        now,
        keywords,
        sorted(core_codes),
        OPPORTUNITY_CANDIDATES_PER_KEYWORD,
    )
    products = _identities(rows, now)
    statuses = await read_fund_subscription_status(conn, now, list(products))
    prices, stale_prices = _local_prices(packet, now)
    result = {}
    for key, topic, terms, fallback, fallback_label in prepared:
        match = topic.get("match") if isinstance(topic.get("match"), dict) else {}
        industries = set(_texts(match.get("industryIds")))
        regional_bonds = _regional_bond_terms(topic) is not None
        candidates = []
        for code, product in products.items():
            direct = next(
                (
                    product["matches"][word]
                    for word in terms
                    if word in product["matches"]
                ),
                None,
            )
            broad = next(
                (
                    product["matches"][word]
                    for word in fallback
                    if word in product["matches"]
                ),
                None,
            )
            if code in core_codes and (
                direct or not regional_bonds and sectors.get(code) in industries
            ):
                level = "CORE_MATCH"
                reason = f"精选池基金与主题「{topic.get('title') or key}」的名称或登记行业精确匹配。"
            elif direct:
                level = "DYNAMIC_MATCH"
                reason = f"主题「{topic.get('title') or key}」动态匹配基金{direct[0]}「{direct[1]}」。"
            elif broad:
                level = "BROAD_FALLBACK"
                reason = f"主题「{topic.get('title') or key}」未找到已确认可申购的直接匹配产品，回退至{fallback_label}「{broad[1]}」作间接参与。"
            else:
                continue
            subscription, can_subscribe = _subscription(statuses.get(code), now)
            price = prices.get(code)
            if (
                price
                and (price["subjectKind"], price["subjectId"]) != product["identity"]
            ):
                price = None
            score, scoring = _quantitative_score(price, packet, now)
            missing = (
                ([] if score is not None else ["QUANTITATIVE_SCORE_UNAVAILABLE"])
                if price
                else ["LOCAL_QUANTITATIVE_EVIDENCE_MISSING"]
            )
            warnings = [
                "主题关联仅作为基金推荐依据，买入仍须独立核验净值区间与交易条件。"
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
            if level == "BROAD_FALLBACK":
                warnings.append(
                    "宽基或大类资产仅提供间接敞口，不代表纯主题持仓或收益。"
                )
            if direct and direct[0] == "名称":
                warnings.append("名称关键词匹配不等于已核验基金当前持仓。")
            candidates.append(
                {
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
                    "matchLevel": level,
                    "matchReason": reason,
                    "subscription": subscription,
                    "canSubscribe": can_subscribe,
                    "canBuyNow": False,
                    "actionable": False,
                    "quantitativeScore": score,
                    **scoring,
                    "sourceUri": (direct or broad or (None, None, _SOURCE))[2],
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
        candidates.sort(
            key=lambda row: (
                not row["canSubscribe"],
                _LEVEL[row["matchLevel"]],
                -(
                    row["quantitativeScore"]
                    if row["quantitativeScore"] is not None
                    else -1
                ),
                _price_rank(prices.get(row["fundCode"]))
                if row["confirmedNav"]
                else _price_rank(None),
                _share_priority(row["name"]),
                row["fundCode"],
            )
        )
        # Once a tier has an available product, later fallback tiers add no value.
        available_level = min(
            (_LEVEL[row["matchLevel"]] for row in candidates if row["canSubscribe"]),
            default=2,
        )
        funds = [
            row for row in candidates if _LEVEL[row["matchLevel"]] <= available_level
        ][:OPPORTUNITY_FUNDS_PER_TOPIC]
        unavailable, message = (
            None,
            "已找到主题相关真实基金；申购状态与立即买入条件分别展示。",
        )
        if not funds:
            unavailable, message = (
                (
                    "NO_VERIFIED_FUND_MATCH",
                    "当前真实基金主库中未找到可核验的场外基金或合理资产映射，请补充基金主数据后重试。",
                )
                if fallback and not regional_bonds
                else (
                    "NO_EXPLAINABLE_ASSET_MAPPING",
                    "未找到主题直接匹配基金，当前缺少可说明的地域或资产关联，不能用其他地区宽基代替。",
                )
            )
        elif not any(row["canSubscribe"] for row in funds):
            states = {row["subscription"]["status"] for row in funds}
            unavailable = (
                "SUBSCRIPTION_UNVERIFIED"
                if "UNKNOWN" in states
                else "SUBSCRIPTION_SUSPENDED"
                if states == {"SUSPENDED"}
                else "SUBSCRIPTION_RESTRICTED"
            )
            message = "已展示相关真实基金，但申购状态未知、暂停或额度受限，暂未找到已确认可正常申购的产品。"
        result[key] = {
            "topicId": key,
            "status": "MATCHED" if funds else "UNAVAILABLE",
            "funds": funds,
            "unavailableReason": unavailable,
            "message": message,
        }
    return result
