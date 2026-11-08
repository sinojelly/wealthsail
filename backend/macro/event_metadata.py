"""Display metadata from saved public evidence; never formal research assurance."""

import re
import unicodedata
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


_TZ = ZoneInfo("Asia/Shanghai")
_CATEGORIES = {
    "GEOPOLITICS_POLICY",
    "DOMESTIC_GROWTH",
    "FINANCIAL_MARKET",
    "INDUSTRY_EMPLOYMENT",
    "MONETARY_POLICY",
    "TECHNOLOGY",
    "UNCLASSIFIED",
}
_TOPICS = (
    (
        "GEOPOLITICS_POLICY",
        r"地缘|军事|战争|冲突|俄乌|俄美|美伊|伊朗|外交|停火|海峡|核设施|制裁",
    ),
    (
        "TECHNOLOGY",
        r"(?i)(?<![a-z])ai(?![a-z])|人工智能|机器人|半导体|芯片|算力|通信|火箭|航天|科技|技术创新",
    ),
    (
        "MONETARY_POLICY",
        r"货币政策|央行|利率|降息|加息|互换便利|稳市政策|降准|流动性工具",
    ),
    (
        "DOMESTIC_GROWTH",
        r"(?i)(?<![a-z])(?:cpi|ppi|gdp|pmi)(?![a-z])|采购经理|国内经济|居民消费|生产价格|消费价格|经济增长|社融|信贷",
    ),
    ("FINANCIAL_MARKET", r"金融市场|指数|交易所|权重上限|证券|资本市场|股市|债市|汇率"),
    (
        "INDUSTRY_EMPLOYMENT",
        r"产业|就业|招聘|工资|稀土|氧化铝|化工|公共卫生|医疗|制造业|企业订单",
    ),
)


def _category(event):
    existing = event.get("category")
    if isinstance(existing, str) and existing in _CATEGORIES:
        return existing
    # Titles describe the subject. Conditional scenarios and possible impacts do not.
    title = event.get("title")
    if isinstance(title, str):
        for category, pattern in _TOPICS:
            if re.search(pattern, title):
                return category
    for channel in event.get("channels", []):
        if not isinstance(channel, str):
            continue
        for category, pattern in _TOPICS:
            if re.search(pattern, channel):
                return category
    return "UNCLASSIFIED"


def _publication(value):
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.combine(date.fromisoformat(value), time(), _TZ)
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except ValueError:
        return None


def event_metadata(event, sources, now):
    """Classify an observation and label its latest saved source publication."""
    published = []
    for source in sources:
        value = source.get("publishedAt") or source.get("publishedDate")
        stamp = _publication(value)
        if stamp is not None and stamp <= now:
            published.append((stamp, value))
    latest = max(published, key=lambda item: item[0])[1] if published else None
    return {
        "category": _category(event),
        "sourcePublishedAt": latest,
        "sourcePublicationBasis": "SOURCE_PUBLICATION" if latest else None,
    }


def _text(value):
    if not isinstance(value, str):
        return ""
    return "".join(
        char
        for char in unicodedata.normalize("NFKC", value).casefold()
        if char.isalnum()
    )


def _title_matches(left, right):
    def numbers(value):
        if not isinstance(value, str):
            return []
        normalized = unicodedata.normalize("NFKC", value).replace("−", "-")
        return re.findall(r"[+\-–—]?\d+(?:[.,:/\-–—]\d+)*(?:%|‰)?", normalized)

    # Punctuation tolerance is for headline layout and publisher wrappers.
    # It must not erase signs, units, decimals or date/time separators.
    if numbers(left) != numbers(right):
        return False
    a, b = _text(left), _text(right)
    if min(len(a), len(b)) >= 8 and (a in b or b in a):
        return True
    # Publisher prefixes/suffixes may change ordering in a saved page heading.
    parts = re.split(r"\s[-|]\s|[：:]", left) if isinstance(left, str) else []
    return any(len(text) >= 8 and text in b for text in map(_text, parts))


def _quote_matches(quote, body):
    def literal(value):
        return (
            "".join(unicodedata.normalize("NFKC", value).casefold().split())
            if isinstance(value, str)
            else ""
        )

    quote, body = literal(quote), literal(body)
    if len(quote) < 8:
        return False
    # Preserve numerical meaning: decimal points, signs, percentages and date
    # separators are evidence. A truncated number cannot match a longer value.
    start = r"(?<![\d.+−-])" if quote[0].isdigit() else ""
    end = r"(?![\d.])" if quote[-1].isdigit() else ""
    return re.search(start + re.escape(quote) + end, body) is not None


def source_metadata(source, packet, now):
    """Match a report citation to readable frozen evidence without changing checks.

    A candidate-index heading can support a title match only. A body quotation
    requires a checked frozen document, the same URL, title and visible clock.
    """
    from backend.macro.daily_research import _stamp, _url, _visible

    result = {"frozenTitleMatched": False, "frozenQuoteMatched": False}
    try:
        uri = _url(source.get("url"))
    except ValueError:
        return result
    for kind in ("documents", "candidateIndex"):
        for row in packet.get(kind, []):
            if not isinstance(row, dict):
                continue
            try:
                same_url = _url(row.get("url")) == uri
            except ValueError:
                continue
            retrieved = _stamp(row.get("retrievedAt"))
            if not (
                same_url
                and retrieved is not None
                and retrieved <= now
                and _visible(row, now)
                and re.fullmatch(r"[a-fA-F0-9]{64}", str(row.get("contentHash") or ""))
                and _title_matches(source.get("title"), row.get("title"))
            ):
                continue
            result["frozenTitleMatched"] = True
            if (
                kind == "documents"
                and row.get("verification") == "SOURCE_CHECKED"
                and _quote_matches(source.get("quote"), row.get("quote"))
            ):
                result["frozenQuoteMatched"] = True
    return result
