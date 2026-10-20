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
    _UNITS,
)

_STATES = {
    "STOP_LOSS": 0,
    "TAKE_PROFIT_T2": 1,
    "TAKE_PROFIT_T1": 2,
    "RSI_OVERBOUGHT": 3,
}


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


def _valid_quote(quote, target, now, *, estimate=False):
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
    from backend.investment.theme_graph import nav_plan

    if not targets:
        return {}
    now = _stamp(now)
    calendar = await _calendar(conn, now) if calendar is None else calendar
    histories = await _cached_histories(
        conn, sorted({item["fundCode"] for item in targets}), now
    )
    output = {}
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
    return {"asOf": now.isoformat(), "items": items, "limitations": []}
