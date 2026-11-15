"""Live projection for completed A-share trading dates only."""

from datetime import timedelta
from decimal import Decimal
import json
import logging

from asyncpg import PostgresError

from backend.integrations.china_market_trends import (
    TURNOVER_CLOSE,
    TURNOVER_METHOD,
    TURNOVER_URI,
)
from backend.macro.market_repository import _day, _number, _stamp

logger = logging.getLogger("wealthsail.china_market_dashboard")


async def _optional(conn, loader, now):
    try:
        # A missing relation or grant must roll back only this optional read.
        async with conn.transaction():
            return await loader(conn, now)
    except PostgresError as exc:
        logger.warning("china_market_dashboard_unavailable sqlstate=%s", exc.sqlstate)
        return None


async def add_live_dashboard_series(conn, series, now):
    result = list(series)
    csi = next((row for row in result if row.get("code") == "CN_CSI300"), None)
    if not csi or csi.get("state") != "READY":
        local = await _optional(conn, read_csi300_fallback, now)
        if local and (not csi or (local["asOf"] or "") >= (csi.get("asOf") or "")):
            result = [row for row in result if row.get("code") != "CN_CSI300"] + [local]
    if turnover := await _optional(conn, read_turnover, now):
        result.append(turnover)
    return result


def dashboard_series(code, label, unit, points, source_name, now, *, method):
    latest = points[-1] if points else {}
    previous = points[-2]["value"] if len(points) >= 2 else None
    value = latest.get("value")
    day = _day(latest.get("date"))
    return {
        "code": code,
        "label": label,
        "frequency": "DAILY",
        "unit": unit,
        "value": value,
        "asOf": latest.get("date"),
        "state": "NOT_COLLECTED"
        if day is None
        else "STALE"
        if now.date() - day > timedelta(days=5)
        else "READY",
        "sourceUri": latest.get("sourceUri"),
        "sourceName": source_name,
        "publishedAt": None,
        "retrievedAt": latest.get("retrievedAt"),
        "availableAt": latest.get("availableAt"),
        "timeBasis": "RETRIEVAL_ONLY",
        "methodVersion": method,
        "history": points,
        "changes": {
            "previousValue": previous,
            "periodChange": value - previous if previous is not None else None,
            "change5Samples": None,
            "change20Samples": None,
            "changeUnit": unit,
            "median": None,
            "percentile": None,
            "sampleCount": len(points),
        },
    }


async def read_turnover(conn, now):
    rows = await conn.fetch(
        """SELECT s.trade_date,s.source_as_of,s.retrieved_at,s.turnover
        FROM market.market_sentiment s
        JOIN reference.trading_calendars day ON day.market='SSE'
          AND day.calendar_date=s.trade_date AND day.is_trading_day
        WHERE s.method_version=$1 AND s.source_name='AkShare-OpenSource'
          AND s.quality_status='FRESH' AND s.turnover>0
          AND s.trade_date BETWEEN $2 AND ($3::timestamptz AT TIME ZONE 'Asia/Shanghai')::date
          AND s.retrieved_at<=$3 AND s.source_as_of<=s.retrieved_at
          AND (s.source_as_of AT TIME ZONE 'Asia/Shanghai')::date=s.trade_date
          AND (s.source_as_of AT TIME ZONE 'Asia/Shanghai')::time >= $4::time
        ORDER BY s.trade_date""",
        TURNOVER_METHOD,
        now.date() - timedelta(days=740),
        now,
        TURNOVER_CLOSE,
    )
    points = []
    for row in rows:
        day, earned, retrieved, amount = (
            _day(row.get("trade_date")),
            _stamp(row.get("source_as_of")),
            _stamp(row.get("retrieved_at")),
            _number(row.get("turnover")),
        )
        if (
            day is not None
            and earned
            and retrieved
            and earned <= retrieved <= now
            and amount is not None
            and amount > 0
        ):
            points.append(
                {
                    "date": day.isoformat(),
                    "value": amount,
                    "state": "OBSERVED",
                    "sourceUri": TURNOVER_URI,
                    "sourceName": "东方财富沪深京 A 股已观测成交额",
                    "retrievedAt": retrieved.isoformat(),
                    "availableAt": retrieved.isoformat(),
                    "definitionVersion": TURNOVER_METHOD,
                    "unit": "亿元",
                }
            )
    return dashboard_series(
        "CN_A_TURNOVER",
        "沪深京 A 股成交额",
        "亿元",
        points,
        "东方财富沪深京 A 股已观测成交额",
        now,
        method=TURNOVER_METHOD,
    )


def csi300_fallback(result, now):
    """Reuse a local official CSI history when the worker quote is unavailable."""
    source = "https://www.csindex.com.cn/zh-CN/indices/index-detail/000300"
    retrieved = _stamp(result.get("retrievedAt"))
    available = _stamp(result.get("availableAt")) or retrieved
    if (
        not retrieved
        or not available
        or max(retrieved, available) > now
        or result.get("source") != source
        or result.get("trendQuality") != "READY"
    ):
        return None
    points = []
    cutoff = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
    for raw in result.get("points", []):
        if not isinstance(raw, dict):
            continue
        day, price = _day(raw.get("date")), _number(raw.get("close"))
        if (
            day is not None
            and cutoff - timedelta(days=740) <= day <= cutoff
            and price is not None
            and price > Decimal(0)
        ):
            points.append(
                {
                    "date": day.isoformat(),
                    "value": price,
                    "state": "OBSERVED",
                    "sourceUri": source,
                    "retrievedAt": retrieved.isoformat(),
                    "availableAt": available.isoformat(),
                    "unit": "点",
                    "definitionVersion": "PRICE_CLOSE",
                }
            )
    if not points or [p["date"] for p in points] != sorted({p["date"] for p in points}):
        return None
    return dashboard_series(
        "CN_CSI300",
        "沪深300",
        "点",
        points,
        "中证指数有限公司",
        now,
        method="csindex-retrieval-v1",
    )


async def read_csi300_fallback(conn, now):
    rows = await conn.fetch(
        """SELECT result,points,source_uri,retrieved_at,available_at
        FROM market.investment_analysis_snapshots
        WHERE subject_kind='index' AND subject_key='hs300'
          AND source_uri='https://www.csindex.com.cn/zh-CN/indices/index-detail/000300'
          AND retrieved_at<=$1 AND available_at<=$1
        ORDER BY retrieved_at DESC,id DESC LIMIT 1""",
        now,
    )
    if not rows:
        return None
    row = rows[0]
    try:
        result = (
            json.loads(row["result"])
            if isinstance(row["result"], str)
            else row["result"]
        )
        points = (
            json.loads(row["points"])
            if isinstance(row["points"], str)
            else row["points"]
        )
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(result, dict) or not isinstance(points, list):
        return None
    return csi300_fallback(
        {
            **result,
            "points": points,
            "source": row["source_uri"],
            "retrievedAt": row["retrieved_at"],
            "availableAt": row["available_at"],
        },
        now,
    )
