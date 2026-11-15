"""Optional cycle outlook: preserve views, keep clocks and references honest."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import unicodedata
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
_SPEAKERS = tuple(
    (key, re.compile(pattern, re.I))
    for key, pattern in (
        ("HAMMACK", r"哈马克|\bHammack\b"),
        ("DALY", r"戴利|\bDaly\b"),
        ("POWELL", r"鲍威尔|\bPowell\b"),
        ("WALLER", r"沃勒|\bWaller\b"),
        ("WILLIAMS", r"威廉姆斯|\bWilliams\b"),
        ("JEFFERSON", r"杰斐逊|\bJefferson\b"),
        ("BOWMAN", r"鲍曼|\bBowman\b"),
    )
)
_STANCE = (
    (
        "TIGHTER",
        re.compile(
            r"加息|提高利率|收紧(?:货币|政策)|限制性(?:立场|政策)|(?:利率|政策).{0,6}限制性|rate hike",
            re.I,
        ),
    ),
    ("EASIER", re.compile(r"降息|下调利率|降低利率|放松(?:货币|政策)|rate cut", re.I)),
    (
        "HOLD",
        re.compile(
            r"维持.{0,12}(?:利率|区间|不变)|利率.{0,12}(?:维持|按兵不动)|暂停(?:加息|降息)"
        ),
    ),
)
_PUBLISHER_PREFIX = re.compile(
    r"^\s*(?:\d{1,2}[、.)）]\s*|【[^】]{4,180}】\s*(?=.{12,})|"
    r"财联社\s*\d{1,2}月\d{1,2}日(?:电|讯)[，,:：]?\s*)+"
)
_CME_ORIGIN = re.compile(r"(?<![a-z])CME(?![a-z])|芝商所|美联储观察|FedWatch", re.I)
_RATE_MONTH = re.compile(
    r"(?<!\d)(?P<relative>明年|次年|今年|\d{4}年)?\s*(?P<month>1[0-2]|[1-9])\s*月"
    r"(?!\s*\d{1,2}\s*[日号])"
)
_ODDS = re.compile(
    r"(?:(?P<action>加息|降息)\s*(?P<step>\d{1,3})\s*(?:个?\s*基点|bps?)"
    r"|(?:维持|保持)(?:当前|现有)?(?:利率(?:水平|区间)?)?(?:不变)?|利率不变|按兵不动)"
    r"\s*的?\s*(?:概率|可能性)\s*(?:为|是|达到|约|[:：])?\s*(?P<chance>\d{1,3}(?:\.\d+)?)\s*%",
    re.I,
)


def _speaker(text):
    matches = {key for key, pattern in _SPEAKERS if pattern.search(text)}
    return next(iter(matches)) if len(matches) == 1 else None


def _stance(text):
    categories = {key for key, pattern in _STANCE if pattern.search(text)}
    # Opposing words and explicit negation need their full text preserved.
    if len(categories) != 1 or re.search(
        r"(?:不会|无须|无需|不宜|反对|不支持|不会考虑).{0,8}(?:加息|降息|收紧)", text
    ):
        return None
    return next(iter(categories))


def _compact_policy_text(value):
    return re.sub(r"[^\w\d%]+", "", unicodedata.normalize("NFKC", value)).casefold()


def _forecast_period(month, clock):
    month_number = int(month["month"])
    day = _day(clock)
    year = day.year if day else None
    relative = month["relative"]
    if relative and relative[0].isdigit():
        year = int(relative[:4])
    elif year is not None and (
        relative in {"明年", "次年"} or not relative and month_number < day.month
    ):
        year += 1
    return year, month_number


def _forecast_tables(text, clock):
    months = list(_RATE_MONTH.finditer(text))
    tables = {}
    for index, month in enumerate(months):
        end = months[index + 1].start() if index + 1 < len(months) else len(text)
        block = text[month.end() : end]
        period = (*_forecast_period(month, clock), "累计" in block)
        for row in _ODDS.finditer(block):
            chance = Decimal(row["chance"])
            step = int(row["step"] or 0)
            if chance <= 100 and (not row["action"] or step > 0):
                tables.setdefault(period, set()).add(
                    (-step if row["action"] == "降息" else step, chance)
                )
    return tables


def _forecast_snapshot(text, clock):
    tables = _forecast_tables(text, clock)
    if len(tables) == 1:
        period, outcomes = next(iter(tables.items()))
        return period, frozenset(outcomes)
    return (None, frozenset()) if tables else None


def _same_policy_signal(a, b):
    if a["kind"] != b["kind"] or a["day"] != b["day"]:
        return False
    if a["textKey"] == b["textKey"] and a["claims"] == b["claims"]:
        return True
    if a["odds"] and b["odds"]:
        (period_a, outcomes_a), (period_b, outcomes_b) = a["odds"], b["odds"]
        return (
            period_a is not None
            and period_a == period_b
            and (outcomes_a <= outcomes_b or outcomes_b <= outcomes_a)
        )
    shorter, longer = sorted((a["textKey"], b["textKey"]), key=len)
    if len(shorter) >= 15 and shorter in longer and a["claims"] == b["claims"]:
        return True
    if not (
        a["speaker"]
        and a["speaker"] == b["speaker"]
        and a["stance"]
        and a["stance"] == b["stance"]
        and (a["claims"] == b["claims"] or not a["claims"] or not b["claims"])
    ):
        return False
    same_article = a["sourceUri"] == b["sourceUri"] and a["title"] == b["title"]
    if same_article and (a["headline"] or b["headline"]):
        return True
    # Two comments pointing in the same direction can still have different
    # reasons or conditions. Match shared phrases only for substantive text.
    a_words = {shorter[i : i + 3] for i in range(len(shorter) - 2)}
    b_words = {longer[i : i + 3] for i in range(len(longer) - 2)}
    return len(a_words & b_words) >= max(10, 0.62 * len(a_words))


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


def _policy_sources(packet, report, cutoff):
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
                for key in ("content", "text", "quote")
                if isinstance(source.get(key), str) and source[key].strip()
            ),
            "",
        )
        yield source, clock, uri, title, content


def _fedwatch_source(title, content, uri):
    return bool(
        _CME_ORIGIN.search(f"{title}\n{content}")
        or urlsplit(uri).hostname in {"cmegroup.com", "www.cmegroup.com"}
    )


def fed_rate_expectations(packet, report, cutoff):
    """Resolve sourced FedWatch distributions without pooling different quotes."""
    from backend.macro.daily_research import _stamp

    results = {}
    for source, clock, uri, title, content in _policy_sources(packet, report, cutoff):
        if not clock or not _fedwatch_source(title, content, uri):
            continue
        clock_day = _day(clock)
        quote_stamp = _stamp(clock) or datetime.combine(
            clock_day, datetime.min.time(), tzinfo=cutoff.tzinfo
        )
        for excerpt in (content, title):
            text = _PUBLISHER_PREFIX.sub("", unicodedata.normalize("NFKC", excerpt))
            for (year, month, cumulative), outcomes in _forecast_tables(
                text, clock
            ).items():
                period = f"{year:04d}-{month:02d}"
                if period < cutoff.strftime("%Y-%m"):
                    continue
                total = sum(probability for _, probability in outcomes)
                # Contradictory tiers in one excerpt are often comparisons
                # with an earlier quote. Never combine them as a distribution.
                if total > Decimal("100.25") or len(dict(outcomes)) != len(outcomes):
                    continue
                complete = abs(total - 100) <= Decimal("0.25")
                changes = [
                    {"changeBp": step, "probability": float(probability)}
                    for step, probability in sorted(outcomes)
                ]
                movement = (
                    sum(step * probability for step, probability in outcomes) / 100
                ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                row = {
                    "meetingMonth": period,
                    "cumulative": cumulative,
                    "complete": complete,
                    "hikeProbability": float(
                        sum(p for step, p in outcomes if step > 0)
                    ),
                    "cutProbability": float(sum(p for step, p in outcomes if step < 0)),
                    "holdProbability": float(
                        sum(p for step, p in outcomes if step == 0)
                    ),
                    "expectedChangeBp": float(movement) if complete else None,
                    "outcomes": changes,
                    "mainOutcome": max(
                        changes, key=lambda outcome: outcome["probability"]
                    ),
                    "sourceUri": uri,
                    "asOf": clock,
                    "sourceId": source.get("id"),
                }
                identity = period, cumulative
                rank = complete, quote_stamp, len(outcomes)
                if identity not in results or rank > results[identity][0]:
                    results[identity] = rank, row
    return [results[key][1] for key in sorted(results)]


def policy_signals(packet, report, cutoff):
    """Extract only direct source excerpts from the selected report packet."""
    signals = []
    for source, clock, uri, title, content in _policy_sources(packet, report, cutoff):
        odds_source = _fedwatch_source(title, content, uri)
        # Numbered news digests need sentence-level qualification. A generic
        # headline must not lend an unrelated sentence a source affiliation.
        for line in dict.fromkeys([*re.split(r"[。！？；\r\n]+", content), title]):
            text = unicodedata.normalize(
                "NFKC", _PUBLISHER_PREFIX.sub("", re.sub(r"\s+", " ", line))
            ).strip(" ，。；")
            if len(text) < 10:
                continue
            odds = _forecast_snapshot(text, clock) if odds_source else None
            speaker = _speaker(text)
            if speaker is None and line == title and _FED.search(title):
                # Standalone headlines often name the office only. Borrow
                # the identity solely from this article's single named voice.
                speaker = _speaker(content)
            stance = _stance(text) if speaker else None
            if odds:
                kind = "MARKET_RATE_ODDS"
            elif (
                _FED.search(text)
                and _RATES.search(text)
                and not _ODDS.search(text)
                and not re.search(r"会议日程|例会安排|上月美联储决定", text)
            ):
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
            signals.append(
                {
                    "kind": kind,
                    "title": title[:180],
                    "text": text[:300],
                    "sourceUri": uri,
                    "asOf": clock,
                    "sourceId": source.get("id"),
                    "textKey": _compact_policy_text(text),
                    "day": _day(clock),
                    "speaker": speaker,
                    "stance": stance,
                    "odds": odds,
                    "claims": frozenset(
                        re.findall(
                            r"\d+(?:\.\d+)?\s*(?:%|个?基点|bp)", text, flags=re.I
                        )
                    ),
                    "headline": line == title,
                }
            )
    # Process complete direct excerpts before a title or partial reprint. Keep
    # different odds, attributed stances and report days as separate rows.
    first = sorted(
        signals,
        key=lambda row: (
            len(row["odds"][1]) if row["odds"] else 0,
            not row["headline"],
            len(row["text"]),
        ),
        reverse=True,
    )
    unique = []
    for row in first:
        if not any(_same_policy_signal(row, known) for known in unique):
            unique.append(row)
    fields = ("kind", "title", "text", "sourceUri", "asOf", "sourceId")
    return [
        {key: row[key] for key in fields}
        for row in sorted(unique, key=lambda row: row["asOf"] or "", reverse=True)[:18]
    ]


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
        parts.append("长端融资成本快速上行")
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
    "VIX",
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
    "valueVerification",
)
_MONTHLY_CODES = {
    "PMI",
    "CPI_YOY",
    "PPI_YOY",
    "M1_YOY",
    "M2_YOY",
    "M1_M2_GAP",
    "TSF_STOCK_YOY",
    "US_CPI_YOY",
    "US_CPI_MOM",
    "US_CORE_CPI_YOY",
    "US_CORE_CPI_MOM",
    "US_PPI_YOY",
    "US_PPI_MOM",
}
_MISSING_STATES = {
    "CONFLICT",
    "UNVERIFIED",
    "UNAVAILABLE",
    "NOT_COLLECTED",
    "PERMISSION_REQUIRED",
    "PENDING",
    "FAILED",
    "TIMEOUT",
    "RATE_LIMITED",
}
_AUDIT_CLAUSE = re.compile(
    r"M1.{0,25}(?:口径|(?:新旧)?定义).{0,25}(?:断点|不.{0,5}串|不.{0,5}比)"
    r"|M1.{0,25}(?:不|不能).{0,6}跨.{0,20}(?:口径|定义|断点)"
    r"|(?:社融|企业中长期贷款).{0,35}(?:累计|单点).{0,25}(?:不能替代|不足以|不代表)"
    r"|(?:四城|4城|城市样本).{0,20}(?:不.{0,6}代表全国|不等于全国)"
    r"|\bRETRIEVAL_ONLY\b|(?:未访问|受限).{0,12}CFETS|CFETS.{0,12}(?:未访问|受限)"
    r"|(?:基金)?相对强弱.{0,14}(?:证据不足|基准不足|基准尚缺|缺少基准)"
    r"|布伦特(?:原油)?现货.{0,40}(?:美元/桶|不.{0,9}期货.{0,12}拼接)"
    r"|BLS.{0,16}(?:403|抓取|不可访问)|Polymarket.{0,12}(?:超时|抓取)|网页未打开",
    re.I,
)
_MONEY_SPREAD_MENTION = re.compile(
    r"(?<!\d)(?P<month>1[0-2]|[1-9])月.{0,14}M1\s*[-−—－–/]\s*M2.{0,7}(?:差|剪刀差)",
    re.I,
)


def _reference_month(row):
    value = row.get("referencePeriod")
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}", value):
        day = _day(value + "-01")
        if day:
            return value
    day = next(
        (
            value
            for key in ("referenceDate", "date", "asOf")
            if (value := _day(row.get(key)))
        ),
        None,
    )
    return day.strftime("%Y-%m") if day else None


def _chart_value(row):
    return (
        _number(row.get("value")) if row.get("state") not in _MISSING_STATES else None
    )


def _monthly_points(rows, code):
    values = {}
    for row in rows:
        if row.get("code") != code:
            continue
        for point in [*_rows(row.get("history")), row]:
            period = _reference_month(point)
            if period and _chart_value(point) is not None:
                values[period] = point
    return values


def _month_index(period):
    year, month = map(int, period.split("-"))
    return year * 12 + month


def _month_label(period):
    year, month = map(int, period.split("-"))
    return f"{year}年{month}月"


def _money_supply_spread(data):
    rows = [*data["items"], *data["series"]]
    m1, m2, prior_gaps = (
        _monthly_points(rows, code) for code in ("M1_YOY", "M2_YOY", "M1_M2_GAP")
    )
    shared = sorted(m1.keys() & m2.keys())
    if not shared or (prior_gaps and shared[-1] < max(prior_gaps)):
        return None
    projected = {}
    for period in shared:
        one, two = m1[period], m2[period]
        one_value, two_value = (_chart_value(row) for row in (one, two))
        point = {
            "referencePeriod": period,
            "date": period + "-01",
            "value": float(one_value - two_value),
            "definitionVersion": one.get("definitionVersion"),
            "sourceUri": one.get("sourceUri") or two.get("sourceUri"),
            "state": "STALE"
            if "STALE" in (one.get("state"), two.get("state"))
            else "OBSERVED",
        }
        for field in ("publishedAt", "retrievedAt", "availableAt"):
            if clocks := [row[field] for row in (one, two) if row.get(field)]:
                point[field] = max(clocks)
        projected[period] = point
    newest = shared[-1]
    current = projected[newest]
    history = {**prior_gaps, **projected}
    previous = max(
        (
            period
            for period in history
            if _month_index(period) == _month_index(newest) - 1
        ),
        default=None,
    )
    # M1's January 2025 definition change makes the cross-break delta invalid.
    comparable = previous and not (previous == "2024-12" and newest == "2025-01")
    if comparable:
        before = history[previous]
        current_def, prior_def = (
            current.get("definitionVersion"),
            before.get("definitionVersion"),
        )
        comparable = not current_def or not prior_def or current_def == prior_def
    gap = _chart_value(current)
    previous_gap = _chart_value(history[previous]) if comparable else None
    change = gap - previous_gap if previous_gap is not None else None
    series = {
        **current,
        "code": "M1_M2_GAP",
        "label": "M1−M2 剪刀差",
        "asOf": current["date"],
        "frequency": "MONTHLY",
        "unit": "percentage_points",
        "sourceName": "根据同月M1与M2同比计算",
        "publishedAt": current.get("publishedAt"),
        "retrievedAt": current.get("retrievedAt"),
        "availableAt": current.get("availableAt"),
        "timeBasis": "DERIVED_SAME_MONTH",
        "methodVersion": "same-month-money-supply-spread-v1",
        "metadata": {
            "definitionKey": "M1_M2_GAP",
            "definitionVersion": current.get("definitionVersion"),
        },
        "state": "STALE" if current["state"] == "STALE" else "READY",
        "history": [history[period] for period in sorted(history)[-36:]],
        "changes": {
            "previousValue": float(previous_gap) if previous_gap is not None else None,
            "periodChange": float(change) if change is not None else None,
            "changeUnit": "pp",
            "change5Samples": None,
            "change20Samples": None,
            "median": None,
            "percentile": None,
            "sampleCount": min(36, len(history)),
        },
    }
    one, two = _chart_value(m1[newest]), _chart_value(m2[newest])
    summary = f"{_month_label(newest)} M1同比 {one:.2f}%，M2同比 {two:.2f}%，剪刀差 {gap:+.2f} 个百分点"
    if change:
        direction = (
            "收窄"
            if abs(gap) < abs(previous_gap)
            else "扩大"
            if abs(gap) > abs(previous_gap)
            else "变化"
        )
        summary += f"，较{_month_label(previous)}{direction} {abs(change):.2f} 个百分点"
    return series, {
        "referencePeriod": newest,
        "m1Yoy": float(one),
        "m2Yoy": float(two),
        "gap": float(gap),
        "previousGap": float(previous_gap) if previous_gap is not None else None,
        "periodChange": float(change) if change is not None else None,
        "summary": summary + "。",
    }


def _composite_cycle(data):
    rows = [*data["items"], *data["series"]]
    pmi, cpi, ppi = (
        _monthly_points(rows, code) for code in ("PMI", "CPI_YOY", "PPI_YOY")
    )
    inflation_month = max(cpi.keys() & ppi.keys(), default=None)
    pmi_value = _chart_value(pmi[max(pmi)]) if pmi else None
    cpi_value = _chart_value(cpi[inflation_month]) if inflation_month else None
    ppi_value = _chart_value(ppi[inflation_month]) if inflation_month else None
    gap = ppi_value - cpi_value if inflation_month else None
    rates = [
        row
        for row in rows
        if row.get("code") == "US_T10_REAL" and _chart_value(row) is not None
    ]
    rate = (
        _chart_value(max(rates, key=lambda row: row.get("asOf") or ""))
        if rates
        else None
    )
    labels = []
    if pmi_value is not None:
        labels.append(
            "需求偏冷"
            if pmi_value < 50
            else "景气扩张"
            if pmi_value > 50
            else "景气处于荣枯线"
        )
    if gap is not None and gap >= 2 and ppi_value > 0:
        labels.append("上游成本挤压")
    if rate is not None and rate >= 2:
        labels.append("外部高息紧缩")
    return (
        {
            "labels": labels,
            "pmi": float(pmi_value) if pmi_value is not None else None,
            "ppiYoy": float(ppi_value) if ppi_value is not None else None,
            "cpiYoy": float(cpi_value) if cpi_value is not None else None,
            "inflationGap": float(gap) if gap is not None else None,
            "inflationReferencePeriod": inflation_month,
            "us10yReal": float(rate) if rate is not None else None,
        }
        if labels
        else None
    )


def _display_prose(text, audit, money_month=None):
    if not isinstance(text, str):
        return text
    clean = []
    for clause in re.split(r"(?<=[。；;！？])|\r?\n", text):
        if match := _AUDIT_CLAUSE.search(clause):
            # Keep a substantive lead-in separated from the audit caveat.
            start = max(clause.rfind(mark, 0, match.start()) for mark in ("，", ","))
            if start >= 0:
                clean.append(clause[:start].rstrip() + "。")
            note = clause[start + 1 :].strip(" *\t。；;")
            if note and note not in audit:
                audit.append(note)
        elif not (
            money_month
            and (match := _MONEY_SPREAD_MENTION.search(clause))
            and int(match["month"]) != int(money_month[-2:])
        ):
            clean.append(clause)
    return "".join(clean).strip()


def _display_sections(cycle, report, snapshot):
    existing = cycle.get("auditNotes")
    audit = (
        [note for note in existing if isinstance(note, str)]
        if isinstance(existing, list)
        else []
    )
    period = ((snapshot or {}).get("moneySupplySpread") or {}).get("referencePeriod")
    original = report.get("macroCycle") or report.get("summary")
    display = _display_prose(original, audit, period)
    if display != original:
        cycle["displayMacroCycle"] = display

    def clean_row(row):
        return {
            key: _display_prose(value, audit, period)
            if key in {"summary", "impact", "action", "driver"}
            else value
            for key, value in row.items()
        }

    for field in ("globalLiquidity", "domesticCycle", "marketRegime"):
        if isinstance(cycle.get(field), dict):
            cycle[field] = clean_row(cycle[field])
    if isinstance(cycle.get("keyFactors"), list):
        cycle["keyFactors"] = [clean_row(row) for row in _rows(cycle["keyFactors"])]
    if audit:
        cycle["auditNotes"] = audit


def macro_snapshot(packet, cutoff):
    from backend.macro.daily_research import _pick

    data = {"items": [], "series": []}
    for raw_key, target_key in (("macros", "items"), ("series", "series")):
        for row in _rows(packet.get(raw_key)):
            code = row.get("code")
            if not isinstance(code, str) or code not in _SNAPSHOT_CODES:
                continue
            visible = _visible(row, cutoff)
            parsed = (
                _pick(row, ("code", *_SNAPSHOT_FIELDS))
                if visible
                else {"code": code, "label": row.get("label")}
            )
            history = {
                (
                    _reference_month(point)
                    if code in _MONTHLY_CODES
                    else point.get("date")
                    or point.get("asOf")
                    or point.get("referenceDate")
                ): _pick(point, _SNAPSHOT_FIELDS)
                for point in _rows(row.get("history"))
                if _visible(point, cutoff)
            }
            history = [
                {
                    **history[key],
                    **(
                        {
                            "referencePeriod": key,
                            "date": history[key].get("date")
                            or history[key].get("asOf")
                            or key + "-01",
                        }
                        if code in _MONTHLY_CODES
                        else {}
                    ),
                }
                for key in sorted(key for key in history if isinstance(key, str))[
                    -(212 if code == "VIX" else 36) :
                ]
            ]
            if code in _MONTHLY_CODES and (period := _reference_month(parsed)):
                parsed.update(referencePeriod=period, frequency="MONTHLY")
            metadata = row.get("metadata")
            if visible and isinstance(metadata, dict):
                for field in ("definitionKey", "definitionVersion"):
                    if field not in parsed and metadata.get(field):
                        parsed[field] = metadata[field]
            if history:
                parsed["history"] = history
            if parsed.get("value") is None and not history:
                continue
            changes = (row.get("changes") or row.get("statistics")) if visible else None
            if isinstance(changes, dict):
                parsed["changes"] = _pick(
                    changes,
                    ("previousValue", "periodChange", "change5Samples", "changeUnit"),
                )
            data[target_key].append(parsed)
    if spread := _money_supply_spread(data):
        series, details = spread
        for key in ("items", "series"):
            data[key] = [row for row in data[key] if row["code"] != "M1_M2_GAP"]
        data["series"].append(series)
        data["moneySupplySpread"] = details
    if composite := _composite_cycle(data):
        data["composite"] = composite
    return data if any(data.values()) else None


def project_cycle_insights(report, packet, cutoff):
    """Read-time enrichment for saved reports; neither facts nor plans mutate."""
    packet = packet or {}
    existing = report.get("cycleInsights")
    cycle = dict(existing) if isinstance(existing, dict) else {}
    signals = policy_signals(packet, report, cutoff)
    if signals:
        cycle["policySignals"] = signals
    if fed_odds := fed_rate_expectations(packet, report, cutoff):
        cycle["fedRateExpectations"] = fed_odds
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
    _display_sections(cycle, report, snapshot)
    return {**report, "cycleInsights": cycle} if cycle else report
