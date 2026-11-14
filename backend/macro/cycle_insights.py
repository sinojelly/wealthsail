"""Optional cycle outlook: preserve views, keep clocks and references honest."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlsplit


def sanitize_cycle_insights(raw, *, aliases, now, warnings):
    """Normalize a local display extension without gating report publication.

    A model's fact/expectation label is attribution, never independent fact
    verification. No rates, probabilities or industry stages are inferred here.
    """
    from backend.macro.research_sanitize import _references, _text, _warn

    if isinstance(raw, str) and raw.strip():
        raw = {"marketRegime": {"summary": raw}}
    if not isinstance(raw, dict):
        return None

    def clock(value, path):
        if value is None or value == "":
            return None
        try:
            if not isinstance(value, str):
                raise ValueError("invalid clock")
            if len(value) == 10:
                parsed = date.fromisoformat(value)
                valid = parsed <= now.date()
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                valid = parsed.tzinfo is not None and parsed <= now
            if valid:
                return value
        except (ValueError, TypeError):
            pass
        _warn(
            warnings,
            path,
            "CYCLE_CLOCK_CLEARED",
            "周期观点时间未能对应，保留观点并清空无效时点。",
        )
        return None

    def insight(value, path):
        if isinstance(value, str):
            value = {"summary": value}
        if not isinstance(value, dict):
            return None
        row = {
            field: _text(value.get(field))
            for field in (
                "title",
                "stage",
                "summary",
                "impact",
                "action",
                "industryId",
                "label",
                "driver",
            )
        }
        if not any(row.values()):
            return None
        kind = value.get("evidenceKind")
        row.update(
            evidenceKind=kind
            if kind in ("OBSERVED_FACT", "MARKET_EXPECTATION", "MODEL_ASSESSMENT")
            else "MODEL_ASSESSMENT",
            factVerification="MODEL_UNVERIFIED",
            asOf=clock(value.get("asOf"), path + ".asOf"),
            sourceIds=_references(
                value.get("sourceIds"), aliases, warnings, path + ".sourceIds"
            ),
        )
        if value.get("importance") in ("HIGH", "MEDIUM", "LOW"):
            row["importance"] = value["importance"]
        return row

    result = {"version": "macro-cycle-v1"}
    for key in ("globalLiquidity", "domesticCycle", "marketRegime"):
        row = insight(raw.get(key), "cycleInsights." + key)
        if row:
            result[key] = row
    for key in ("keyFactors", "industries"):
        values = raw.get(key)
        values = (
            values
            if isinstance(values, list)
            else [values]
            if isinstance(values, (dict, str))
            else []
        )
        rows = [
            insight(value, f"cycleInsights.{key}[{index}]")
            for index, value in enumerate(values)
        ]
        result[key] = [row for row in rows if row]
    return (
        result
        if len(result) > 3 or result["keyFactors"] or result["industries"]
        else None
    )


_FED = re.compile(
    r"美联储|联储行长|联储主席|FOMC|哈马克|戴利|鲍威尔|沃勒|威廉姆斯|杰斐逊|鲍曼|Hammack|Daly|Powell",
    re.I,
)
_FORECASTER = re.compile(
    r"高盛|大摩|摩根士丹利|摩根大通|小摩|花旗|瑞银|巴克莱|国际货币基金组织|世界银行|\bIMF\b|Goldman Sachs|Morgan Stanley|JPMorgan",
    re.I,
)
_RATES = re.compile(
    r"加息|降息|通胀|利率|货币政策|通缩|rate hike|rate cut|interest rate|inflation",
    re.I,
)
_FORECAST = re.compile(
    r"预计|预期|预测|上调|下调|展望|forecast|expect|projection", re.I
)
_ECONOMY = re.compile(r"GDP|增长|生产总值", re.I)
_PRICE_DRIVER = re.compile(
    r"AI.{0,8}(?:投资|资本开支|需求)|涨价|提价|关税|能源|原油|供给", re.I
)


def _rows(value):
    return (
        [row for row in value if isinstance(row, dict)]
        if isinstance(value, list)
        else []
    )


def _number(raw):
    if isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
        return (
            value
            if value.is_finite() and abs(value) <= Decimal("10000000000")
            else None
        )
    except (InvalidOperation, ValueError):
        return None


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _visible(row, cutoff):
    from backend.macro.daily_research import _stamp
    from backend.macro.daily_research import _visible as fact_visible

    raw = row.get("asOf")
    if isinstance(raw, str) and len(raw) > 10:
        stamp = _stamp(raw)
        if stamp is None or stamp > cutoff:
            return False
    return fact_visible(row, cutoff) and all(
        not row.get(key)
        or ((day := _day(row[key])) is not None and day <= cutoff.date())
        for key in ("date", "sourceDate")
    )


def policy_signals(packet, report, cutoff):
    """Extract only direct source excerpts from the selected report packet."""
    alpha = packet.get("alphaPublic")
    report_sources = [
        row
        for row in _rows(report.get("sources"))
        if row.get("verification") == "SOURCE_CHECKED"
    ]
    rows = [
        *_rows(alpha.get("catalysts") if isinstance(alpha, dict) else None),
        *_rows(packet.get("sourceItems")),
        *_rows(packet.get("source_items")),
        *_rows(packet.get("documents")),
        *report_sources,
    ]
    signals, seen = [], set()
    for source in rows:
        if not _visible(source, cutoff):
            continue
        clock = next(
            (
                source[key]
                for key in ("publishedAt", "publishedDate", "asOf", "retrievedAt")
                if isinstance(source.get(key), str) and _day(source[key])
            ),
            None,
        )
        uri = source.get("sourceUri") or source.get("url")
        try:
            parts = urlsplit(uri) if isinstance(uri, str) else None
            if (
                not parts
                or parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
            ):
                continue
        except ValueError:
            continue
        title = source.get("title") if isinstance(source.get("title"), str) else ""
        content = next(
            (
                source[key]
                for key in ("content", "quote")
                if isinstance(source.get(key), str)
            ),
            "",
        )
        # Numbered news digests need sentence-level qualification. A generic
        # headline must not lend an unrelated sentence a source affiliation.
        for line in dict.fromkeys([*re.split(r"[。！？；\r\n]+", content), title]):
            text = re.sub(r"\s+", " ", line).strip(" ，。；")
            if len(text) < 10 or text in seen:
                continue
            if _FED.search(text) and _RATES.search(text):
                kind = "CENTRAL_BANK_REMARK"
            elif (
                _FORECASTER.search(text)
                and _FORECAST.search(text)
                and (_RATES.search(text) or _ECONOMY.search(text))
            ):
                kind = "INSTITUTION_FORECAST"
            elif (
                _RATES.search(text)
                and _PRICE_DRIVER.search(text)
                and re.search(r"通胀|inflation", text, re.I)
            ):
                kind = "INFLATION_DRIVER"
            else:
                continue
            seen.add(text)
            signals.append(
                {
                    "kind": kind,
                    "title": title[:180],
                    "text": text[:300],
                    "sourceUri": uri,
                    "asOf": clock,
                    "sourceId": source.get("id"),
                }
            )
    return sorted(signals, key=lambda row: row["asOf"] or "", reverse=True)[:18]


def rate_expectation(series):
    """Separate a dated bond-market signal from an FOMC outcome or probability."""
    rows = {
        row.get("code"): row for row in _rows(series) if row.get("state") == "READY"
    }
    two, fed, ten = (
        rows.get(code, {})
        for code in ("US_T2_NOMINAL", "US_FED_FUNDS", "US_T10_NOMINAL")
    )
    two_day, fed_day, ten_day = (_day(row.get("asOf")) for row in (two, fed, ten))
    two_rate, fed_rate = (_number(row.get("value")) for row in (two, fed))
    spread = (
        (two_rate - fed_rate) * 100
        if two_day
        and fed_day
        and abs(two_day - fed_day) <= timedelta(days=4)
        and two_rate is not None
        and fed_rate is not None
        else None
    )

    def change(row):
        values = row.get("changes")
        return (
            _number(values.get("change5Samples"))
            if isinstance(values, dict) and values.get("changeUnit") == "bp"
            else None
        )

    move2, move10 = change(two), change(ten)
    if all(value is None for value in (spread, move2, move10)):
        return None
    upward = any(value is not None and value >= 25 for value in (spread, move2))
    downward = all(value is None or value <= 0 for value in (spread, move2)) and any(
        value is not None and value <= -25 for value in (spread, move2)
    )
    state, text = (
        ("TIGHTER", "2年期美债定价倾向利率上行或高位维持")
        if upward
        else ("EASIER", "2年期美债定价倾向利率下行")
        if downward
        else ("MIXED", "2年期美债相对有效利率未呈现明确方向")
    )
    if spread is None and move2 is None:
        text = "暂缺2年期有效比较，以下仅反映10年期收益率变化"
    parts = [text]
    if move10 is not None and move10 >= 25:
        parts.append("长端融资成本近5个观测快速上行")
    for label, value in (
        ("2年期−有效利率", spread),
        ("2年期近5个观测", move2),
        ("10年期近5个观测", move10),
    ):
        if value is not None:
            parts.append(f"{label} {value:+.0f} bp")
    return {
        "state": state,
        "asOf": (two_day or ten_day).isoformat() if two_day or ten_day else None,
        "spreadBp": float(spread) if spread is not None else None,
        "us2yChange5Bp": float(move2) if move2 is not None else None,
        "us10yChange5Bp": float(move10) if move10 is not None else None,
        "summary": "，".join(parts) + "。收益率还受期限溢价影响。",
    }


def _plan_position(plan):
    if plan.get("priceBasis") != "UNIT_NAV" or plan.get("market") not in (
        None,
        "FUND_CN",
    ):
        return None
    code, current = plan.get("fundCode"), _number(plan.get("currentPrice"))
    if (
        not isinstance(code, str)
        or not re.fullmatch(r"[0-9]{6}", code)
        or current is None
        or current <= 0
    ):
        return None
    tracks = _rows(plan.get("tracks"))
    selected = plan.get("selectedTrack") or plan.get("track")
    tracks.sort(key=lambda track: track.get("track") != selected)
    for track in tracks:
        low, high = (
            _number(track.get(key)) for key in ("entryZoneMin", "entryZoneMax")
        )
        if low is None or high is None or not 0 < low <= high:
            continue
        sell = _number(track.get("sellZoneMin") or track.get("takeProfitT1"))
        stop = _number(track.get("stopLossPrice"))
        indicators = plan.get("indicators")
        percentile = (
            _number(indicators.get("navPercentile2y"))
            if isinstance(indicators, dict)
            else None
        )
        distance = max(low - current, current - high, Decimal(0)) / current * 100
        right = any(
            row.get("track") == "RIGHT_TREND"
            and row.get("state") == "CANDIDATE"
            and row.get("entryBlocked") is not True
            for row in tracks
        )
        if sell is not None and sell > high and current >= sell:
            stage = "高位兑现·入卖区"
        elif stop is not None and stop > 0 and current <= stop:
            stage = "跌破止损参考·风险复核"
        elif distance == 0 and percentile is not None and 0 <= percentile < 20:
            stage = "低分位·已入买区"
        elif distance < 2 and right:
            stage = "低吸/右侧观察"
        elif distance == 0:
            stage = "已入买区·等待验证"
        elif current < low:
            stage = "低于买区·等待企稳"
        elif distance < 2:
            stage = "接近买区·等待验证"
        else:
            stage = "景气分化·等待回调入区"
        return code, stage, plan.get("asOf"), round(float(distance), 2)
    return None


_REVIEW_NOISE = re.compile(
    r"旧报告未执行|阶段未单列|等待本轮研究补齐|反方核查|尚未核验|以证据包为准"
)
_STAGE_LABELS = {
    "EARNINGS_DELIVERY": "业绩兑现",
    "POLICY_CATALYST": "政策/事件催化",
    "EARLY_COMMERCIALIZATION": "商业化早期",
}


def industry_position_rows(report):
    scan = report.get("quantitativeScan")
    by_fund = {
        row.get("fundCode"): row
        for row in _rows(scan.get("items") if isinstance(scan, dict) else None)
        if isinstance(row.get("fundCode"), str)
    }
    result = []
    for row in _rows(report.get("valueDirections")):
        if not isinstance(row.get("title"), str) or not row["title"].strip():
            continue
        cycle = (
            row.get("industryCycle")
            if isinstance(row.get("industryCycle"), dict)
            else {}
        )
        stage = (
            _STAGE_LABELS.get(cycle.get("stage"))
            if isinstance(cycle.get("stage"), str)
            else None
        )
        plan = next(
            (
                position
                for item in _rows(row.get("plans"))
                if (position := _plan_position(item))
            ),
            None,
        )
        codes = row.get("quantitativeFundCodes")
        if plan is None and isinstance(codes, list):
            plan = next(
                (
                    position
                    for code in codes
                    if isinstance(code, str)
                    and (position := _plan_position(by_fund.get(code, {})))
                ),
                None,
            )
        summaries = []
        for key in ("catalysts", "risks"):
            notes = row.get(key)
            if isinstance(notes, list):
                summaries.extend(
                    note.get("summary") if isinstance(note, dict) else note
                    for note in notes[:1]
                )
        if stage:
            summaries.insert(0, cycle.get("evidenceSummary"))
        summaries.append(row.get("thesis") or row.get("summary"))
        clauses = [
            clause.strip()
            for summary in summaries
            if isinstance(summary, str)
            for clause in re.split(r"(?<=[。；])", summary)
            if clause.strip() and not _REVIEW_NOISE.search(clause)
        ]
        insight = {
            "label": row["title"],
            "stage": stage or (plan[1] if plan else "继续观察"),
            "driver": "".join(dict.fromkeys(clauses))[:330],
            "sourceIds": cycle.get("sourceIds") or row.get("sourceIds") or [],
            "evidenceKind": "MODEL_ASSESSMENT",
            "origin": "原报告产业研判"
            if stage
            else "原报告基金价格位置"
            if plan
            else "原报告方向",
        }
        identities = row.get("reviewIndustryIds")
        if (
            isinstance(identities, list)
            and len(identities) == 1
            and isinstance(identities[0], str)
        ):
            insight["industryId"] = identities[0]
        if plan and not stage:
            insight.update(fundCode=plan[0], asOf=plan[2], buyDistancePct=plan[3])
        result.append(insight)
    return result


_SNAPSHOT_CODES = {
    "PMI",
    "CPI_YOY",
    "PPI_YOY",
    "M1_YOY",
    "M2_YOY",
    "M1_M2_GAP",
    "TSF_STOCK_YOY",
    "CN_T10",
    "US_T10_NOMINAL",
    "US_T10_REAL",
    "US_T2_NOMINAL",
    "US_FED_FUNDS",
    "US_CPI_YOY",
    "US_CPI_MOM",
    "US_CORE_CPI_YOY",
    "US_CORE_CPI_MOM",
    "US_PPI_YOY",
    "US_PPI_MOM",
    "US_BRENT",
}
_SNAPSHOT_FIELDS = (
    "label",
    "value",
    "unit",
    "asOf",
    "date",
    "referenceDate",
    "referencePeriod",
    "state",
    "sourceUri",
    "retrievedAt",
    "publishedAt",
    "availableAt",
    "definitionVersion",
    "definitionKey",
    "periodChange",
    "frequency",
)


def macro_snapshot(packet, cutoff):
    from backend.macro.daily_research import _pick

    data = {"items": [], "series": []}
    for raw_key, target_key in (("macros", "items"), ("series", "series")):
        for row in _rows(packet.get(raw_key)):
            code = row.get("code")
            if (
                not isinstance(code, str)
                or code not in _SNAPSHOT_CODES
                or not _visible(row, cutoff)
            ):
                continue
            parsed = _pick(row, ("code", *_SNAPSHOT_FIELDS))
            history = [
                _pick(point, _SNAPSHOT_FIELDS)
                for point in _rows(row.get("history"))
                if _visible(point, cutoff)
            ][-36:]
            if history:
                parsed["history"] = history
            if parsed.get("value") is None and not history:
                continue
            changes = row.get("changes") or row.get("statistics")
            if isinstance(changes, dict):
                parsed["changes"] = _pick(
                    changes,
                    ("previousValue", "periodChange", "change5Samples", "changeUnit"),
                )
            data[target_key].append(parsed)
    return data if any(data.values()) else None


def project_cycle_insights(report, packet, cutoff):
    """Read-time enrichment for saved reports; neither facts nor plans mutate."""
    packet = packet or {}
    existing = report.get("cycleInsights")
    cycle = dict(existing) if isinstance(existing, dict) else {}
    signals = policy_signals(packet, report, cutoff)
    if signals:
        cycle["policySignals"] = signals
    rates = rate_expectation(
        [row for row in _rows(packet.get("series")) if _visible(row, cutoff)]
    )
    if rates:
        cycle["rateExpectation"] = rates
    snapshot = macro_snapshot(packet, cutoff)
    if snapshot:
        cycle["macroSnapshot"] = snapshot
    industries = [
        row
        for row in _rows(cycle.get("industries"))
        if isinstance(row.get("stage"), str)
        and row["stage"].strip()
        and row["stage"] != "UNKNOWN"
        and not _REVIEW_NOISE.search(row["stage"])
    ]
    known_ids = {
        row["industryId"]
        for row in industries
        if isinstance(row.get("industryId"), str)
    }
    known_labels = {
        row["label"] for row in industries if isinstance(row.get("label"), str)
    }
    industries.extend(
        row
        for row in industry_position_rows(report)
        if row.get("industryId") not in known_ids and row["label"] not in known_labels
    )
    if industries or "industries" in cycle:
        cycle["industries"] = industries
    return {**report, "cycleInsights": cycle} if cycle else report
