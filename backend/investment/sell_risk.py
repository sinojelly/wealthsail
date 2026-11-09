"""Actor-only risk projections, independent of the public model and trade ledger."""

from copy import deepcopy
from datetime import timedelta, time
from decimal import Decimal, InvalidOperation
import re

from backend.investment.study_price_watch import (
    _day,
    _number,
    _object,
    _source_uri,
    _stamp,
    _track,
    _trading_time,
    _UNITS,
)

_STATES = {
    "STOP_LOSS": 0,
    "TAKE_PROFIT_T2": 1,
    "TAKE_PROFIT_T1": 2,
    "RSI_OVERBOUGHT": 3,
}
_ACTION_STATES = {"STOP_LOSS": 0, "SELL_ZONE": 1, "BUY_ZONE": 2, "LARGE_DAILY_MOVE": 3}


def _rsi(value):
    try:
        result = Decimal(str(value)) if not isinstance(value, bool) else None
        return (
            result
            if result is not None and result.is_finite() and 0 <= result <= 100
            else None
        )
    except (ValueError, TypeError, InvalidOperation):
        return None


def _valid_quote(quote, target, now, *, estimate=False, intraday=False):
    if not isinstance(quote, dict) or quote.get("usable") is not True:
        return False
    day, retrieved = _day(quote.get("asOf")), _stamp(quote.get("retrievedAt"))
    plan = target.get("plan") or {}
    if (
        quote.get("fundCode") != target.get("fundCode")
        or quote.get("subjectKind") != target.get("subjectKind")
        or str(quote.get("subjectId")) != str(target.get("subjectId"))
        or quote.get("priceBasis") != "UNIT_NAV"
        or quote.get("unit") not in _UNITS
        or _number(quote.get("value")) is None
        or day is None
        or day > now.date()
        or retrieved is None
        or retrieved > now
        or day > retrieved.date()
        or _source_uri(quote.get("sourceUri")) is None
        or plan
        and (_day(plan.get("asOf")) is None or day < _day(plan["asOf"]))
    ):
        return False
    if not estimate:
        return quote.get("quality") == "READY" and (now.date() - day).days <= 10
    stamp = _stamp(quote.get("asOf"))
    if intraday:
        checked = _stamp(quote.get("checkedAt"))
        return bool(
            quote.get("quality") == "ESTIMATED"
            and quote.get("tradingDayConfirmed") is True
            and stamp is not None
            and checked is not None
            and stamp <= checked <= now
            and stamp.date() == now.date()
            and _trading_time(stamp)
            and _trading_time(now)
            and now - stamp <= timedelta(minutes=30)
        )
    if not (
        quote.get("quality") in {"ESTIMATED", "READY", "FRESH"}
        and stamp is not None
        and stamp.date() == now.date()
        and time(14) <= stamp.time() < time(15)
        and now.time() < time(15)
        and timedelta(0) <= now - stamp <= timedelta(minutes=30)
    ):
        return False
    coverage = _rsi(quote.get("coveragePct"))
    if quote.get("coveragePct") is not None and (coverage is None or coverage < 60):
        return False
    if any(quote.get(key) is not None for key in ("lowerValue", "upperValue")):
        low, high = _number(quote.get("lowerValue")), _number(quote.get("upperValue"))
        if low is None or high is None or not low <= _number(quote["value"]) <= high:
            return False
    return True


def evaluate_sell_risk(target, *, confirmed=None, estimate=None, now):
    """A reference-line warning is never an instruction or claim of realised profit."""
    now = _stamp(now)
    if now is None:
        raise ValueError("personal risk requires an aware clock")
    relations = target.get("relationship")
    if (
        not isinstance(relations, list)
        or not relations
        or not set(relations) <= {"HELD", "TRACKED"}
    ):
        return None
    if not isinstance(target.get("fundCode"), str) or not re.fullmatch(
        r"[0-9]{6}", target["fundCode"]
    ):
        return None
    plan = target.get("plan")
    track = _track(plan) if isinstance(plan, dict) else None
    if plan is not None and (
        plan.get("priceBasis") != "UNIT_NAV"
        or plan.get("unit") not in _UNITS
        or track is None
    ):
        return None
    confirmed_ok = _valid_quote(confirmed, target, now)
    estimate_ok = _valid_quote(estimate, target, now, estimate=True)
    quote = estimate if estimate_ok else confirmed if confirmed_ok else None
    if quote is None:
        return None
    value = _number(quote["value"])
    rsi = _rsi(confirmed.get("rsi14")) if confirmed_ok else None
    rsi_day = _day(confirmed.get("rsiAsOf")) if confirmed_ok else None
    if not confirmed_ok or rsi_day != _day(confirmed.get("asOf")):
        rsi, rsi_day = None, None
    stop, first, second = (
        _number((track or {}).get(key))
        for key in ("stopLossPrice", "takeProfitT1", "takeProfitT2")
    )
    state = (
        "STOP_LOSS"
        if stop is not None and value <= stop
        else "TAKE_PROFIT_T2"
        if second is not None and value >= second
        else "TAKE_PROFIT_T1"
        if first is not None and value >= first
        else "RSI_OVERBOUGHT"
        if rsi is not None and rsi >= 72
        else None
    )
    if state is None:
        return None
    result = {
        "fundCode": target["fundCode"],
        "label": target.get("label") or target["fundCode"],
        "relationship": [key for key in ("HELD", "TRACKED") if key in relations],
        "state": state,
        "basis": "ESTIMATE_1430" if estimate_ok else "CONFIRMED_NAV",
        "value": str(value),
        "asOf": quote["asOf"],
        "sourceUri": quote["sourceUri"],
        "rsi14": float(rsi) if rsi is not None else None,
        "rsiAsOf": str(rsi_day) if rsi_day else None,
        "planOrigin": target.get("planOrigin") if plan is not None else "NONE",
        "stopLossPrice": str(stop) if stop is not None else None,
        "takeProfitT1": str(first) if first is not None else None,
        "takeProfitT2": str(second) if second is not None else None,
    }
    for key in ("trackId", "reportRunId", "holdingsAsOf"):
        if target.get(key) is not None:
            result[key] = str(target[key])
    if track:
        result["selectedTrack"] = track["track"]
    return result


def _change(value):
    try:
        result = Decimal(str(value)) if not isinstance(value, bool) else None
        return (
            result
            if result is not None and result.is_finite() and result > -100
            else None
        )
    except (InvalidOperation, ValueError, TypeError):
        return None


def _action_plan(target, confirmed, now):
    """A private frozen track wins; unfrozen holdings use current public zones."""
    plan, origin = target.get("plan"), target.get("planOrigin")
    current = (confirmed or {}).get("actionableZones")
    if (
        origin != "FROZEN_STUDY"
        and "HELD" in target["relationship"]
        and isinstance(current, dict)
    ):
        plan, origin = current, "CURRENT_ACTIONABLE_ZONES"
    if not isinstance(plan, dict):
        return None, None, "NONE"
    plan = deepcopy(plan)
    if not plan.get("selectedTrack", plan.get("track")):
        # The public chart lists the left-value track first. Use that same
        # stable ordering rather than inventing a user preference.
        plan["selectedTrack"] = next(
            (
                row.get("track")
                for row in plan.get("tracks", [])
                if isinstance(row, dict)
                and row.get("priceState") == "READY"
                and _track({**plan, "selectedTrack": row.get("track")}) is not None
            ),
            None,
        )
    plan = _comparable_plan(plan, target, now)
    track = _track(plan) if plan else None
    if (
        track is None
        or track.get("priceState") != "READY"
        or plan.get("historyQuality", "READY") != "READY"
    ):
        return None, None, "NONE"
    return plan, track, origin or "NONE"


def evaluate_action_reminder(target, *, confirmed=None, estimate=None, now):
    """Project only price-zone hits and material daily moves for this actor."""
    now = _stamp(now)
    if now is None:
        raise ValueError("personal reminders require an aware clock")
    relations = target.get("relationship")
    if (
        not isinstance(relations, list)
        or not relations
        or not set(relations) <= {"HELD", "TRACKED"}
    ):
        return None
    if not isinstance(target.get("fundCode"), str) or not re.fullmatch(
        r"[0-9]{6}", target["fundCode"]
    ):
        return None
    confirmed_ok = _valid_quote(confirmed, target, now)
    intraday_ok = _valid_quote(estimate, target, now, estimate=True, intraday=True)
    estimate_ok = intraday_ok or _valid_quote(estimate, target, now, estimate=True)
    quote = estimate if estimate_ok else confirmed if confirmed_ok else None
    if quote is None:
        return None
    value = _number(quote["value"])
    plan, track, origin = _action_plan(target, confirmed if confirmed_ok else None, now)
    if plan and _day(quote["asOf"]) < _day(plan["asOf"]):
        plan, track, origin = None, None, "NONE"
    change = _change(quote.get("dailyChangePct", quote.get("changePct")))
    if (
        estimate_ok
        and change is None
        and confirmed_ok
        and _day(confirmed["asOf"]) < now.date()
    ):
        change = (value / _number(confirmed["value"]) - 1) * 100
    low, high, sell, sell_max, stop = (
        _number((track or {}).get(key))
        for key in (
            "entryZoneMin",
            "entryZoneMax",
            "sellZoneMin",
            "sellZoneMax",
            "stopLossPrice",
        )
    )
    if track:
        sell = sell or _number(track.get("takeProfitT1"))
        sell_max = sell_max or _number(track.get("takeProfitT2"))
    triggers = []
    if stop is not None and value <= stop:
        triggers.append("STOP_LOSS")
    elif sell is not None and value >= sell:
        triggers.append("SELL_ZONE")
    elif (
        low is not None
        and low <= value <= high
        and not any(
            row.get("entryBlocked") is True or row.get("subscriptionRestricted") is True
            for row in (plan, track)
        )
    ):
        triggers.append("BUY_ZONE")
    if change is not None and abs(change) >= Decimal("2.5"):
        triggers.append("LARGE_DAILY_MOVE")
    if not triggers and (track is None or "HELD" not in relations):
        return None
    buy_distance = (
        max(low - value, value - high, Decimal(0)) if low is not None else None
    )
    result = {
        "fundCode": target["fundCode"],
        "label": target.get("label") or target["fundCode"],
        "relationship": [key for key in ("HELD", "TRACKED") if key in relations],
        "state": triggers[0] if triggers else "NORMAL",
        "triggerStates": triggers,
        "basis": "INTRADAY_ESTIMATE"
        if intraday_ok
        else "ESTIMATE_1430"
        if estimate_ok
        else "CONFIRMED_NAV",
        "value": str(value),
        "asOf": quote["asOf"],
        "sourceUri": quote["sourceUri"],
        "planOrigin": origin,
        "planAsOf": plan.get("asOf") if plan else None,
        "selectedTrack": track.get("track") if track else None,
        "entryZoneMin": str(low) if low is not None else None,
        "entryZoneMax": str(high) if high is not None else None,
        "sellZoneMin": str(sell) if sell is not None else None,
        "sellZoneMax": str(sell_max) if sell_max is not None else None,
        "stopLossPrice": str(stop) if stop is not None else None,
        "distanceToBuyPct": round(float(buy_distance / value * 100), 4)
        if buy_distance is not None
        else None,
        "distanceToSellPct": round(
            float(max(sell - value, Decimal(0)) / value * 100), 4
        )
        if sell is not None
        else None,
        "distanceToStopPct": round(
            float(max(value - stop, Decimal(0)) / value * 100), 4
        )
        if stop is not None
        else None,
        "dailyChangePct": round(float(change), 4) if change is not None else None,
        "dailyChangeAsOf": quote["asOf"] if change is not None else None,
    }
    for key in ("trackId", "reportRunId", "holdingsAsOf"):
        if target.get(key) is not None:
            result[key] = str(target[key])
    return result


async def _calendar(conn, now):
    rows = await conn.fetch(
        "SELECT calendar_date,is_trading_day FROM reference.trading_calendars WHERE market='SSE' AND calendar_date BETWEEN $1 AND $2 ORDER BY calendar_date",
        now.date() - timedelta(days=1000),
        now.date(),
    )
    return [
        {
            "calendar_date": _day(row.get("calendar_date")),
            "is_trading_day": row["is_trading_day"],
        }
        for row in rows
        if _day(row.get("calendar_date")) is not None
        and type(row.get("is_trading_day")) is bool
    ]


def _identity(row):
    code = row.get("fund_code")
    if (
        not isinstance(code, str)
        or not re.fullmatch(r"[0-9]{6}", code)
        or row.get("market") != "FUND_CN"
        or row.get("mapping_count") != 1
        or row.get("trade_form") not in {"FUND", "LOF"}
        or row.get("currency", "CNY") != "CNY"
    ):
        return None
    instrument = row.get("instrument_id")
    return {
        "fundCode": code,
        "label": str(row.get("fund_name") or code)[:200],
        "subjectKind": "fund" if instrument else "nav",
        "subjectId": str(instrument or code),
        "instrumentId": str(instrument) if instrument else None,
        "relationship": [],
        "plan": None,
        "planOrigin": "NONE",
        "tradeForm": row["trade_form"],
    }


def _comparable_plan(plan, identity, now):
    if (
        not isinstance(plan, dict)
        or plan.get("priceBasis") != "UNIT_NAV"
        or plan.get("unit") not in _UNITS
    ):
        return None
    if plan.get("fundCode") not in {None, identity["fundCode"]} or plan.get(
        "market"
    ) not in {None, "FUND_CN"}:
        return None
    if plan.get("subjectKind") == "nav":
        match = plan.get("subjectId") == identity["fundCode"]
    elif plan.get("subjectKind") == "fund":
        match = (
            identity.get("instrumentId") is not None
            and str(plan.get("subjectId")) == identity["instrumentId"]
        )
    else:
        match = False
    day = _day(plan.get("asOf"))
    if not match or day is None or day > now.date() or _track(plan) is None:
        return None
    return {**deepcopy(plan), "fundCode": identity["fundCode"], "market": "FUND_CN"}


async def load_risk_targets(
    conn, tenant_id, user_id, now, *, daily=None, calendar=None
):
    """Only actor-owned quantities or currently enabled, valid private tracks."""
    now = _stamp(now)
    studies = await conn.fetch(
        """SELECT t.id AS track_id,t.scope,t.enabled,t.generation,t.created_at,v.id AS version_id,
          jsonb_build_object('actionablePlan',v.snapshot->'actionablePlan','expiresOn',v.snapshot->>'expiresOn') AS snapshot,
          c.fund_code,c.fund_name,c.market,c.trade_form,c.currency,c.instrument_id,
          (SELECT count(*) FROM reference.research_fund_catalog other WHERE other.market=c.market AND other.fund_code=c.fund_code) AS mapping_count
        FROM research.study_tracks t JOIN LATERAL (
          SELECT id,snapshot FROM research.study_versions v WHERE v.tenant_id=t.tenant_id AND v.user_id=t.user_id
            AND v.track_id=t.id AND v.created_at<=$3 ORDER BY version DESC LIMIT 1) v ON TRUE
        JOIN LATERAL (SELECT c.* FROM reference.research_fund_catalog c WHERE c.market='FUND_CN'
          AND ((NULLIF(t.scope->>'instrumentId','') IS NOT NULL AND c.instrument_id::text=t.scope->>'instrumentId')
            OR (NULLIF(t.scope->>'instrumentId','') IS NULL AND t.scope->>'fundMarket'='FUND_CN' AND c.fund_code=t.scope->>'fundCode'))
          AND c.trade_form IN ('FUND','LOF') ORDER BY c.fund_code LIMIT 1) c ON TRUE
        WHERE t.tenant_id=$1 AND t.user_id=$2 AND t.enabled AND t.created_at<=$3
          AND EXISTS(SELECT 1 FROM identity.tenant_members m WHERE m.tenant_id=t.tenant_id AND m.user_id=t.user_id AND m.status='ACTIVE')
        ORDER BY t.updated_at DESC,t.id LIMIT 100""",
        tenant_id,
        user_id,
        now,
    )
    positions = await conn.fetch(
        """WITH mine AS (
          SELECT DISTINCT a.id FROM identity.accounts a JOIN identity.account_members m
            ON m.tenant_id=a.tenant_id AND m.account_id=a.id
          JOIN identity.tenant_members t ON t.tenant_id=m.tenant_id AND t.user_id=m.user_id AND t.status='ACTIVE'
          WHERE a.tenant_id=$1 AND m.user_id=$2 AND a.status='ACTIVE'
            AND m.relationship IN ('BENEFICIAL_OWNER','JOINT_OWNER')
            AND m.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
            AND (m.effective_to IS NULL OR m.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date)
        ),latest AS (
          SELECT DISTINCT ON(p.account_id,p.instrument_id) p.instrument_id,p.quantity,p.as_of,p.data_quality_status
          FROM ledger.position_snapshots p JOIN mine ON mine.id=p.account_id
          WHERE p.tenant_id=$1 AND p.created_at<=$3 AND p.as_of<=($3 AT TIME ZONE 'Asia/Shanghai')::date
          ORDER BY p.account_id,p.instrument_id,p.as_of DESC,p.created_at DESC)
        SELECT p.quantity,p.as_of,p.data_quality_status,c.fund_code,c.fund_name,c.market,c.trade_form,c.currency,c.instrument_id,
          (SELECT count(*) FROM reference.research_fund_catalog other WHERE other.market=c.market AND other.fund_code=c.fund_code) AS mapping_count
        FROM latest p JOIN reference.research_fund_catalog c ON c.instrument_id=p.instrument_id
        JOIN reference.instruments i ON i.id=p.instrument_id AND i.lifecycle_status='ACTIVE'
        WHERE c.market='FUND_CN' AND c.trade_form IN ('FUND','LOF')""",
        tenant_id,
        user_id,
        now,
    )
    targets = {}
    for raw in studies:
        row = dict(raw)
        item = _identity(row)
        scope, snapshot = _object(row.get("scope")), _object(row.get("snapshot"))
        expires = _day(snapshot.get("expiresOn"))
        if (
            item is None
            or row.get("enabled") is False
            or snapshot.get("expiresOn") is not None
            and expires is None
            or expires is not None
            and expires < now.date()
            or any(
                scope.get(key) is not None and str(scope[key]) != str(row.get(field))
                for key, field in (
                    ("fundCode", "fund_code"),
                    ("fundMarket", "market"),
                    ("instrumentId", "instrument_id"),
                )
            )
        ):
            continue
        item = targets.setdefault(item["fundCode"], item)
        if "TRACKED" not in item["relationship"]:
            item["relationship"].append("TRACKED")
        plan = _comparable_plan(snapshot.get("actionablePlan"), item, now)
        if plan is not None and item["plan"] is None:
            item.update(
                plan=plan,
                planOrigin="FROZEN_STUDY",
                trackId=str(row["track_id"]),
                versionId=str(row["version_id"]),
                subjectKind=plan["subjectKind"],
                subjectId=plan["subjectId"],
            )
    for raw in positions:
        row = dict(raw)
        item = _identity(row)
        day = _day(row.get("as_of"))
        if (
            item is None
            or _number(row.get("quantity")) is None
            or row.get("data_quality_status") != "FRESH"
            or day is None
            or not now.date() - timedelta(days=10) <= day <= now.date()
        ):
            continue
        item = targets.setdefault(item["fundCode"], item)
        if "HELD" not in item["relationship"]:
            item["relationship"].insert(0, "HELD")
        item["holdingsAsOf"] = max(str(day), item.get("holdingsAsOf", str(day)))
    report = (daily or {}).get("report") or {}
    report_stamp = _stamp(report.get("asOf"))
    if (
        report_stamp is not None
        and report_stamp <= now
        and (now.date() - report_stamp.date()).days <= 10
    ):
        plans = [
            *report.get("actionablePlans", []),
            *report.get("actionableZones", []),
            *(
                plan
                for direction in report.get("valueDirections", [])
                for plan in direction.get("plans", [])
            ),
            *(report.get("quantitativeScan") or {}).get("items", []),
        ]
        for item in targets.values():
            if item["plan"] is not None or "HELD" not in item["relationship"]:
                continue
            for candidate in plans:
                plan = _comparable_plan(candidate, item, now)
                if plan is not None:
                    item.update(
                        plan=plan,
                        planOrigin="DAILY_REPORT",
                        reportRunId=(daily or {}).get("reportRunId"),
                        subjectKind=plan["subjectKind"],
                        subjectId=plan["subjectId"],
                    )
                    break
    return list(targets.values())


async def confirmed_quotes(conn, targets, now, *, calendar=None):
    from backend.investment.discovery import _cached_histories
    from backend.investment.live_fund_plans import refresh_report_fund_plans
    from backend.investment.theme_graph import nav_plan

    if not targets:
        return {}
    now = _stamp(now)
    calendar = await _calendar(conn, now) if calendar is None else calendar
    histories = await _cached_histories(
        conn, sorted({item["fundCode"] for item in targets}), now
    )
    output, supplied = {}, []
    for target in targets:
        history = deepcopy(histories.get(target["fundCode"]))
        if not isinstance(history, dict):
            continue
        overseas = (
            history.get("overseasCalendarRequired") is True
            or (target.get("plan") or {}).get("priceTimeBasis")
            == "LATEST_DISCLOSED_NAV"
        )
        history["calendarProof"] = {
            "market": "SSE",
            "checkedThrough": now.date().isoformat(),
            "overseasCalendarRequired": overseas,
            "days": [
                {
                    "date": str(row["calendar_date"]),
                    "isTradingDay": row["is_trading_day"],
                }
                for row in calendar
            ],
        }
        supplied.append(history)
        current = nav_plan(
            history,
            now=now,
            subject_kind=target["subjectKind"],
            subject_id=target["subjectId"],
            label=target["label"],
        )
        if not current:
            continue
        output[target["fundCode"]] = {
            "fundCode": target["fundCode"],
            "subjectKind": target["subjectKind"],
            "subjectId": target["subjectId"],
            "value": current.get("currentPrice"),
            "asOf": current.get("asOf"),
            "retrievedAt": current.get("retrievedAt"),
            "sourceUri": current.get("sourceUri"),
            "priceBasis": "UNIT_NAV",
            "unit": current["unit"],
            "quality": current.get("historyQuality"),
            "usable": current.get("historyQuality") == "READY",
            "rsi14": current.get("indicators", {}).get("rsi14"),
            "rsiAsOf": current.get("asOf"),
            "actionableZones": current,
            "dailyChangePct": current.get("currentChangePct"),
        }
    # Read the same local chart/research history selection as the visible
    # actionableZones. A newer chart must not leave reminders on older NAV.
    refreshed = await refresh_report_fund_plans(
        conn,
        {
            "actionablePlans": [
                {
                    "subjectKind": target["subjectKind"],
                    "subjectId": target["subjectId"],
                    "fundCode": target["fundCode"],
                    "label": target["label"],
                    "market": "FUND_CN",
                    "priceBasis": "UNIT_NAV",
                    "unit": "CNY/份",
                    "tracks": [],
                }
                for target in targets
            ]
        },
        now,
        histories=supplied,
    )
    for current in refreshed["actionablePlans"]:
        code = current["fundCode"]
        if current.get("historyQuality") != "READY":
            # An incomplete chart never promotes an old comparison to READY.
            if code in output:
                output[code]["actionableZones"] = current
            continue
        output[code] = {
            "fundCode": code,
            "subjectKind": current["subjectKind"],
            "subjectId": current["subjectId"],
            "value": current.get("currentPrice"),
            "asOf": current.get("asOf"),
            "retrievedAt": current.get("retrievedAt"),
            "sourceUri": current.get("sourceUri"),
            "priceBasis": "UNIT_NAV",
            "unit": current["unit"],
            "quality": "READY",
            "usable": True,
            "rsi14": current.get("indicators", {}).get("rsi14"),
            "rsiAsOf": current.get("asOf"),
            "actionableZones": current,
            "dailyChangePct": current.get("currentChangePct"),
        }
    return output


def _saved_estimate(runs, target, now):
    selected = []
    for row in runs:
        finished = _stamp(row.get("finished_at"))
        if (
            finished is None
            or finished > now
            or _day(row.get("run_date")) != now.date()
        ):
            continue
        for item in _object(row.get("result")).get("riskEstimates", []):
            if not isinstance(item, dict) or item.get("fundCode") != target["fundCode"]:
                continue
            if any(
                str(item.get(key)) != str(target.get(key))
                for key in ("trackId", "versionId", "reportRunId", "planOrigin")
            ):
                continue
            if item.get("plan") != target.get("plan"):
                continue
            selected.append(
                {**item, "retrievedAt": finished.isoformat(), "usable": True}
            )
    return max(
        selected,
        key=lambda row: _stamp(row.get("asOf")) or now - timedelta(days=100),
        default=None,
    )


async def sell_risk_alerts(conn, tenant_id, user_id, now, *, daily=None):
    from backend.investment.personal_funds import _persisted_estimates

    now = _stamp(now)
    calendar = await _calendar(conn, now)
    targets = await load_risk_targets(
        conn, tenant_id, user_id, now, daily=daily, calendar=calendar
    )
    quotes = await confirmed_quotes(conn, targets, now, calendar=calendar)
    runs = (
        await conn.fetch(
            """SELECT run_date,finished_at,result FROM research.alpha_opportunity_runs
        WHERE tenant_id=$1 AND user_id=$2 AND slot='FUND_1430' AND status IN ('SENT','NO_ALERT','COMPLETED')
          AND run_date=($3 AT TIME ZONE 'Asia/Shanghai')::date AND claimed_at<=$3 AND finished_at<=$3 ORDER BY finished_at DESC""",
            tenant_id,
            user_id,
            now,
        )
        if targets
        else []
    )
    items = [
        alert
        for target in targets
        if (
            alert := evaluate_sell_risk(
                target,
                confirmed=quotes.get(target["fundCode"]),
                estimate=_saved_estimate(runs, target, now),
                now=now,
            )
        )
    ]
    items.sort(key=lambda row: (_STATES[row["state"]], row["fundCode"]))
    public_estimates = await _persisted_estimates(
        conn, sorted({row["fundCode"] for row in targets}), now
    )
    estimates = {}
    for target in targets:
        code = target["fundCode"]
        saved = _saved_estimate(runs, target, now)
        public = public_estimates.get(code)
        if public:
            public = {
                **public,
                "subjectKind": target["subjectKind"],
                "subjectId": target["subjectId"],
            }
        estimates[code] = max(
            (
                quote
                for quote in (saved, public)
                if _valid_quote(quote, target, now, estimate=True)
                or _valid_quote(quote, target, now, estimate=True, intraday=True)
            ),
            key=lambda quote: _stamp(quote["asOf"]),
            default=None,
        )
    evaluated = [
        reminder
        for target in targets
        if (
            reminder := evaluate_action_reminder(
                target,
                confirmed=quotes.get(target["fundCode"]),
                estimate=estimates.get(target["fundCode"]),
                now=now,
            )
        )
        is not None
    ]
    reminders = [row for row in evaluated if row["state"] != "NORMAL"]
    reminders.sort(key=lambda row: (_ACTION_STATES[row["state"]], row["fundCode"]))
    normal = [row for row in evaluated if row["state"] == "NORMAL"]
    held_count = sum("HELD" in row["relationship"] for row in targets)
    evaluated_held = sum("HELD" in row["relationship"] for row in evaluated)
    return {
        "asOf": now.isoformat(),
        "items": items,
        "limitations": [],
        "actionReminders": reminders,
        "normalHoldings": normal,
        "summary": {
            "heldCount": held_count,
            "evaluatedCount": evaluated_held,
            "alertCount": sum("HELD" in row["relationship"] for row in reminders),
            "normalCount": len(normal),
            "unavailableCount": held_count - evaluated_held,
        },
    }
