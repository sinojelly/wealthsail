"""Authenticated disclosed unit-NAV chart cache and durable on-demand requests."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
from zoneinfo import ZoneInfo

from backend.errors import ValidationError_
from backend.funds.nav_window import two_year_nav_points, two_year_nav_start
from backend.integrations.nav_history_proof import nav_points_digest
from backend.macro.research_debug_repository import active_operator

SHANGHAI = ZoneInfo("Asia/Shanghai")
PERIODS = {"6m", "1y", "2y", "all"}
MAX_POINTS = 20000


def local(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("NAV history requires an aware clock")
    return value.astimezone(SHANGHAI)


def stamp(value):
    try:
        return local(datetime.fromisoformat(value))
    except (ValueError, TypeError):
        return None


def obj(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def recently_checked(history, now):
    """Source recheck age is independent of delayed overseas disclosure dates."""
    retrieved = stamp(history.get("retrievedAt"))
    return retrieved is not None and timedelta(0) <= local(
        now
    ) - retrieved <= timedelta(hours=36)


def period_start(period, now):
    if period not in PERIODS:
        raise ValueError("unsupported NAV chart period")
    today = local(now).date()
    if period == "all":
        return None
    months = {"6m": 6, "1y": 12, "2y": 24}[period]
    index = today.year * 12 + today.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(today.day, monthrange(year, month)[1]))


def valid_points(history, code, now):
    if (
        not isinstance(history, dict)
        or history.get("fundCode") != code
        or history.get("priceBasis") != "UNIT_NAV"
    ):
        return []
    retrieved, available = (
        stamp(history.get("retrievedAt")),
        stamp(history.get("availableAt")),
    )
    allowed = {
        f"https://fund.eastmoney.com/{code}.html",
        f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
        "https://api.fund.eastmoney.com/f10/lsjz",
    }
    rows = history.get("points")
    if (
        retrieved is None
        or available is None
        or retrieved > now
        or available > now
        or history.get("sourceUri") not in allowed
        or not isinstance(rows, list)
        or not 0 < len(rows) <= MAX_POINTS
    ):
        return []
    by_day = {}
    try:
        for point in rows:
            day = date.fromisoformat(point["date"])
            raw = point["close"]
            if isinstance(raw, bool):
                return []
            value = Decimal(str(raw))
            if (
                not date(1900, 1, 1) <= day <= now.date()
                or not value.is_finite()
                or value <= 0
                or day in by_day
                and by_day[day] != value
            ):
                return []
            by_day[day] = value
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return []
    return [
        {"date": day.isoformat(), "close": str(value)}
        for day, value in sorted(by_day.items())
    ]


def full_history_proven(history, points, now):
    proof = history.get("historyProof") or {}
    if not points or history.get("fullHistory") is not True:
        return False
    try:
        return (
            proof.get("methodVersion") == "akshare-unit-nav-full-v1"
            and proof.get("complete") is True
            and proof.get("fundCode") == history["fundCode"]
            and proof.get("sourceUri") == history["sourceUri"]
            and proof.get("queryStart") == "1900-01-01"
            and date.fromisoformat(proof["queryEnd"]) <= now.date()
            and proof["queryEnd"] >= points[-1]["date"]
            and type(proof.get("pointCount")) is int
            and proof["pointCount"] == len(points)
            and type(proof.get("receivedRowCount")) is int
            and proof["receivedRowCount"] >= len(points)
            and proof.get("firstDate") == points[0]["date"]
            and proof.get("lastDate") == points[-1]["date"]
            and proof.get("pointsDigest") == nav_points_digest(points)
        )
    except (KeyError, TypeError, ValueError):
        return False


def interval_proven(history, points, start):
    if start is None or not points:
        return False
    if points[0]["date"] <= start.isoformat():
        return True
    proof = history.get("providerHistoryProof") or {}
    try:
        return (
            proof.get("complete") is True
            and proof.get("fundCode") == history.get("fundCode")
            and proof.get("methodVersion")
            in {"eastmoney-nav-history-v1", "akshare-unit-nav-history-v1"}
            and date.fromisoformat(proof["queryStart"]) <= start
            and proof.get("uniquePointCount") == proof.get("pointCount") == len(points)
            and proof.get("firstDate") == points[0]["date"]
            and proof.get("lastDate") == points[-1]["date"]
            and proof.get("pointsDigest") == nav_points_digest(points)
        )
    except (KeyError, TypeError, ValueError):
        return False


def history_view(code, name, period, history, now):
    now = local(now)
    start = period_start(period, now)
    points = valid_points(history, code, now)
    if period == "2y" and points:
        start = two_year_nav_start(date.fromisoformat(points[-1]["date"]))
    full = full_history_proven(history or {}, points, now)
    checked = recently_checked(history or {}, now)
    covered = checked and (full or interval_proven(history or {}, points, start))
    selected = (
        two_year_nav_points(points)
        if period == "2y"
        else [
            point
            for point in points
            if start is None or point["date"] >= start.isoformat()
        ]
    )
    values = [Decimal(point["close"]) for point in selected]
    summary = {
        "minimumNav": None,
        "maximumNav": None,
        "currentNav": None,
        "percentilePct": None,
        "percentileMethod": "EMPIRICAL_MIDRANK_V1",
    }
    if values:
        current = values[-1]
        summary.update(
            minimumNav=str(min(values)),
            maximumNav=str(max(values)),
            currentNav=str(current),
            percentilePct=round(
                float(
                    (
                        sum(value < current for value in values)
                        + Decimal("0.5") * sum(value == current for value in values)
                    )
                    / len(values)
                    * 100
                ),
                4,
            ),
        )
    result = {
        "methodVersion": "fund-unit-nav-trend-v1",
        "fundCode": code,
        "fundName": name,
        "period": period,
        "priceBasis": "UNIT_NAV",
        "unit": "CNY/份",
        "state": "READY"
        if selected and covered
        else "PARTIAL"
        if selected
        else "NOT_COLLECTED",
        "asOf": selected[-1]["date"] if selected else None,
        "retrievedAt": history.get("retrievedAt") if points else None,
        "sourceUri": history.get("sourceUri") if points else None,
        "points": [
            {"date": point["date"], "unitNav": point["close"]} for point in selected
        ],
        "coverage": {
            "startDate": points[0]["date"] if points else None,
            "endDate": points[-1]["date"] if points else None,
            "fullHistory": full,
            "requestedStartDate": start.isoformat() if start else None,
            "sampleCount": len(selected),
        },
        "summary": summary,
        "refresh": {"state": "IDLE", "runId": None, "pollAfterMs": 2000},
    }

    if points and not checked:
        result["coverage"]["reason"] = "SOURCE_RECHECK_DUE"
    return result


async def _refresh_state(conn, code, tenant_id, user_id, now):
    active = await conn.fetchrow(
        """SELECT c.run_id FROM market.macro_evidence_attempts c WHERE c.job_key=$1 AND c.phase='CLAIMED' AND c.lease_until>$2
        AND NOT EXISTS(SELECT 1 FROM market.macro_evidence_attempts f WHERE f.run_id=c.run_id AND f.phase IN ('FINISHED','CANCELLED') AND f.recorded_at<=$2) LIMIT 1""",
        f"FUND_NAV_CHART:{code}",
        now,
    )
    if active:
        return {"state": "RUNNING", "runId": str(active["run_id"]), "pollAfterMs": 2000}
    last = await conn.fetchrow(
        """SELECT run_id,result FROM market.macro_evidence_attempts WHERE job_key=$1 AND phase IN ('FINISHED','CANCELLED') AND recorded_at BETWEEN $2::timestamptz-interval '1 hour' AND $2::timestamptz ORDER BY recorded_at DESC,id DESC LIMIT 1""",
        f"FUND_NAV_CHART:{code}",
        now,
    )
    if last and obj(last.get("result")).get("status") != "SUCCESS":
        return {
            "state": "FAILED",
            "runId": str(last["run_id"]),
            "pollAfterMs": 2000,
            "reason": "SOURCE_COOLDOWN",
        }
    if not await active_operator(conn, tenant_id, user_id):
        return {
            "state": "IDLE",
            "runId": None,
            "pollAfterMs": 2000,
            "reason": "OPERATOR_REQUIRED",
        }
    # Lease and payload columns belong to the Worker. The narrow helper checks
    # the active operator again and only resets this fund's chart-fetch task.
    row = await conn.fetchrow(
        "SELECT id,status,reason FROM workflow.enqueue_fund_nav_chart_fetch($1,$2,$3)",
        tenant_id,
        user_id,
        code,
    )
    return {
        "state": "FAILED"
        if row.get("reason") == "SOURCE_COOLDOWN"
        else "RUNNING"
        if row["status"] == "RUNNING"
        else "QUEUED",
        "runId": str(row["id"]),
        "pollAfterMs": 2000,
        **({"reason": row["reason"]} if row.get("reason") else {}),
    }


def select_history_view(code, name, period, histories, now):
    """Share chart source selection with live plan repricing without HTTP I/O."""
    candidates = [
        history_view(code, name, period, obj(history), now) for history in histories
    ]
    result = max(
        candidates,
        key=lambda item: (
            item["state"] == "READY",
            item["coverage"]["fullHistory"] if period == "all" else False,
            item.get("asOf") or "",
            item["coverage"]["sampleCount"],
            item.get("retrievedAt") or "",
        ),
        default=history_view(code, name, period, {}, now),
    )
    latest_known = max((item.get("asOf") or "" for item in candidates), default="")
    if result.get("asOf") and result["asOf"] < latest_known:
        result["state"] = "PARTIAL"
        result["coverage"].update(
            fullHistory=False,
            reason="NEWER_CONFIRMED_NAV_AVAILABLE",
            latestKnownNavDate=latest_known,
        )
    return result


async def get_nav_history(conn, fund_code, period="2y", *, tenant_id, user_id, now):
    now = local(now)
    if (
        not isinstance(fund_code, str)
        or not re.fullmatch(r"[0-9]{6}", fund_code)
        or period not in PERIODS
    ):
        raise ValidationError_("基金代码或走势图周期无效")
    identities = await conn.fetch(
        """SELECT fund_name,currency FROM reference.research_fund_catalog WHERE fund_code=$1 AND market='FUND_CN' AND trade_form IN ('FUND','LOF')""",
        fund_code,
    )
    if (
        len(identities) != 1
        or identities[0]["currency"] != "CNY"
        or re.search(r"美元|港币|港元|外币|现汇|现钞", identities[0]["fund_name"])
    ):
        raise ValidationError_("基金身份不唯一或不是已核验人民币场外基金")
    rows = await conn.fetch(
        """SELECT payload FROM market.fund_nav_chart_histories WHERE fund_code=$1 AND retrieved_at<=$2 AND available_at<=$2 ORDER BY retrieved_at DESC,id DESC LIMIT 8""",
        fund_code,
        now,
    )
    rows += await conn.fetch(
        """SELECT payload FROM market.fund_research_nav_histories WHERE fund_code=$1 AND retrieved_at<=$2 AND available_at<=$2 ORDER BY retrieved_at DESC,id DESC LIMIT 8""",
        fund_code,
        now,
    )
    result = select_history_view(
        fund_code,
        identities[0]["fund_name"],
        period,
        [row["payload"] for row in rows],
        now,
    )
    if not result["points"] and result["coverage"]["fullHistory"]:
        result["state"] = "UNAVAILABLE"
        if result["coverage"].get("reason") != "SOURCE_RECHECK_DUE":
            result["refresh"]["reason"] = "NO_NAV_FOR_PERIOD"
            return result
    if result["state"] != "READY":
        result["refresh"] = await _refresh_state(
            conn, fund_code, tenant_id, user_id, now
        )
    return result
