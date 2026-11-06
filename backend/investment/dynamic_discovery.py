"""Public event themes and bounded market-wide OTC fund identity lookup."""

from datetime import date, datetime
import re
from uuid import UUID

from backend.investment.discovery_limits import (
    DYNAMIC_SELECTION_LIMIT,
    RESEARCH_SELECTION_LIMIT,
)

CORE_FUND_CODES = (
    "320007",
    "008281",
    "005609",
    "161129",
    "008975",
    "008298",
    "000051",
    "012733",
    "011035",
    "004432",
    "004069",
    "013477",
    "018344",
    "003017",
    "008086",
    "012737",
    "009179",
    "000216",
    "162411",
    "270042",
    "021607",
    "004854",
    "007466",
    "000962",
    "110020",
    "202015",
    "001632",
    "011103",
    "161725",
    "161726",
    "006756",
    "001361",
    "001629",
    "016185",
    "018027",
    "024194",
    "007300",
    "014880",
    "000218",
    "023530",
)
SELECTION_LIMIT = RESEARCH_SELECTION_LIMIT
DYNAMIC_LIMIT = DYNAMIC_SELECTION_LIMIT

# These are name-search hints, not certified holdings or product-index links.
_THEMES = (
    ("SATELLITE", r"卫星|卫星互联网", ("卫星",)),
    ("LOW_ALTITUDE", r"低空|无人机|\beVTOL\b", ("低空", "通用航空")),
    (
        "MEMORY",
        r"存储|\bHBM\b|\bDRAM\b|长鑫|长江存储|德明利",
        ("存储", "芯片", "半导体"),
    ),
    ("PCB", r"\bPCB\b|印制电路|覆铜板|生益科技|胜宏科技|沪电股份", ("电子", "通信")),
    ("SOLID_BATTERY", r"固态电池|锂电|动力电池", ("电池", "锂电")),
    ("TRADITIONAL_MEDICINE", r"中药", ("中药",)),
    ("COAL", r"煤炭|焦煤|动力煤", ("煤炭",)),
    ("POWER", r"电力|电网|特高压", ("电力", "电网")),
    ("GAMING", r"游戏|电竞", ("游戏", "动漫")),
    ("CHEMICAL", r"化工|化学材料", ("化工",)),
    ("HK_TECH", r"恒生科技|港股科技", ("恒生科技",)),
    ("DIVIDEND", r"红利|低波", ("红利", "低波")),
    (
        "AI",
        r"人工智能|英伟达|\bGTC\b|\bAI\b|算力|数据中心",
        ("人工智能", "计算机", "云计算", "算力"),
    ),
    ("ROBOT", r"机器人|具身智能|人形", ("机器人",)),
    ("RARE_EARTH", r"稀土|盛和资源|战略金属|有色", ("稀土", "有色")),
    ("FINANCIAL", r"金融街论坛|券商|证券|银行", ("证券", "银行", "金融")),
    ("DRUG", r"创新药|\bESMO\b|临床|生物医药", ("创新药", "生物医药")),
    ("GOLD_ENERGY", r"黄金|地缘|以色列|伊朗|俄乌|原油|油气", ("黄金", "油气", "石油")),
    ("AEROSPACE", r"商业航天|火箭|军工|航空航天", ("军工", "航空航天")),
    ("CONSUMPTION", r"消费|白酒|食品饮料", ("消费", "白酒", "食品饮料")),
)
_ROUNDUP = re.compile(r"(?:《?新闻联播》?).*(?:要闻|汇总)")
_SOCIAL = re.compile(r"备降|迫降|改航线|改航|居民楼火灾|零星疫情|海外火灾")
_INDUSTRIAL = re.compile(r"制造|订单|回购|业绩|研报|产能|采购|营收|净利润")
_EXCLUDED_NAME = re.compile(
    r"后端|后收费|清盘|清算|终止|退市|(?:^|[^A-Za-z])[BC]\s*(?:类|份额)?"
    r"(?:[（(][^（）()]{0,30}[)）])?(?:人民币|美元|港币)?$",
    re.IGNORECASE,
)
_FOREIGN_CURRENCY = re.compile(
    r"美元|港币|港元|外币|现汇|现钞|欧元|英镑|日元|澳元|加元|瑞郎"
)


def _stamp(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return (
            stamp
            if stamp.tzinfo is not None and stamp.utcoffset() is not None
            else None
        )
    except (TypeError, ValueError):
        return None


def event_themes(catalysts, now):
    """Read the complete public item before any card-length text truncation."""
    now = _stamp(now)
    if now is None:
        raise ValueError("event discovery requires an aware clock")
    found = {}
    for index, row in enumerate(catalysts[:500] if isinstance(catalysts, list) else []):
        if not isinstance(row, dict):
            continue
        retrieved = _stamp(row.get("retrievedAt"))
        if retrieved is None or retrieved > now:
            continue
        if any(
            row.get(field) is not None
            and (_stamp(row[field]) is None or _stamp(row[field]) > now)
            for field in ("publishedAt", "availableAt")
        ):
            continue
        if row.get("publishedDate") is not None:
            try:
                if date.fromisoformat(row["publishedDate"]) > now.date():
                    continue
            except (TypeError, ValueError):
                continue
        title = str(row.get("title") or "")[:1000]
        text = " ".join(
            value[:20000]
            for field in ("title", "summary", "content", "text")
            if isinstance(value := row.get(field), str)
        )
        if (
            _ROUNDUP.search(title)
            or _SOCIAL.search(title)
            and not _INDUSTRIAL.search(text)
        ):
            continue
        matches = []
        for code, pattern, keywords in _THEMES:
            match = re.search(pattern, text, re.IGNORECASE | re.ASCII)
            if match:
                matches.append((match.start(), code, keywords))
        for offset, code, keywords in sorted(matches):
            found.setdefault(
                code,
                {
                    "code": code,
                    "fundKeywords": list(keywords),
                    "order": (index, offset),
                },
            )
    return [
        {"code": row["code"], "fundKeywords": row["fundKeywords"]}
        for row in sorted(found.values(), key=lambda row: row["order"])
    ]


def _share_priority(name):
    is_a = bool(
        re.search(
            r"A(?:类)?(?:[（(]?(?:人民币|美元|港币)[)）]?)?$", name, re.IGNORECASE
        )
    )
    if is_a and "ETF" in name.upper() and "联接" in name:
        return 0
    if is_a and ("指数" in name or "ETF" in name.upper()):
        return 1
    return 2 if is_a else 3


async def read_dynamic_assets(conn, now, catalysts, excluded_codes):
    """Look up all matching master identities before applying per-keyword bounds."""
    themes = event_themes(catalysts, now)
    keywords = list(
        dict.fromkeys(key for theme in themes for key in theme["fundKeywords"])
    )
    if not keywords:
        return []
    rows = await conn.fetch(
        """WITH catalog AS (
          SELECT c.*,count(*) OVER(PARTITION BY c.market,c.fund_code) AS mapping_count
          FROM reference.research_fund_catalog c WHERE c.market='FUND_CN'
            AND c.trade_form IN ('FUND','LOF') AND c.fund_code~'^[0-9]{6}$'
        ), matched AS (
          SELECT c.*,m.updated_at AS identity_verified_at,k AS matched_keyword,
            CASE WHEN c.fund_name ~* 'ETF.*联接.*A([（(].*[)）])?$' THEN 0
              WHEN c.fund_name ~* '(指数|ETF).*A([（(].*[)）])?$' THEN 1
              WHEN c.fund_name ~* 'A([（(].*[)）])?$' THEN 2 ELSE 3 END AS share_priority,
            (c.fund_type ILIKE '%QDII%' OR c.fund_name ILIKE '%QDII%') AS overseas_calendar_required
          FROM catalog c JOIN reference.fund_master m ON m.fund_code=c.fund_code
          CROSS JOIN unnest($2::text[]) k
          WHERE m.updated_at<=$1 AND c.mapping_count=1 AND c.currency='CNY'
            AND c.fund_name ILIKE '%'||k||'%'
            AND NOT(c.fund_code=ANY($3::text[]))
            AND c.fund_name !~* '后端|后收费|清盘|清算|终止|退市|(^|[^A-Za-z])[BC][[:space:]]*(类|份额)?([（(][^（）()]{0,30}[)）])?(人民币|美元|港币)?$'
            AND c.fund_type !~* '货币|债券|FOF'
            AND c.fund_name !~ '美元|港币|港元|外币|现汇|现钞|欧元|英镑|日元|澳元|加元|瑞郎'
            AND NOT EXISTS(SELECT 1 FROM reference.instrument_aliases a
              JOIN reference.instruments i ON i.id=a.instrument_id
              WHERE a.alias_type='TICKER' AND a.alias_value=c.fund_code
                AND i.asset_type='OFF_EXCHANGE_FUND' AND i.lifecycle_status<>'ACTIVE'
                AND a.effective_from<=($1 AT TIME ZONE 'Asia/Shanghai')::date
                AND (a.effective_to IS NULL OR a.effective_to>=($1 AT TIME ZONE 'Asia/Shanghai')::date))
        ), ranked AS (
          SELECT *,row_number() OVER(PARTITION BY matched_keyword
            ORDER BY share_priority,fund_code,instrument_id NULLS LAST) AS keyword_rank FROM matched
        ) SELECT * FROM ranked WHERE keyword_rank<=16
          ORDER BY keyword_rank,share_priority,fund_code,matched_keyword""",
        now,
        keywords,
        list(excluded_codes),
    )
    products, bad_codes = {}, set()
    keyword_themes = {
        keyword: [theme["code"] for theme in themes if keyword in theme["fundKeywords"]]
        for keyword in keywords
    }
    for row in rows:
        code, name = row.get("fund_code"), str(row.get("fund_name") or "")
        verified = _stamp(row.get("identity_verified_at"))
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"[0-9]{6}", code)
            or code in excluded_codes
            or row.get("market") != "FUND_CN"
            or row.get("currency", "CNY") != "CNY"
            or row.get("mapping_count") != 1
            or row.get("lifecycle_status", "ACTIVE") != "ACTIVE"
            or verified is None
            or verified > now
            or not name.strip()
            or _EXCLUDED_NAME.search(name)
            or _FOREIGN_CURRENCY.search(name)
            or re.search(
                r"货币|债券|FOF", str(row.get("fund_type") or ""), re.IGNORECASE
            )
            or row.get("matched_keyword") not in keyword_themes
        ):
            if isinstance(code, str) and row.get("mapping_count", 1) != 1:
                bad_codes.add(code)
            continue
        subject = str(row["instrument_id"]) if row.get("instrument_id") else code
        kind = "fund" if row.get("instrument_id") else "nav"
        if kind == "fund":
            try:
                UUID(subject)
            except (ValueError, TypeError):
                bad_codes.add(code)
                continue
        if code in products and (kind, subject) != (
            products[code]["subjectKind"],
            products[code]["subjectId"],
        ):
            bad_codes.add(code)
            continue
        product = products.setdefault(
            code,
            {
                "subjectKind": kind,
                "subjectId": subject,
                "fundCode": code,
                "market": "FUND_CN",
                "label": name[:200],
                "overseasCalendarRequired": row.get("overseas_calendar_required")
                is True,
                "identitySourceUri": "https://fund.eastmoney.com/js/fundcode_search.js",
                "identityVerifiedAt": verified.isoformat(),
                "dynamicThemes": [],
            },
        )
        for theme_code in keyword_themes[row["matched_keyword"]]:
            if theme_code not in product["dynamicThemes"]:
                product["dynamicThemes"].append(theme_code)
    identities = {}
    for code, row in products.items():
        identity = row["subjectKind"], row["subjectId"]
        if identity in identities and identities[identity] != code:
            bad_codes.update({code, identities[identity]})
        identities[identity] = code
    ordered = sorted(
        (row for code, row in products.items() if code not in bad_codes),
        key=lambda row: (_share_priority(row["label"]), row["fundCode"]),
    )
    groups = [
        [row for row in ordered if theme["code"] in row["dynamicThemes"]]
        for theme in themes
    ]
    selected, used = [], set()
    for offset in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if offset < len(group) and group[offset]["fundCode"] not in used:
                product = group[offset]
                used.add(product["fundCode"])
                product["discoverySector"] = "EVENT_" + product["dynamicThemes"][0]
                selected.append(product)
                if len(selected) >= DYNAMIC_LIMIT:
                    return selected
    return selected
