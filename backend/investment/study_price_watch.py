"""Actor-only read model over frozen plans and already persisted market facts."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import json
from uuid import UUID
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
_UNITS = {"CNY", "元", "元/份", "CNY/unit", "CNY/份", "CNY/share"}


def _source_uri(value):
    try:
        uri = urlsplit(value)
        return (
            value
            if uri.scheme == "https"
            and uri.hostname
            and not uri.username
            and not uri.password
            and uri.port in {None, 443}
            else None
        )
    except (ValueError, TypeError):
        return None


def _current_session(calendar, now):
    cutoff = now.date() if now.time() >= time(18) else now.date() - timedelta(days=1)
    days = {row["calendar_date"]: row["is_trading_day"] for row in calendar}
    needed = {cutoff - timedelta(days=i) for i in range(11)}
    if not needed.issubset(days) or any(type(days[day]) is not bool for day in needed):
        return None
    return max((day for day in needed if days[day]), default=None)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def _stamp(value):
    try:
        value = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
        return value.astimezone(SHANGHAI) if value.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _day(value):
    if isinstance(value, datetime):
        stamp = _stamp(value)
        return stamp.date() if stamp else None
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _object(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _track(frozen):
    if not isinstance(frozen, dict):
        return None
    tracks = [
        row
        for row in frozen.get("tracks", [])
        if isinstance(row, dict)
        and row.get("track") == frozen.get("selectedTrack", frozen.get("track"))
    ]
    if len(tracks) != 1 or tracks[0].get("state") not in {"CANDIDATE", "WATCH"}:
        return None
    row = tracks[0]
    values = [
        _number(row.get(key))
        for key in (
            "stopLossPrice",
            "entryZoneMin",
            "entryZoneMax",
            "takeProfitT1",
            "takeProfitT2",
        )
    ]
    if any(value is None for value in values):
        return None
    stop, low, high, first, second = values
    return row if stop < low <= high < first < second else None


def _usable(quote, frozen, now, *, estimate=False):
    if not isinstance(quote, dict) or quote.get("usable") is not True:
        return False
    retrieved, day = _stamp(quote.get("retrievedAt")), _day(quote.get("asOf"))
    if (
        frozen.get("priceBasis") != "UNIT_NAV"
        or frozen.get("unit") not in _UNITS
        or quote.get("priceBasis") != "UNIT_NAV"
        or quote.get("unit") not in _UNITS
        or quote.get("subjectKind") != frozen.get("subjectKind")
        or str(quote.get("subjectId")) != str(frozen.get("subjectId"))
        or quote.get("fundCode") != frozen.get("fundCode")
        or _number(quote.get("value")) is None
        or day is None
        or day > now.date()
        or _day(frozen.get("asOf")) is None
        or day < _day(frozen.get("asOf"))
        or retrieved is None
        or retrieved > now
        or day > retrieved.date()
        or not _source_uri(quote.get("sourceUri"))
    ):
        return False
    if not estimate:
        return quote.get("quality") == "READY" and (now.date() - day).days <= 10
    stamp, checked = _stamp(quote.get("asOf")), _stamp(quote.get("checkedAt"))
    return (
        quote.get("quality") == "ESTIMATED"
        and stamp is not None
        and checked is not None
        and stamp <= checked <= now
        and stamp.date() == now.date()
        and time(14) <= stamp.time() < time(15)
        and now.time() < time(15)
        and now - stamp <= timedelta(minutes=30)
    )


def price_watch(frozen, *, confirmed=None, estimate=None, holding=None, now):
    """A reference-line hit never asserts a purchase, execution or realized profit."""
    now = _stamp(now)
    if now is None:
        raise ValueError("price watch requires an aware clock")
    result = {
        "frozenPlan": deepcopy(frozen),
        "confirmedNav": deepcopy(confirmed),
        "estimate1430": deepcopy(estimate),
        "holding": deepcopy(holding or {"state": "UNKNOWN"}),
        "state": "UNKNOWN",
        "basis": None,
        "asOf": None,
        "reason": "暂无可比较的同基金当前净值，保留冻结计划。",
    }
    track = _track(frozen)
    if track is None:
        result["reason"] = "未冻结完整的单一价格轨道，暂不判定到价。"
        return result
    confirmed_ok = _usable(confirmed, frozen, now)
    estimate_ok = _usable(estimate, frozen, now, estimate=True)
    if result["confirmedNav"] is not None:
        result["confirmedNav"]["usable"] = confirmed_ok
    if result["estimate1430"] is not None:
        result["estimate1430"]["usable"] = estimate_ok
    candidate = estimate if estimate_ok else confirmed if confirmed_ok else None
    if candidate is None:
        return result
    value = _number(candidate["value"])
    result.update(
        basis="ESTIMATE_1430" if estimate_ok else "CONFIRMED_NAV",
        asOf=candidate["asOf"],
    )
    result["state"] = (
        "STOP_LOSS"
        if value <= _number(track["stopLossPrice"])
        else "TAKE_PROFIT"
        if value >= _number(track["takeProfitT1"])
        else "ENTRY_ZONE"
        if _number(track["entryZoneMin"]) <= value <= _number(track["entryZoneMax"])
        else "HOLDING"
        if (holding or {}).get("state") == "CONFIRMED"
        else "OBSERVING"
    )
    result["reason"] = (
        "按14:30估算值核对冻结参考线，申赎成交净值尚未确认。"
        if estimate_ok
        else "按最新已确认披露净值核对冻结参考线。"
    )
    return result


def _confirmed(history, frozen, now, calendar):
    from backend.investment.theme_graph import nav_plan

    history = deepcopy(history)
    if history.get("fundCode") != frozen.get("fundCode"):
        return None
    history["overseasCalendarRequired"] = (
        history.get("overseasCalendarRequired") is True
        or frozen.get("priceTimeBasis") == "LATEST_DISCLOSED_NAV"
    )
    history["calendarProof"] = {
        "market": "SSE",
        "checkedThrough": now.date().isoformat(),
        "overseasCalendarRequired": history["overseasCalendarRequired"],
        "days": [
            {"date": str(row["calendar_date"]), "isTradingDay": row["is_trading_day"]}
            for row in calendar
        ],
    }
    current = nav_plan(
        history,
        now=now,
        subject_kind=frozen.get("subjectKind"),
        subject_id=frozen.get("subjectId"),
        label=frozen.get("label"),
    )
    if current is None or _number(current.get("currentPrice")) is None:
        return None
    return {
        "value": current["currentPrice"],
        "asOf": current["asOf"],
        "retrievedAt": current["retrievedAt"],
        "sourceUri": current.get("sourceUri"),
        "subjectKind": frozen["subjectKind"],
        "subjectId": frozen["subjectId"],
        "fundCode": frozen["fundCode"],
        "priceBasis": "UNIT_NAV",
        "unit": current["unit"],
        "quality": current.get("historyQuality"),
        "usable": current.get("historyQuality") == "READY",
    }


def _estimate(rows, tenant, actor, track_id, version_id, frozen, now):
    selected = []
    for row in rows:
        if (
            str(row.get("tenant_id")) != str(tenant)
            or str(row.get("user_id")) != str(actor)
            or (finished := _stamp(row.get("finished_at"))) is None
            or finished > now
        ):
            continue
        for check in _object(row.get("result")).get("checks", []):
            if (
                not isinstance(check, dict)
                or check.get("trackId") != str(track_id)
                or check.get("versionId") != str(version_id)
                or check.get("state")
                not in {"STOP_LOSS", "TAKE_PROFIT_T1", "IN_ENTRY_ZONE", "WATCH"}
                or _day(row.get("run_date")) != _day(check.get("asOf"))
            ):
                continue
            track = _track(frozen)
            if (
                track is None
                or check.get("selectedTrack") != track["track"]
                or check.get("priceBasis") != frozen.get("priceBasis")
                or check.get("unit") not in _UNITS
                or _day(check.get("planAsOf")) != _day(frozen.get("asOf"))
                or any(
                    _number(check.get(key)) != _number(track.get(key))
                    for key in (
                        "entryZoneMin",
                        "entryZoneMax",
                        "stopLossPrice",
                        "takeProfitT1",
                    )
                )
            ):
                continue
            selected.append(
                {
                    "value": check.get("price"),
                    "asOf": check.get("asOf"),
                    "checkedAt": finished.isoformat(),
                    "retrievedAt": finished.isoformat(),
                    "sourceUri": _source_uri(check.get("marketSourceUri")),
                    "unit": check.get("unit"),
                    "priceBasis": check.get("priceBasis"),
                    "quality": "ESTIMATED",
                    "usable": bool(_source_uri(check.get("marketSourceUri"))),
                    "state": check.get("state"),
                    "runDate": str(row.get("run_date")),
                    "subjectKind": frozen["subjectKind"],
                    "subjectId": frozen["subjectId"],
                    "fundCode": frozen.get("fundCode"),
                }
            )
    return max(selected, key=lambda item: _stamp(item["checkedAt"]), default=None)


async def enrich_tracks(conn, tenant, actor, items, now, *, versions=None):
    """Fixed-count bulk SELECTs under actor RLS, without any provider calls."""
    from backend.investment.discovery import _cached_histories

    now = _stamp(now)
    if now is None:
        raise ValueError("price watch requires an aware clock")
    ids = []
    for item in items:
        try:
            ids.append(UUID(str(item["id"])))
        except (KeyError, TypeError, ValueError):
            pass
    if not ids:
        return [{**item, "priceWatch": None} for item in items]
    if versions is None:
        rows = await conn.fetch(
            """SELECT DISTINCT ON(v.track_id) v.* FROM research.study_versions v
            JOIN research.study_tracks t ON t.id=v.track_id AND t.tenant_id=v.tenant_id AND t.user_id=v.user_id
            WHERE v.tenant_id=$1 AND v.user_id=$2 AND v.track_id=ANY($3::uuid[])
              AND v.created_at<=$4
            ORDER BY v.track_id,v.version DESC,v.id DESC""",
            tenant,
            actor,
            ids,
            now,
        )
        versions = {str(row["track_id"]): dict(row) for row in rows}
    frozen = {}
    for item in items:
        version = versions.get(str(item.get("id"))) or {}
        if (
            not version
            or version.get("generation", 1) > item.get("generation", 1)
            or _stamp(version.get("created_at", version.get("createdAt"))) is None
            or _stamp(version.get("created_at", version.get("createdAt"))) > now
        ):
            continue
        snapshot = _object(version.get("snapshot"))
        plan = snapshot.get("actionablePlan")
        if (
            isinstance(plan, dict)
            and plan.get("subjectKind") in {"fund", "nav"}
            and plan.get("fundCode")
        ):
            frozen[str(item["id"])] = plan
    if not frozen:
        return [{**item, "priceWatch": None} for item in items]
    histories = await _cached_histories(
        conn, sorted({plan["fundCode"] for plan in frozen.values()}), now
    )
    starts = [
        _day(point.get("date"))
        for h in histories.values()
        for point in h.get("points", [])
        if isinstance(point, dict)
    ]
    starts = [day for day in starts if day is not None and day <= now.date()]
    calendar = await conn.fetch(
        "SELECT calendar_date,is_trading_day FROM reference.trading_calendars WHERE market='SSE' AND calendar_date BETWEEN $1 AND $2 ORDER BY calendar_date",
        min(
            max(min(starts), now.date() - timedelta(days=1000)),
            now.date() - timedelta(days=12),
        )
        if starts
        else now.date() - timedelta(days=12),
        now.date(),
    )
    checks = await conn.fetch(
        """SELECT tenant_id,user_id,run_date,finished_at,result FROM research.alpha_opportunity_runs
        WHERE tenant_id=$1 AND user_id=$2 AND slot='FUND_1430'
          AND status IN ('SENT','NO_ALERT','COMPLETED') AND finished_at<=$3 AND claimed_at<=$3
          AND run_date BETWEEN ($3 AT TIME ZONE 'Asia/Shanghai')::date-7 AND ($3 AT TIME ZONE 'Asia/Shanghai')::date
        ORDER BY run_date DESC,finished_at DESC""",
        tenant,
        actor,
        now,
    )
    instrument_ids = []
    for item in items:
        try:
            instrument_ids.append(
                UUID(str((item.get("scope") or {}).get("instrumentId")))
            )
        except (TypeError, ValueError):
            pass
    positions = []
    if instrument_ids:
        positions = await conn.fetch(
            """WITH latest AS (
              SELECT DISTINCT ON(p.account_id,p.instrument_id) p.instrument_id,p.quantity,p.as_of,p.account_id,p.data_quality_status
              FROM ledger.position_snapshots p JOIN identity.accounts a ON a.id=p.account_id AND a.tenant_id=p.tenant_id AND a.status='ACTIVE'
              JOIN identity.account_members m
                ON m.tenant_id=p.tenant_id AND m.account_id=p.account_id
              WHERE p.tenant_id=$1 AND m.user_id=$2 AND m.relationship IN ('BENEFICIAL_OWNER','JOINT_OWNER')
                AND m.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
                AND (m.effective_to IS NULL OR m.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date)
                AND p.created_at<=$3 AND p.as_of<=($3 AT TIME ZONE 'Asia/Shanghai')::date
                AND p.instrument_id=ANY($4::uuid[])
              ORDER BY p.account_id,p.instrument_id,p.as_of DESC,p.created_at DESC)
            SELECT p.* FROM latest p JOIN LATERAL (
              SELECT data_quality_status,as_of,valuation_coverage_pct,unpriced_position_count FROM ledger.account_nav_snapshots n
              WHERE n.tenant_id=$1 AND n.account_id=p.account_id AND n.created_at<=$3
                AND n.as_of<=($3 AT TIME ZONE 'Asia/Shanghai')::date ORDER BY n.as_of DESC,n.created_at DESC LIMIT 1
            ) n ON n.as_of=p.as_of AND n.data_quality_status='FRESH' AND p.data_quality_status='FRESH'
              AND n.valuation_coverage_pct=100 AND n.unpriced_position_count=0""",
            tenant,
            actor,
            now,
            instrument_ids,
        )
    current_day = _current_session(calendar, now)
    result = []
    for item in items:
        key = str(item.get("id"))
        if key not in frozen:
            result.append({**item, "priceWatch": None})
            continue
        plan, version = frozen[key], versions[key]
        history = histories.get(plan["fundCode"])
        confirmed = _confirmed(history, plan, now, calendar) if history else None
        estimate = _estimate(checks, tenant, actor, key, version["id"], plan, now)
        identity = str((item.get("scope") or {}).get("instrumentId"))
        actual = [
            row
            for row in positions
            if str(row["instrument_id"]) == identity
            and current_day is not None
            and current_day <= row["as_of"] <= now.date()
            and _number(row["quantity"]) is not None
        ]
        holding = (
            {"state": "CONFIRMED", "asOf": str(max(row["as_of"] for row in actual))}
            if actual
            else {"state": "UNKNOWN"}
        )
        result.append(
            {
                **item,
                "priceWatch": price_watch(
                    plan,
                    confirmed=confirmed,
                    estimate=estimate,
                    holding=holding,
                    now=now,
                ),
            }
        )
    return result
