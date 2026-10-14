"""Asset-specific evidence requirements; no cross-market universal score."""

from backend.macro.market_repository import CATALOG, _number

VERSION = "opportunity-radar-v1"
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
        ["人民币融资条件", "企业盈利/订单", "指数估值与趋势"],
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
        ["美元/汇率", "当地企业盈利", "跨境资金与估值"],
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
        ["美国本地增长/通胀", "企业盈利", "美元/汇率"],
        "美股",
    ),
    "bond": (["CN_T10", "DR007"], ["收益率曲线/久期", "信用利差/违约风险"], "债券"),
    "gold": (["US_T10_REAL"], ["美元/汇率", "全球事件/黄金估值"], "黄金"),
}


def profile(direction):
    market = (
        direction.get("market_id", direction.get("marketId"))
        or direction.get("id", "").split(":")[0]
    )
    return PROFILES.get(
        market, ([], ["当地资产基本面/价格/风险"], direction.get("label", "当前方向"))
    )


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
    supports, counters, missing = [], [], list(checks)
    present = set()
    for code in required:
        item = items.get(code)
        if not item or not _usable(item):
            if not code.startswith("INDEX_"):
                missing.append((item or {}).get("label") or code)
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
        if code not in required:
            continue
        if market_evidence and all(
            _trend_usable(e)
            if field == "trend"
            else e.get(field, {}).get("state") == "READY"
            for e in market_evidence
        ):
            present.add(code)
            if field == "trend":
                above = all(
                    _number(e["trend"]["distanceMA200"]) > 0
                    and _number(e["trend"]["momentum63"]) > 0
                    for e in market_evidence
                )
                (supports if above else counters).append(
                    {
                        "label": "指数长期趋势",
                        "summary": "当前已核查指数均高于MA200且63期动量为正；估值和产品质量仍需分别核验。"
                        if above
                        else "当前已核查指数尚未全部同时满足高于MA200和63期正动量，需继续核查趋势条件。",
                        "evidenceIds": [e["indexId"] for e in market_evidence],
                        "state": "OBSERVED",
                    }
                )
        else:
            missing.append(
                {
                    "trend": "当前指数趋势",
                    "valuation": "当前指数估值",
                    "earnings": "企业盈利证据",
                }[field]
            )
    missing = list(dict.fromkeys(missing))
    if "INDEX_TREND" in present and "INDEX_VALUATION" in present:
        missing = [gap for gap in missing if gap != "指数估值与趋势"]
    return {
        "methodVersion": VERSION,
        "state": "PARTIAL" if present else "UNKNOWN",
        "supports": supports,
        "counterFactors": counters,
        "watchFor": checks,
        "requiredEvidence": required,
        "missingEvidence": missing,
        "label": market_label,
    }
