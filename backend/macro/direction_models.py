"""Asset-specific evidence requirements; no cross-market universal score."""

from backend.macro.market_repository import CATALOG, _number

VERSION = "opportunity-radar-v2"
EVIDENCE_LABELS = {
    **{code: row[0] for code, row in CATALOG.items()},
    "PMI": "制造业PMI",
    "INDEX_TREND": "当前指数趋势",
    "INDEX_VALUATION": "当前指数估值",
    "INDEX_EARNINGS": "企业盈利证据",
    "USD_FX": "美元/汇率事实",
    "YIELD_CURVE_DURATION": "收益率曲线/久期",
    "CREDIT_SPREAD_DEFAULT": "信用利差/违约事实",
    "LOCAL_ASSET_EVIDENCE": "当地资产基本面/价格/风险",
}
PROFILES = {
    "a-share": (
        [
            "PMI",
            "M1_YOY",
            "TSF_STOCK_YOY",
            "CN_T10",
            "INDEX_TREND",
            "INDEX_VALUATION",
            "INDEX_EARNINGS",
        ],
        ["融资结构与需求", "企业订单", "价格反应"],
        "A股",
    ),
    "hk": (
        [
            "US_T10_NOMINAL",
            "M1_YOY",
            "TSF_STOCK_YOY",
            "INDEX_TREND",
            "INDEX_VALUATION",
            "INDEX_EARNINGS",
        ],
        ["美元/汇率", "跨境资金", "价格反应"],
        "港股",
    ),
    "us": (
        [
            "US_T10_NOMINAL",
            "US_T10_REAL",
            "INDEX_TREND",
            "INDEX_VALUATION",
            "INDEX_EARNINGS",
        ],
        ["美国本地增长/通胀", "美元/汇率", "价格反应"],
        "美股",
    ),
    "bond": (
        ["CN_T10", "DR007", "YIELD_CURVE_DURATION"],
        ["通胀预期", "价格反应"],
        "债券",
    ),
    "gold": (["US_T10_REAL", "USD_FX"], ["全球事件/黄金估值", "价格反应"], "黄金"),
}


def profile(direction):
    market = (
        direction.get("market_id", direction.get("marketId"))
        or direction.get("id", "").split(":")[0]
    )
    required, checks, label = PROFILES.get(
        market,
        (["LOCAL_ASSET_EVIDENCE"], ["价格反应"], direction.get("label", "当前方向")),
    )
    required = list(required)
    if direction.get("id") == "bond:credit":
        required.append("CREDIT_SPREAD_DEFAULT")
    return required, list(checks), label


def _usable(item):
    if item.get("state") != "READY" or _number(item.get("value")) is None:
        return False
    code = item.get("code")
    if code in CATALOG:
        _, frequency, unit, _ = CATALOG[code]
        return item.get("frequency") == frequency and item.get("unit") == unit
    return (
        item.get("valueVerification") not in {"CONFLICT", "UNAVAILABLE"}
        and item.get("interpretation", {}).get("level") != "UNAVAILABLE"
    )


def _trend_usable(evidence):
    trend = evidence.get("trend", {})
    ma200 = _number(trend.get("ma200"))
    return (
        trend.get("state") == "READY"
        and ma200 is not None
        and ma200 > 0
        and all(
            _number(trend.get(field)) is not None
            for field in ("distanceMA200", "momentum63")
        )
    )


def direction_model(direction, context, series, market_evidence):
    required, checks, market_label = profile(direction)
    items = {i["code"]: i for i in context.get("items", []) + series}
    supports, counters, failed = [], [], []
    present = set()
    for code in required:
        # These judgments have no verified adapter yet; an arbitrary READY scalar
        # cannot stand in for a curve, currency exposure or local fundamentals.
        if code not in CATALOG and code != "PMI":
            continue
        item = items.get(code)
        if not item or not _usable(item):
            continue
        present.add(code)
        changes = item.get("changes", {})
        delta = _number(
            changes.get("change5Samples")
            if item.get("frequency") == "DAILY"
            else changes.get("periodChange", item.get("periodChange"))
        )
        if (
            code in {"CN_T10", "US_T10_REAL", "US_T10_NOMINAL", "DR007"}
            and delta is not None
            and delta != 0
            and changes.get("changeUnit") == "bp"
        ):
            higher = delta > 0
            message = f"{item.get('label', code)}近5个观察样本变化{delta:+.2f}基点。"
            (counters if higher else supports).append(
                {
                    "label": "利率变化渠道",
                    "summary": message
                    + (
                        "较高利率可能增加折现/融资压力；盈利与风险偏好可能抵消。"
                        if higher
                        else "较低利率是估值/融资条件研究线索；若伴随盈利走弱，未必利好。"
                    ),
                    "evidenceIds": [code],
                    "state": "OBSERVED",
                }
            )
        elif (
            code in {"M1_YOY", "TSF_STOCK_YOY"}
            and delta is not None
            and delta != 0
            and changes.get("changeUnit") == "pp"
        ):
            (supports if delta > 0 else counters).append(
                {
                    "label": "信用与货币渠道",
                    "summary": f"{item.get('label', code)}增速较前月{delta:+.2f}个百分点，需需求、结构与融资成本确认。",
                    "evidenceIds": [code],
                    "state": "OBSERVED",
                }
            )
        elif code == "PMI" and _number(item["value"]) != 50:
            (supports if _number(item["value"]) > 50 else counters).append(
                {
                    "label": "制造业景气",
                    "summary": item.get("interpretation", {}).get(
                        "summary", "制造业景气需订单/就业确认。"
                    ),
                    "evidenceIds": [code],
                    "state": "OBSERVED",
                }
            )
    for code, field in (
        ("INDEX_TREND", "trend"),
        ("INDEX_VALUATION", "valuation"),
        ("INDEX_EARNINGS", "earnings"),
    ):
        if code not in required and field != "trend":
            continue
        available = [
            e
            for e in market_evidence
            if (
                _trend_usable(e)
                if field == "trend"
                else e.get(field, {}).get("state") == "READY"
            )
        ]
        if market_evidence and len(available) == len(market_evidence):
            present.add(code)
        if field == "trend" and available:
            positive, unmet = [], []
            for evidence in available:
                (
                    positive
                    if _number(evidence["trend"]["distanceMA200"]) > 0
                    and _number(evidence["trend"]["momentum63"]) > 0
                    else unmet
                ).append(evidence["indexId"])
            if positive:
                supports.append(
                    {
                        "label": "指数长期趋势",
                        "summary": "当前以下已核查指数高于MA200且63期动量为正；估值和产品质量仍需分别核验。",
                        "evidenceIds": positive,
                        "state": "OBSERVED",
                    }
                )
            if unmet:
                reason = "当前尚未同时满足高于MA200和63期正动量。"
                failed.append(
                    {
                        "id": "INDEX_TREND",
                        "label": EVIDENCE_LABELS["INDEX_TREND"],
                        "reason": reason,
                        "evidenceIds": unmet,
                    }
                )
                counters.append(
                    {
                        "label": "指数长期趋势",
                        "summary": reason,
                        "evidenceIds": unmet,
                        "state": "OBSERVED",
                    }
                )
    missing = [code for code in required if code not in present]
    qualification = (
        "INCOMPLETE" if missing else "BLOCKED" if counters or failed else "READY"
    )
    research = (
        "RISK_OBSERVATION"
        if any(row["label"] != "指数长期趋势" for row in counters)
        else "PRIORITY_RESEARCH"
        if any(row["label"] == "指数长期趋势" for row in supports)
        else "WAIT_CONDITIONS"
        if present or failed
        else "INSUFFICIENT"
    )
    return {
        "methodVersion": VERSION,
        "state": "READY"
        if not missing
        else "PARTIAL"
        if present or supports or counters
        else "UNKNOWN",
        "supports": supports,
        "counterFactors": counters,
        "watchFor": checks,
        "recommendedChecks": checks,
        "requiredEvidence": required,
        "missingRequiredEvidence": missing,
        "missingEvidence": [EVIDENCE_LABELS[code] for code in missing],
        "failedConditions": failed,
        "researchState": research,
        "qualificationState": qualification,
        "label": market_label,
    }
