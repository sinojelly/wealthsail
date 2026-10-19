"""Public media clues, preserving reported status and one parent's provenance.

An analyst view, a buyback proposal and a media flow estimate are different
facts. None of them establishes an investor's identity or an executed trade.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import re
from urllib.parse import urlsplit


_DIGEST = re.compile(
    r"(?:早间|晚间|早晨|晚间)?新闻精选|投资日历|大事提醒|早报精选|晚报精选"
)
_NUMBERS = re.compile(r"(?<![\d.])(?:(\d{1,2})[、．.](?!\d)|([①-⑳]))")
_BROADCAST = re.compile(
    r"新闻联播.*(?:要闻|汇总|摘要|重点|内容)|(?:要闻|汇总|摘要).*新闻联播"
)
_SOCIAL = re.compile(
    r"航班.*(?:备降|迫降|改航|可疑物)|航空.*(?:备降|迫降|改航|可疑物)|"
    r"火灾|失火|起火|山火|零星.*(?:疾病|疫情)|"
    r"(?:海外|国外|当地).*(?:疫情|新增病例)|疾病病例|礼节性|外事礼节"
)
_ECONOMIC = re.compile(
    r"工厂|炼油厂|炼厂|油田|煤矿|电厂|数据中心|火箭|卫星|厂房|停产|产能|供应链|供应收缩|生产经营|盈利预期|盈利预测|"
    r"营业收入|净利润|业绩|临床|疫苗|医药|药品|消防.*订单|订单.*消防"
)
_INSTITUTION = re.compile(
    r"摩根士丹利|大摩|高盛|摩根大通|瑞银|瑞信|花旗|美银|野村|汇丰|"
    r"中信(?:证券|建投)?|中金|华泰|国泰海通|海通证券|招商证券|申万宏源|沙利文|券商|大行"
)
_VIEW = re.compile(
    r"研报|研究报告|评级|预测|预计|估计|展望|上调|下调|维持.*(?:买入|增持)"
)
_EARNINGS = re.compile(
    r"预增|扭亏|预盈|业绩预告|盈利预警|利润.*(?:大增|增长|翻倍)|业绩.*(?:大增|增长|翻倍)"
)
_ETF_FLOW = re.compile(
    r"ETF.*(?:净申购|净赎回|净流入|净流出|资金流入|资金流出)|(?:净申购|净赎回|净流入|净流出).*ETF",
    re.I,
)


def is_media_noise(title, content):
    """Remove social accidents even when an incidental word maps to a sector."""
    title, content = str(title or ""), str(content or "")
    if _BROADCAST.search(title):
        return True
    text = title + " " + content
    return bool(_SOCIAL.search(text) and not _ECONOMIC.search(text))


def split_numbered_digest(title, content):
    """Only split recognized, explicitly numbered collections before truncation."""
    if not _DIGEST.search(str(title) + " " + str(content)[:120]):
        return [(None, title, content[:1000])]
    markers = list(_NUMBERS.finditer(content))
    ordinals = [int(m[1]) if m[1] else ord(m[2]) - ord("①") + 1 for m in markers]
    if len(markers) < 2 or ordinals != list(range(1, len(ordinals) + 1)):
        return [(None, title, content[:1000])]
    rows = []
    for i, marker in enumerate(markers[:40]):
        text = content[
            marker.end() : markers[i + 1].start()
            if i + 1 < len(markers)
            else len(content)
        ].strip()
        if not text:
            continue
        heading = re.split(r"[。！？!?\n]", text, maxsplit=1)[0].strip()[:200]
        rows.append((ordinals[i], heading or title, text[:1000]))
    return rows


def _stamp(value):
    try:
        result = datetime.fromisoformat(str(value))
        return (
            result
            if result.tzinfo is not None and result.utcoffset() is not None
            else None
        )
    except (TypeError, ValueError):
        return None


def extract_media_institutional_signals(catalysts, now):
    """Return bounded factual text with current parent/child source references."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("institutional signal collection requires an aware clock")
    from backend.integrations.alpha_collectors import catalyst_sectors

    result, seen = [], set()
    for row in catalysts[:600] if isinstance(catalysts, list) else []:
        if not isinstance(row, dict):
            continue
        uri = str(row.get("sourceUri") or "")
        try:
            parts = urlsplit(uri)
            safe = (
                parts.scheme == "https"
                and parts.hostname == "www.cls.cn"
                and parts.port in (None, 443)
                and not parts.username
                and not parts.password
            )
        except ValueError:
            safe = False
        published, retrieved = (
            _stamp(row.get("publishedAt")),
            _stamp(row.get("retrievedAt")),
        )
        available = (
            _stamp(row.get("availableAt"))
            if row.get("availableAt") is not None
            else retrieved
        )
        identity = row.get("id")
        if (
            not safe
            or not isinstance(identity, str)
            or not identity
            or len(identity) > 80
            or published is None
            or retrieved is None
            or available is None
            or max(published, retrieved, available) > now
        ):
            continue
        title, content = str(row.get("title") or ""), str(row.get("content") or "")
        if is_media_noise(title, content):
            continue
        text = title + " " + content
        kinds = []
        if _VIEW.search(text) and (
            _INSTITUTION.search(text) or "研报" in text or "研究报告" in text
        ):
            kinds.append("RESEARCH_VIEW")
        if "回购" in text and not re.search(
            r"逆回购|正回购|回购利率|回购操作|公开市场", text
        ):
            kinds.append("CORPORATE_BUYBACK")
        if _EARNINGS.search(text):
            kinds.append("EARNINGS_UPGRADE")
        if _ETF_FLOW.search(text):
            kinds.append("ETF_MEDIA_FLOW")
        for kind in kinds:
            key = kind, identity
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "id": "media-signal:"
                    + hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()[:24],
                    "kind": kind,
                    "summary": (
                        content
                        if content and title in content
                        else title + ("。" + content if content else "")
                    )[:420],
                    "sectorBuckets": catalyst_sectors(text),
                    "mediaEvidenceId": identity,
                    "sourceIds": [identity],
                    "parentSourceId": row.get("parentSourceId") or identity,
                    "sourceUri": uri,
                    "publishedAt": published.isoformat(),
                    "retrievedAt": retrieved.isoformat(),
                    "availableAt": available.isoformat(),
                    "factVerification": row.get("factVerification")
                    if row.get("factVerification")
                    in {"SOURCE_CHECKED", "UNVERIFIED", "UNAVAILABLE"}
                    else "UNVERIFIED",
                    "institutionAttribution": "UNKNOWN",
                    **(
                        {
                            "cancellationBuyback": bool(
                                re.search(
                                    r"注销(?:式|性)?回购|回购.*注销|注销.*股份", text
                                )
                            )
                        }
                        if kind == "CORPORATE_BUYBACK"
                        else {}
                    ),
                }
            )
    return result[:80]
