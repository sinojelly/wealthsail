"""Deterministic public research priorities, not estimated winning probabilities."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlsplit

METHOD = "opportunity-priority-v1"
SCORE_FIELDS = (
    "opportunityScore",
    "rewardRiskRatio",
    "scoreBreakdown",
    "scoreMethodVersion",
)
_TOPICS = (
    r"半导体|芯片|存储|HBM|DRAM|英伟达|先进封装|PCB|电子",
    r"人工智能|计算机|算力|云计算|科技传媒|通信|5G|GTC",
    r"机器人|具身智能|人形|汽车|智能驾驶|智驾",
    r"医药|创新药|生物|临床|ESMO",
    r"稀土|有色|资源|金属|铝|铜|锗",
    r"证券|券商|金融街|非银",
    r"银行|金融街",
    r"黄金|贵金属|避险",
    r"原油|油气|石油|能源",
    r"卫星|航天|军工|航空|火箭",
    r"红利|低波",
    r"沪深300|中证500|上证|宽基|A500",
    r"消费|食品|白酒|饮料",
    r"电力|电网|特高压",
    r"游戏|动漫|传媒",
    r"电池|新能源|锂电|固态",
    r"化工|材料",
    r"恒生科技|港股科技|互联网",
    r"纳斯达克|纳指|美股科技",
)
_POSITIVE = re.compile(
    r"增长|大增|回购|预增|扭亏|订单|获批|投产|上调|整合|突破|签署|增持|改善|盈利"
)
_NEGATIVE = re.compile(r"否认|终止|取消|下调|下滑|下降|亏损|未获批|不予|未实施|拟减持")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _stamp(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return (
            value
            if value.tzinfo is not None and value.utcoffset() is not None
            else None
        )
    except (TypeError, ValueError):
        return None


def _topics(text):
    return {
        i for i, pattern in enumerate(_TOPICS) if re.search(pattern, str(text), re.I)
    }


def _source(uri, *, sse=False):
    try:
        url = urlsplit(uri)
        return bool(
            url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
            and url.port in (None, 443)
            and (
                not sse
                or url.hostname == "www.sse.com.cn"
                or url.hostname.endswith(".sse.com.cn")
            )
        )
    except (TypeError, ValueError):
        return False


def _current(row, now, *, media=False):
    retrieved = _stamp(row.get("retrievedAt"))
    if retrieved is None or retrieved > now:
        return False
    if media:
        published = _stamp(row.get("publishedAt"))
        return bool(published and now - timedelta(days=1) <= published <= now)
    try:
        day = date.fromisoformat(row["referenceDate"])
        prior = date.fromisoformat(row["comparisonDate"])
        completed = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
        return prior < day <= completed and (now.date() - day).days <= 7
    except (KeyError, TypeError, ValueError):
        return False


def _prepare_evidence(packet, now):
    """Validate and classify each public source once, scoped to this report."""
    catalysts, shares = [], []
    for row in (packet.get("alphaPublic") or {}).get("catalysts", [])[:300]:
        if (
            not isinstance(row, dict)
            or not _current(row, now, media=True)
            or not _source(row.get("sourceUri"))
        ):
            continue
        text = " ".join(str(row.get(k) or "") for k in ("title", "content", "summary"))
        if not _POSITIVE.search(text) or _NEGATIVE.search(text):
            continue
        catalysts.append(
            (
                _topics(text),
                row.get("parentSourceId") or row["sourceUri"],
                urlsplit(row["sourceUri"])._replace(query="", fragment="").geturl(),
                row["sourceUri"],
            )
        )
    for row in (packet.get("institutionalSignals") or {}).get("etfFlows", [])[:40]:
        if (
            isinstance(row, dict)
            and row.get("metric") == "ETF_SHARE_CHANGE"
            and row.get("unit") == "份"
            and _source(row.get("sourceUri"), sse=True)
            and _current(row, now)
            and row.get("verification") == "EXCHANGE_REPORTED"
            and _source(row.get("comparisonSourceUri"), sse=True)
            and _number(row.get("currentShares")) is not None
            and _number(row.get("previousShares")) is not None
            and _number(row.get("currentShares")) >= 0
            and _number(row.get("previousShares")) >= 0
            and _number(row.get("currentShares")) - _number(row.get("previousShares"))
            == _number(row.get("value"))
            and (_number(row.get("value")) or Decimal(0)) > 0
        ):
            shares.append((_topics(row.get("fundName", "")), row["sourceUri"]))
    return catalysts, shares


def _evidence(plan, packet, now, *, prepared=None):
    topics = _topics(plan.get("label", ""))
    catalysts, shares = (
        _prepare_evidence(packet, now) if prepared is None else prepared
    )
    parents, sources, urls = set(), [], set()
    for related, parent, canonical, uri in catalysts:
        if not topics.intersection(related) or parent in parents or canonical in urls:
            continue
        # Digest siblings and duplicate URLs remain one independent parent.
        parents.add(parent)
        urls.add(canonical)
        if uri not in sources:
            sources.append(uri)
    catalyst = min(20, 10 + 5 * (len(parents) - 1)) if parents else 0
    share = 0
    for related, uri in shares:
        if topics.intersection(related):
            share = 15
            if uri not in sources:
                sources.append(uri)
            break
    return catalyst, share, sources


def high_zone(plan, track):
    current, target, rsi = (
        _number(plan.get("currentPrice")),
        _number(track.get("takeProfitT1")),
        _number((plan.get("indicators") or {}).get("rsi14")),
    )
    return bool(
        current is not None
        and target is not None
        and current >= target
        or rsi is not None
        and rsi >= 70
    )


def _blocked(plan, track):
    return any(
        row.get("entryBlocked") is True
        or row.get("subscriptionRestricted") is True
        or row.get("preferredEntryEligible") is False
        for row in (plan, track)
    )


def _ready(plan, track):
    current, low, high = (
        _number(value)
        for value in (
            plan.get("currentPrice"),
            track.get("entryZoneMin"),
            track.get("entryZoneMax"),
        )
    )
    return bool(
        not _blocked(plan, track)
        and not any(
            row.get("entryReady") is False or row.get("inBuyZone") is False
            for row in (plan, track)
        )
        and track.get("state") == "CANDIDATE"
        and track.get("priceState") == "READY"
        and current is not None
        and low is not None
        and high is not None
        and low <= current <= high
    )


def score_plan(plan, packet, now, *, prepared_evidence=None):
    result = deepcopy(plan)
    for key in SCORE_FIELDS:
        result.pop(key, None)
    for track in result.get("tracks", []):
        for key in SCORE_FIELDS:
            track.pop(key, None)
    catalyst, share, sources = _evidence(
        result, packet, now, prepared=prepared_evidence
    )
    for track in result.get("tracks", []):
        current, low, high, stop, t1 = [
            _number(value)
            for value in (
                result.get("currentPrice"),
                track.get("entryZoneMin"),
                track.get("entryZoneMax"),
                track.get("stopLossPrice"),
                track.get("takeProfitT1"),
            )
        ]
        if (
            track.get("priceState") != "READY"
            or None in (current, low, high, stop, t1)
            or not 0 < stop < low <= high < t1
        ):
            continue
        ready = _ready(result, track)
        technical = (
            0
            if _blocked(result, track)
            else 40
            if ready
            else 15
            if track.get("state") == "CANDIDATE"
            else 5
            if low <= current <= high
            else 0
        )
        mid = (low + high) / 2
        rr = (t1 - mid) / (mid - stop)
        reward = Decimal(15) * min(Decimal(1), max(Decimal(0), (rr - 1) / 2))
        rsi = _number((result.get("indicators") or {}).get("rsi14"))
        momentum = _number((result.get("indicators") or {}).get("momentum20"))
        if track.get("track") == "LEFT_VALUE":
            strength = (
                10
                if rsi is not None and rsi <= 30
                else 7
                if rsi is not None and rsi <= 40
                else 4
                if rsi is not None and rsi <= 50
                else 0
            )
        else:
            strength = (
                float(
                    Decimal(10)
                    * min(
                        Decimal(1),
                        max(Decimal(0), (momentum or Decimal(0)) / Decimal("0.1")),
                    )
                )
                if track.get("state") == "CANDIDATE"
                else 0
            )
        missing = ([] if catalyst else ["POSITIVE_INDUSTRY_CATALYST"]) + (
            [] if share else ["SSE_ETF_SHARE_GROWTH"]
        )
        track.update(
            opportunityScore=round(
                float(technical + catalyst + share + reward + Decimal(str(strength))), 4
            ),
            rewardRiskRatio=round(float(rr), 6),
            scoreMethodVersion=METHOD,
            scoreBreakdown={
                "technical": technical,
                "catalyst": catalyst,
                "etfShares": share,
                "rewardRisk": round(float(reward), 4),
                "strength": strength,
                "evidenceScope": "SECTOR_PROXY",
                "sourceUris": sources,
                "missingEvidence": missing,
            },
        )
    selected = result.get("selectedTrack", result.get("track"))
    preferred = next(
        (row for row in result.get("tracks", []) if row.get("track") == selected), None
    )
    if preferred is None:
        candidates = [
            row for row in result.get("tracks", []) if "opportunityScore" in row
        ]
        preferred = (
            min(candidates, key=lambda row: priority_key(result, row))
            if candidates
            else None
        )
    if preferred:
        for key in SCORE_FIELDS:
            if key in preferred:
                result[key] = deepcopy(preferred[key])
    return result


def priority_key(plan, track):
    high = high_zone(plan, track)
    group = 2 if high else 0 if _ready(plan, track) else 1
    return (
        group,
        -float(track.get("opportunityScore", 0)),
        -float(track.get("rewardRiskRatio", 0)),
        str(plan.get("fundCode", "")),
    )


def rank_report(report, packet):
    result = deepcopy(report)
    now = _stamp(packet.get("asOf")) or _stamp(report.get("asOf"))
    if now is None:
        return result
    evidence = _prepare_evidence(packet, now)

    def score(plan):
        return score_plan(plan, packet, now, prepared_evidence=evidence)

    scan = result.get("quantitativeScan")
    if isinstance(scan, dict):
        scan["items"] = [score(plan) for plan in scan.get("items", [])]
    for direction in result.get("valueDirections", []):
        direction["plans"] = [
            score(plan) for plan in direction.get("plans", [])
        ]

    def direction_key(direction):
        keys = [
            priority_key(plan, track)
            for plan in direction.get("plans", [])
            for track in plan.get("tracks", [])
            if track.get("track") == plan.get("selectedTrack", plan.get("track"))
            and "opportunityScore" in track
        ]
        group = min((key[0] for key in keys), default=3)
        # Keep each fund's research score, but a blocked/high-zone fund cannot
        # lift a direction that also offers an eligible buy-zone fund.
        best_group = [key for key in keys if key[0] == group]
        direction["opportunityScore"] = max((-key[1] for key in best_group), default=0)
        rr = max((-key[2] for key in best_group), default=0)
        return (
            group,
            -direction.get("opportunityScore", 0),
            -rr,
            str(direction.get("key", "")),
        )

    if "valueDirections" in result:
        result["valueDirections"] = sorted(result["valueDirections"], key=direction_key)
    if "actionablePlans" in result:
        result["actionablePlans"] = [
            score(plan) for plan in result["actionablePlans"]
        ]
    return result
