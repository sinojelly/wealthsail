"""Versioned, deterministic macro observations and conditional research channels."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

VERSION = "macro-research-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value):
    try:
        result = Decimal(str(value))
        return result if not isinstance(value, bool) and result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _timestamp(value):
    try:
        result = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
        return result.astimezone(SHANGHAI) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def _period(value):
    if isinstance(value, datetime) or isinstance(value, str) and "T" in value:
        stamp = _timestamp(value)
        return stamp.strftime("%Y-%m") if stamp else None
    try:
        result = date.fromisoformat(str(value)[:7] + "-01")
        return result.strftime("%Y-%m")
    except (ValueError, TypeError):
        return None


def _ordinal(period):
    return int(period[:4]) * 12 + int(period[5:7]) - 1


def _month(ordinal):
    return f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"


def _official_uri(uri, code=None):
    try:
        parsed = urlsplit(uri)
        allowed = ("pbc.gov.cn",) if code == "M2_YOY" else ("stats.gov.cn",)
        return (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and any(
                parsed.hostname == domain
                or (parsed.hostname or "").endswith("." + domain)
                for domain in allowed
            )
        )
    except (ValueError, TypeError):
        return False


def _visible(row, now):
    retrieved = _timestamp(row.get("retrieved_at"))
    published = _timestamp(row.get("published_at"))
    return (
        retrieved is not None
        and retrieved <= now
        and (
            row.get("published_at") is None
            or published is not None
            and published <= now
        )
    )


def _latest(rows, key):
    latest = {}
    for row in rows:
        identity = key(row)
        rank = (row.get("revision") or 0, str(row.get("retrieved_at") or ""))
        if identity not in latest or rank > latest[identity][0]:
            latest[identity] = (rank, row)
    return [row for _, row in latest.values()]


def _statistics(item, history, period):
    # Bad current facts cannot borrow an older historical endpoint.
    points = {}
    for point in history:
        month = _period(point.get("asOf"))
        value = _number(point.get("value"))
        if month and value is not None and period and month <= period:
            if month not in points or (point.get("revision") or 0) > (
                points[month].get("revision") or 0
            ):
                points[month] = {**point, "value": value}
    months = sorted(points)
    missing = (
        [
            _month(m)
            for m in range(_ordinal(months[0]), _ordinal(months[-1]) + 1)
            if _month(m) not in points
        ]
        if months
        else []
    )
    result = {
        "previousValue": None,
        "change3Months": None,
        "change6Months": None,
        "percentile": None,
        "median": None,
        "sampleCount": len(points),
        "firstPeriod": months[0] if months else None,
        "lastPeriod": months[-1] if months else None,
        "missingPeriods": missing,
    }
    value = _number(item.get("value"))
    last = points.get(period)
    if (
        value is None
        or last is None
        or last["value"] != value
        or last.get("unit") != item.get("unit")
        or last.get("revision") != item.get("revision")
    ):
        return result
    basis = last.get("sourceUri") or last.get("source")
    if not basis or item.get("sourceUri") and item["sourceUri"] != basis:
        return result

    def compatible(p):
        return (
            p.get("unit") == item.get("unit")
            and (p.get("sourceUri") or p.get("source")) == basis
        )

    for length, field in (
        (1, "previousValue"),
        (3, "change3Months"),
        (6, "change6Months"),
    ):
        window = [
            points.get(_month(m))
            for m in range(_ordinal(period) - length, _ordinal(period) + 1)
        ]
        if all(p is not None and compatible(p) for p in window):
            result[field] = (
                window[0]["value"] if length == 1 else value - window[0]["value"]
            )
    if len(points) >= 12 and all(compatible(p) for p in points.values()):
        values = [p["value"] for p in points.values()]
        result["median"] = median(values)
        result["percentile"] = (
            Decimal(100)
            * (
                sum(v < value for v in values)
                + Decimal("0.5") * sum(v == value for v in values)
            )
            / len(values)
        )
    return result


def enrich_item(item, history, evidence, now):
    """Add period-specific official evidence without changing the base observation."""
    period, code, value = (
        _period(item.get("asOf")),
        item["code"],
        _number(item.get("value")),
    )
    unit_valid = item.get("unit") == {
        "PMI": "",
        "CPI_YOY": "%",
        "PPI_YOY": "%",
        "M2_YOY": "%",
    }.get(code)
    if not unit_valid:
        value = None
    all_releases = _latest(
        [
            row
            for row in evidence.get("releases", [])
            if row.get("field_code", row.get("code")) == code
            and _period(row.get("reference_period"))
            and row.get("unit") == item.get("unit")
            and _number(row.get("value")) is not None
            and _visible(row, now)
            and _official_uri(row.get("source_uri"), code)
        ],
        lambda row: (row.get("source_uri"), _period(row.get("reference_period"))),
    )
    releases = [
        row for row in all_releases if _period(row["reference_period"]) == period
    ]
    released_periods = {_period(row["reference_period"]) for row in all_releases}
    verified = "UNAVAILABLE" if value is None or period is None else "PENDING"
    if releases and verified != "UNAVAILABLE":
        verified = (
            "VERIFIED"
            if all(_number(r["value"]) == value for r in releases)
            else "CONFLICT"
        )
    release = (
        max(
            releases,
            key=lambda r: (_timestamp(r["retrieved_at"]), r.get("revision") or 0),
        )
        if releases
        else None
    )
    publication = "CONFIRMED" if release and release.get("published_at") else "UNKNOWN"
    official = (
        {
            "publisher": release.get("publisher"),
            "sourceUri": release["source_uri"],
            "value": release["value"],
            "publishedAt": release.get("published_at"),
            "retrievedAt": release["retrieved_at"],
        }
        if release
        else None
    )
    schedules = _latest(
        [
            row
            for row in evidence.get("schedules", [])
            if row.get("field_code", row.get("code")) == code
            and code != "M2_YOY"
            and _official_uri(row.get("source_uri"))
            and _visible(row, now)
            and _timestamp(row.get("scheduled_at"))
            and _period(row.get("reference_period"))
            and (period is None or _period(row["reference_period"]) > period)
            and _period(row["reference_period"]) not in released_periods
        ],
        lambda row: (
            row.get("field_code", row.get("code")),
            _period(row["reference_period"]),
        ),
    )
    schedule = (
        min(schedules, key=lambda r: _timestamp(r["scheduled_at"]))
        if schedules
        else None
    )
    next_release = (
        {
            "status": "SCHEDULED"
            if _timestamp(schedule["scheduled_at"]) > now
            else "OVERDUE",
            "scheduledAt": schedule["scheduled_at"],
            "referencePeriod": _period(schedule["reference_period"]),
            "sourceUri": schedule["source_uri"],
        }
        if schedule
        else None
    )
    conflicting_months = {
        _period(point.get("asOf"))
        for point in history
        if any(
            _period(row["reference_period"]) == _period(point.get("asOf"))
            and _number(row["value"]) != _number(point.get("value"))
            for row in all_releases
        )
    }
    statistics = _statistics(
        {**item, "value": None if verified == "CONFLICT" else value},
        [p for p in history if _period(p.get("asOf")) not in conflicting_months],
        period,
    )
    statistics["missingPeriods"] = sorted(
        set(statistics["missingPeriods"])
        | {p for p in conflicting_months if p and period and p <= period}
    )
    change = (
        value - statistics["previousValue"]
        if value is not None and statistics["previousValue"] is not None
        else None
    )
    benchmark = (
        {"value": Decimal(50), "label": "制造业扩张与收缩分界"}
        if code == "PMI"
        else {"value": Decimal(0), "label": "同比涨跌参照"}
        if code in {"CPI_YOY", "PPI_YOY"}
        else {"value": statistics["median"], "label": "历史样本中位数"}
        if statistics["median"] is not None
        else None
    )
    limitations = []
    if not unit_valid:
        limitations.append("缺少明确单位或单位口径不匹配，暂停比较与解释。")
    if verified == "PENDING":
        limitations.append("数值尚未与同期官方公告核对，以下为基于已采集值的条件观察。")
    if publication == "UNKNOWN":
        limitations.append("官方实际公布时刻待确认，不能用于当时已公开数据的策略回测。")
    if statistics["missingPeriods"]:
        limitations.append("历史月份有缺口，不跨缺月计算变化。")
    stale = False
    if period:
        year, month = int(period[:4]), int(period[5:])
        stale = now.date() > date(year, month, monthrange(year, month)[1]) + timedelta(
            days=item.get("maxAgeDays") or 45
        )
    if stale:
        limitations.append("最新所属期已过更新期限，需核查是否遗漏新公告。")
    if period and any(p > period for p in released_periods):
        stale = True
        limitations.append(
            "已发现更晚所属期的官方公告，观察序列待同步；暂停当前情景解释。"
        )
    level, summary = "UNAVAILABLE", "当前证据不足，暂不判断该指标。"
    momentum = (
        "INSUFFICIENT"
        if change is None
        else "RISING"
        if change > 0
        else "FALLING"
        if change < 0
        else "UNCHANGED"
    )
    if value is not None and verified not in {"CONFLICT", "UNAVAILABLE"} and not stale:
        if code == "PMI":
            level = (
                "EXPANSION"
                if value > 50
                else "CONTRACTION"
                if value < 50
                else "AT_BOUNDARY"
            )
            summary = f"制造业景气{'高于' if value > 50 else '低于' if value < 50 else '位于'}50分界，距离{value - 50:+.2f}指数点。"
            limitations.append(
                "仅覆盖制造业；整体增长需订单、生产、就业和其他行业确认。"
            )
        elif code in {"CPI_YOY", "PPI_YOY"}:
            level = (
                "PRICE_INCREASE"
                if value > 0
                else "PRICE_DECLINE"
                if value < 0
                else "PRICE_UNCHANGED"
            )
            summary = f"{'居民消费价格' if code == 'CPI_YOY' else '工业品出厂价格'}同比{'上涨' if value > 0 else '下降' if value < 0 else '持平'}。"
            limitations.append("价格变化不等于需求或利润改善，需核查结构、数量与成本。")
        elif code == "M2_YOY":
            level = "MONEY_GROWTH"
            summary = "广义货币余额同比增速" + (
                "较前月上升。"
                if change is not None and change > 0
                else "较前月下降。"
                if change is not None and change < 0
                else "尚需连续月数据确认变化。"
            )
            limitations.append("M2增速不直接代表融资条件，需结合社融、信贷需求和利率。")
    elif verified == "CONFLICT":
        summary = "已采集值与同期官方证据冲突，暂停解释并核查口径与修订。"
    if level == "UNAVAILABLE":
        momentum = "INSUFFICIENT"
    state = item.get("state", "NOT_COLLECTED") if unit_valid else "UNAVAILABLE"
    if value is not None:
        state = (
            "CONFLICT"
            if verified == "CONFLICT"
            else "STALE"
            if stale
            else "READY"
            if verified == "VERIFIED" and publication == "CONFIRMED"
            else "UNVERIFIED"
        )
    return {
        **item,
        "value": value,
        "state": state,
        "referencePeriod": period,
        "valueVerification": verified,
        "publicationStatus": publication,
        "officialEvidence": official,
        "publishedAt": release.get("published_at") if release else None,
        "nextRelease": next_release,
        "benchmark": benchmark,
        "statistics": statistics,
        "periodChange": change,
        "interpretation": {
            "level": level,
            "momentum": momentum,
            "summary": summary,
            "limitations": limitations,
            "methodVersion": VERSION,
        },
    }


def research_context(items):
    by_code = {item["code"]: item for item in items}
    definitions = [
        ("growth", "增长", ["PMI"], ["订单/生产/就业", "非制造业/实际增长"]),
        (
            "inflation",
            "通胀",
            ["CPI_YOY", "PPI_YOY"],
            ["核心通胀/价格结构", "企业利润/成本"],
        ),
        ("liquidity", "流动性", ["M2_YOY"], ["社融/信贷", "融资利率/资金价格"]),
        ("risk", "市场风险", [], ["估值/波动", "信用利差", "全球利率/美元"]),
    ]
    dimensions, changes = [], []
    for identity, label, codes, missing in definitions:
        relevant = [by_code[code] for code in codes if code in by_code]
        usable = [i for i in relevant if i["interpretation"]["level"] != "UNAVAILABLE"]
        conflict = any(i["valueVerification"] == "CONFLICT" for i in relevant)
        summary = (
            " ".join(
                (
                    "基于待核验的已采集值："
                    if i["valueVerification"] != "VERIFIED"
                    else ""
                )
                + i["interpretation"]["summary"]
                for i in usable
            )
            or "缺少可比较证据，暂不判断。"
        )
        if identity == "inflation" and len(usable) == 2:
            summary += "消费端与工业端需分别核查，工业品涨价对上游收入和下游成本可能有不同影响。"
        state = "CONFLICT" if conflict else "PARTIAL" if usable else "INSUFFICIENT"
        dimensions.append(
            {
                "id": identity,
                "label": label,
                "state": state,
                "summary": summary,
                "evidenceCodes": [i["code"] for i in usable],
                "missingEvidence": missing,
            }
        )
    for item in items:
        if item["interpretation"]["level"] == "UNAVAILABLE":
            continue
        previous, change = item["statistics"]["previousValue"], item.get("periodChange")
        if previous is not None and item["benchmark"] and item["code"] != "M2_YOY":
            boundary, value = item["benchmark"]["value"], item["value"]
            if previous < boundary <= value or value < boundary <= previous:
                changes.append(
                    {
                        "code": item["code"],
                        "summary": f"{item['label']}跨越{boundary}参考线，需后续月份及其他证据确认。",
                    }
                )
        if change is not None and change != 0:
            changes.append(
                {
                    "code": item["code"],
                    "summary": f"{item['label']}较前月{change:+.2f}{'个百分点' if item.get('unit') == '%' else '指数点'}。",
                }
            )
    usable_summaries = [d["summary"] for d in dimensions if d["state"] == "PARTIAL"]
    return {
        "methodVersion": VERSION,
        "summary": " ".join(usable_summaries)
        + " 证据覆盖有限，信用、利率与市场风险仍需验证。"
        if usable_summaries
        else "宏观证据不足，等待采集或核验。",
        "dimensions": dimensions,
        "changes": changes,
        "limitations": [
            "宏观用于提出研究方向；指数估值、基金质量与组合风险分别核验。",
            "有待核验/时刻未知的历史不能宣称支持有效的策略回测。",
        ],
    }


def direction_research(direction, context):
    profiles = {
        "a-share:broad": (
            ["PMI", "CPI_YOY", "PPI_YOY", "M2_YOY"],
            "制造业景气与价格变化可能影响盈利周期。",
            "盈利修复可能被需求不足、成本上升或高估值抵消。",
            ["盈利持续性", "指数估值与趋势", "信用和融资利率"],
        ),
        "a-share:cyclical": (
            ["PMI", "PPI_YOY"],
            "工业品价格变化可能改变上游收入与下游成本。",
            "价格上涨若缺少需求/销量支持，利润并不一定改善。",
            ["需求/库存", "销量与成本", "利润率与估值"],
        ),
        "a-share:consumer": (
            ["CPI_YOY", "PMI"],
            "消费价格与制造业变化可作为需求和成本研究线索。",
            "价格上升可能源于供给，不能替代实际消费量。",
            ["实际消费量", "收入/就业", "利润率"],
        ),
        "a-share:growth": (
            ["PMI", "M2_YOY"],
            "增长与货币变化提示核查盈利及利率敏感度。",
            "M2变化未必传导到融资成本，高估值可能抵消盈利增长。",
            ["融资利率", "盈利预期", "估值和集中度"],
        ),
        "a-share:technology": (
            ["PMI", "M2_YOY"],
            "增长与货币变化提示核查产业盈利及利率敏感度。",
            "宏观改善未必传导到特定科技行业，需确认产业与估值证据。",
            ["融资利率", "产业订单/盈利", "估值和集中度"],
        ),
        "bond:government": (
            ["CPI_YOY", "PPI_YOY", "M2_YOY"],
            "通胀与货币变化提示检查收益率和久期风险。",
            "价格变化与收益率方向可能不同，政策和期限溢价也会变化。",
            ["国债收益率", "政策利率", "久期与通胀预期"],
        ),
        "gold:physical": (
            [],
            "黄金需结合实际利率、美元及全球风险事件研究。",
            "单凭中国CPI或新闻热度不能推断金价方向。",
            ["实际利率", "美元/汇率", "全球风险与估值"],
        ),
    }
    profile = profiles.get(direction["id"])
    if not profile:
        return {
            "methodVersion": VERSION,
            "summary": "该方向尚无匹配的宏观渠道，需独立指数与资产证据。",
            "channels": [],
            "missingEvidence": ["当地经济与利率", "指数估值/盈利与风险"],
        }
    codes, impact, counter, checks = profile
    observations = [
        i
        for i in context["items"]
        if i["code"] in codes and i["interpretation"]["level"] != "UNAVAILABLE"
    ]
    observation = (
        " ".join(i["interpretation"]["summary"] for i in observations)
        or "当前宏观观察不足，先核查所需证据。"
    )
    pending = any(i["valueVerification"] != "VERIFIED" for i in observations)
    if pending:
        observation = "基于尚待官方核验的已采集值：" + observation
    return {
        "methodVersion": VERSION,
        "summary": f"{direction.get('label', '')}的宏观研究渠道与待验证条件。",
        "channels": [
            {
                "label": direction.get("label", direction["id"]),
                "evidenceCodes": [i["code"] for i in observations],
                "observation": observation,
                "impact": impact,
                "counterCase": counter,
                "watchFor": checks,
            }
        ],
        "missingEvidence": checks,
    }
