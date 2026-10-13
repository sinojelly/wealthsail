"""Worker-only research history retrieval and immutable analysis materialization."""

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta
from uuid import UUID

from backend.common.database import system_connection
from backend.errors import NotFoundError, DataUnavailableError
from backend.indicators.technical import VERSION, analyze
from backend.market.research_history import (
    calendar_market, fund_history, normalize, quality, published_day,
)
from backend.dependencies import now_shanghai


async def load_subject(conn, kind, subject_id):
    if kind == "index":
        row = await conn.fetchrow(
            "SELECT code,market,provider_symbol,source_uri,verified_at,publish_hour FROM reference.research_indices WHERE id=$1",
            subject_id,
        )
    else:
        try:
            instrument_id = UUID(subject_id)
        except ValueError as exc:
            raise NotFoundError("研究标的不存在") from exc
        row = await conn.fetchrow(
            """SELECT c.fund_code AS code,c.market,c.trade_form,i.metadata
          FROM reference.research_fund_catalog c JOIN reference.instruments i ON i.id=c.instrument_id
          WHERE c.instrument_id=$1""",
            instrument_id,
        )
    if not row:
        raise NotFoundError("研究标的不存在")
    return dict(row)


async def fetch_history(subject, kind, start, end):
    import akshare as ak

    code = subject["code"]
    metadata = subject.get("metadata", {})
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    config = metadata.get("research", {})
    if kind == "index":
        if subject["market"] not in ("SSE", "SZSE") or not subject.get(
            "provider_symbol"
        ):
            raise DataUnavailableError("指数行情来源尚未核验")
        frame = await asyncio.to_thread(
            ak.stock_zh_index_daily_em,
            symbol=subject["provider_symbol"],
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        return normalize(frame.to_dict("records")), "INDEX_PRICE", subject["source_uri"]
    if subject["trade_form"] == "ETF":
        frame = await asyncio.to_thread(
            ak.fund_etf_hist_em,
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="hfq",
        )
        return (
            normalize(
                frame.rename(
                    columns={
                        "日期": "date",
                        "开盘": "open",
                        "最高": "high",
                        "最低": "low",
                        "收盘": "close",
                        "成交量": "volume",
                    }
                ).to_dict("records")
            ),
            "ETF_HFQ",
            config.get("sourceUri") or "AKSHARE/fund_etf_hist_em",
        )
    frame = await asyncio.to_thread(
        ak.fund_open_fund_info_em,
        symbol=code,
        indicator="单位净值走势",
        period="成立来",
    )
    points, series_kind = fund_history(
        frame.to_dict("records"), return_verified=config.get("returnSeriesVerified") is True,
        start=start, end=end,
    )
    return points, series_kind, config.get("sourceUri") or "AKSHARE/fund_open_fund_info_em"


async def refresh_subject(pool, settings, kind, subject_id, as_of, *, now=None):
    async with system_connection(pool) as conn:
        subject = await load_subject(conn, kind, subject_id)
    rows, series_kind, source = await asyncio.wait_for(
        fetch_history(subject, kind, as_of - timedelta(days=365 * 5), as_of), timeout=90
    )
    rows = [p for p in rows if (as_of - timedelta(days=365 * 5)).isoformat() <= p["date"] <= as_of.isoformat()]
    if not rows:
        raise DataUnavailableError("来源没有有效历史数据")
    metadata = subject.get("metadata", {})
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    config = metadata.get("research", {})
    verified = (
        bool(subject.get("verified_at"))
        if kind == "index"
        else config.get("sourceVerified") is True and bool(config.get("sourceUri"))
    )
    publish_hour = (
        subject.get("publish_hour") if kind == "index" else config.get("publishHour")
    )
    verified = verified and type(publish_hour) is int and 0 <= publish_hour <= 23
    eligible_day = published_day(as_of, publish_hour, now or now_shanghai())
    if eligible_day is not None:
        rows = [p for p in rows if p["date"] <= eligible_day.isoformat()]
        if not rows:
            raise DataUnavailableError("已发布窗口内没有有效历史数据")
    async with system_connection(pool) as conn:
        calendar = await conn.fetch(
            """SELECT calendar_date FROM reference.trading_calendars
          WHERE market=$1 AND is_trading_day AND calendar_date BETWEEN $2 AND $3 ORDER BY calendar_date""",
            calendar_market(subject["market"]),
            date.fromisoformat(rows[0]["date"]),
            eligible_day,
        )
    days = {str(r["calendar_date"]) for r in calendar}
    latest = date.fromisoformat(rows[-1]["date"])
    # Do not infer source publication schedules or manufacturing OHLC for NAV.
    expected = date.fromisoformat(max(days)) if days else None
    state = quality(
        latest=latest,
        expected=expected,
        verified=verified,
        missing=bool(days - {p["date"] for p in rows}),
    )
    if any(p.get("returnQuality") == "CONFLICT" for p in rows):
        state = "CONFLICT"
    result = analyze(rows, quality=state)
    result["market"] = subject["market"]
    result["publicationHour"] = publish_hour
    result["calendarMarket"] = calendar_market(subject["market"])
    result["dataQuality"] = state
    result["returnSeriesVerified"] = (
        config.get("returnSeriesVerified") is True if kind == "fund" else False
    )
    if series_kind == "ACCUMULATED_NAV":
        result.update(score=None, state="UNSUPPORTED", indicators={}, reasons=[])
    raw = json.dumps(
        {
            "points": rows,
            "source": source,
            "seriesKind": series_kind,
            "quality": state,
            "publicationHour": publish_hour,
            "calendarMarket": calendar_market(subject["market"]),
            "sourceVerified": verified,
            "returnSeriesVerified": result["returnSeriesVerified"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    input_hash = hashlib.sha256(raw.encode()).hexdigest()
    async with system_connection(pool) as conn:
        await conn.execute(
            """
          INSERT INTO market.investment_analysis_snapshots(subject_kind,subject_key,input_hash,source_uri,
            series_kind,as_of,available_at,method_version,result,points)
          VALUES($1,$2,$3,$4,$5,$6,now(),$7,$8::jsonb,$9::jsonb)
          ON CONFLICT(subject_kind,subject_key,input_hash,method_version) DO NOTHING
        """,
            kind,
            subject_id,
            input_hash,
            source,
            series_kind,
            latest,
            VERSION,
            json.dumps(result),
            json.dumps(rows),
        )


async def sync_research(pool, settings, now: datetime):
    import logging
    from backend.jobs.investment_macro import sync_macro

    logger = logging.getLogger("wealthsail.investment_research")
    try:
        await asyncio.wait_for(sync_macro(pool, now), timeout=90)
    except Exception:  # noqa: BLE001 -- provider failure must not block fund research
        logger.exception("research_macro_sync_failed")
    async with system_connection(pool) as conn:
        indices = await conn.fetch(
            "SELECT id FROM reference.research_indices WHERE verified_at IS NOT NULL AND provider_symbol IS NOT NULL"
        )
        funds = await conn.fetch(
            """SELECT DISTINCT c.instrument_id
              FROM reference.research_fund_catalog c
              JOIN reference.instruments i ON i.id=c.instrument_id
              WHERE (
                i.metadata->'research'->'sourceVerified'='true'::jsonb
                AND length(COALESCE(i.metadata->'research'->>'sourceUri',''))>0
                AND i.metadata->'research'->'publishHour' IS NOT NULL
              ) OR EXISTS (
                SELECT 1 FROM reference.fund_index_links f
                WHERE f.instrument_id=c.instrument_id AND f.verified_at IS NOT NULL
                  AND f.effective_from<=CURRENT_DATE
                  AND (f.effective_to IS NULL OR f.effective_to>=CURRENT_DATE)
              ) ORDER BY c.instrument_id"""
        )
    succeeded = failed = 0
    for kind, key in [("index", str(r["id"])) for r in indices] + [
        ("fund", str(r["instrument_id"])) for r in funds
    ]:
        try:
            await refresh_subject(pool, settings, kind, key, now.date(), now=now)
            succeeded += 1
        except Exception:  # noqa: BLE001 -- isolate provider failure per subject
            logger.exception("research_subject_refresh_failed kind=%s id=%s", kind, key)
            failed += 1
        await asyncio.sleep(1)
    return {"succeeded": succeeded, "failed": failed}
