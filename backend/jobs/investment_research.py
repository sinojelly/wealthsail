"""Worker-only research history retrieval and immutable analysis materialization."""

import asyncio
import hashlib
import json
from datetime import date, datetime
from uuid import UUID

from backend.common.database import system_connection
from backend.errors import NotFoundError, DataUnavailableError
from backend.indicators.technical import VERSION, analyze
from backend.indicators.performance import (
    VERSION as PERFORMANCE_VERSION,
    analyze as analyze_performance,
)
from backend.indicators.value_evidence import VERSION as VALUE_VERSION, evaluate
from backend.jobs.investment_fundamentals import refresh_fundamentals
from backend.market.research_history import (
    calendar_market,
    fund_history,
    normalize,
    quality,
    published_day,
    years_before,
)
from backend.dependencies import now_shanghai
from backend.market.service import ensure_trading_calendar
from backend.market import index_policy
from backend.integrations.index_official import fetch_index_history


async def load_subject(conn, kind, subject_id):
    if kind == "index":
        row = await conn.fetchrow(
            "SELECT code,market,provider_symbol,source_uri,verified_at,publish_hour,valuation,collection_policy FROM reference.research_indices WHERE id=$1",
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
        frame.to_dict("records"),
        return_verified=config.get("returnSeriesVerified") is True,
        start=start,
        end=end,
    )
    return (
        points,
        series_kind,
        config.get("sourceUri") or "AKSHARE/fund_open_fund_info_em",
    )


async def refresh_subject(
    pool, settings, kind, subject_id, as_of, *, now=None, _claimed=False
):
    async with system_connection(pool) as conn:
        subject = await load_subject(conn, kind, subject_id)
    if kind == "index" and index_policy.configured(subject):
        if not _claimed:
            # Full Worker and the independent collector share one durable lease.
            from backend.jobs.index_research import claim_index, finish_index

            attempted = now or now_shanghai()
            async with system_connection(pool) as conn:
                claim = await claim_index(
                    conn, {**subject, "id": subject_id}, attempted, force=True
                )
            if claim is None:
                raise DataUnavailableError("指数已有正在运行的研究任务")
            try:
                result = await _refresh_official_index(
                    pool, subject_id, subject, as_of, attempted
                )
            except BaseException as exc:
                async with system_connection(pool) as conn:
                    await finish_index(
                        conn,
                        claim,
                        now_shanghai(),
                        {"status": "FAILED", "errorClass": type(exc).__name__},
                        cancelled=isinstance(exc, asyncio.CancelledError),
                    )
                raise
            async with system_connection(pool) as conn:
                status = index_policy.research_status(result)
                await finish_index(
                    conn,
                    claim,
                    now_shanghai(),
                    {
                        "status": status,
                        "dataQuality": result.get("dataQuality"),
                        "trendQuality": result.get("trendQuality"),
                    },
                )
            if status == "FAILED":
                raise DataUnavailableError("指数当前连续交易窗口或来源质量尚未通过核验")
            return result
        return await _refresh_official_index(
            pool, subject_id, subject, as_of, now or now_shanghai()
        )
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
    history_start = years_before(as_of, 5)
    rows, series_kind, source = await asyncio.wait_for(
        fetch_history(subject, kind, history_start, eligible_day or as_of), timeout=90
    )
    rows = [
        p for p in rows if history_start.isoformat() <= p["date"] <= as_of.isoformat()
    ]
    if not rows:
        raise DataUnavailableError("来源没有有效历史数据")
    if eligible_day is not None:
        rows = [p for p in rows if p["date"] <= eligible_day.isoformat()]
        if not rows:
            raise DataUnavailableError("已发布窗口内没有有效历史数据")
    async with system_connection(pool) as conn:
        calendar = await conn.fetch(
            """SELECT calendar_date,is_trading_day FROM reference.trading_calendars
          WHERE market=$1 AND calendar_date BETWEEN $2 AND $3 ORDER BY calendar_date""",
            calendar_market(subject["market"]),
            date.fromisoformat(rows[0]["date"]),
            eligible_day,
        )
    covered_days = {str(r["calendar_date"]) for r in calendar}
    days = {str(r["calendar_date"]) for r in calendar if r.get("is_trading_day", True)}
    observed_days = {p["date"] for p in rows}
    latest = date.fromisoformat(rows[-1]["date"])
    # Do not infer source publication schedules or manufacturing OHLC for NAV.
    expected = date.fromisoformat(max(days)) if days else None
    state = quality(
        latest=latest,
        expected=expected,
        verified=verified,
        missing=bool({day for day in days if day <= rows[-1]["date"]} - observed_days),
    )
    if observed_days - covered_days:
        state = "UNVERIFIED"
    elif observed_days - days:
        if series_kind not in {"UNIT_NAV", "PROVIDER_RETURN_INDEX"}:
            state = "CONFLICT"
    nav_observations = (
        rows if series_kind in {"UNIT_NAV", "PROVIDER_RETURN_INDEX"} else None
    )
    if (
        nav_observations is not None
        and covered_days
        and not (observed_days - covered_days)
    ):
        rows = [point for point in rows if point["date"] in days]
        if not rows:
            raise DataUnavailableError("没有已核验交易日内的研究净值")
        latest = date.fromisoformat(rows[-1]["date"])
        state = quality(
            latest=latest,
            expected=expected,
            verified=verified,
            missing=bool(
                {day for day in days if day <= rows[-1]["date"]}
                - {p["date"] for p in rows}
            ),
        )
    if any(p.get("returnQuality") == "CONFLICT" for p in (nav_observations or rows)):
        state = "CONFLICT"
    if (
        eligible_day is None
        or len(covered_days)
        != (
            eligible_day - date.fromisoformat((nav_observations or rows)[0]["date"])
        ).days
        + 1
    ):
        state = "UNVERIFIED"
    result = analyze(rows, quality=state)
    result["market"] = subject["market"]
    result["publicationHour"] = publish_hour
    result["calendarMarket"] = calendar_market(subject["market"])
    result["dataQuality"] = state
    if nav_observations is not None:
        result["navObservations"] = nav_observations
        result["sampling"] = "verified_trading_sessions"
    result["returnSeriesVerified"] = (
        config.get("returnSeriesVerified") is True if kind == "fund" else False
    )
    result["performance"] = analyze_performance(
        rows,
        series_kind=series_kind,
        quality=state,
        return_index_verified=result["returnSeriesVerified"],
    )
    try:
        facts = await refresh_fundamentals(
            pool, kind, subject_id, subject, as_of, now=now or now_shanghai()
        )
    except Exception:  # noqa: BLE001 -- failed evidence cannot suppress historical prices
        import logging

        logging.getLogger("wealthsail.investment_research").exception(
            "fundamentals_refresh_failed"
        )
        facts = {}
    result["fundamentals"] = facts
    result["costs"] = facts.get("costs") or {"state": "UNAVAILABLE"}
    result["valuation"] = facts.get("valuation") or {"state": "UNAVAILABLE"}
    if kind == "fund":
        result["valueEvidence"] = evaluate(
            result["valuation"],
            result["costs"],
            result["performance"],
            mapped=facts.get("mapped") is True,
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
            "performanceVersion": PERFORMANCE_VERSION,
            "valueVersion": VALUE_VERSION,
            "fundamentals": facts,
            "navObservations": nav_observations,
            "sampling": "verified_trading_sessions",
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
            f"{VERSION}+{PERFORMANCE_VERSION}+{VALUE_VERSION}",
            json.dumps(result),
            json.dumps(rows),
        )


async def _refresh_official_index(pool, subject_id, subject, as_of, now):
    from backend.indicators.value_evidence import _valuation_evidence
    from backend.jobs.investment_fundamentals import _persist

    if not index_policy.trusted(subject, now) or as_of > now.date():
        raise DataUnavailableError("官方指数来源或截止策略尚未核验")
    start = years_before(as_of, 5)
    async with system_connection(pool) as conn:
        end = await index_policy.expected_session(conn, now)
    if end is None:
        raise DataUnavailableError("指数交易日历缺失或陈旧")
    raw = await fetch_index_history(subject["code"], start, end)
    rows = raw["points"]
    retrieved = datetime.fromisoformat(raw["retrievedAt"])
    async with system_connection(pool) as conn:
        expected = await index_policy.expected_session(
            conn, now, date.fromisoformat(rows[0]["date"])
        )
        calendar = await conn.fetch(
            """SELECT calendar_date,is_trading_day FROM reference.trading_calendars
          WHERE market='SSE' AND calendar_date BETWEEN $1 AND $2 ORDER BY calendar_date""",
            date.fromisoformat(rows[0]["date"]),
            end,
        )
    sessions = {str(row["calendar_date"]) for row in calendar if row["is_trading_day"]}
    observations = {point["date"] for point in rows}
    state = quality(
        latest=date.fromisoformat(rows[-1]["date"]),
        expected=expected,
        verified=True,
        missing=bool(sessions - observations),
    )
    if observations - sessions:
        state = "CONFLICT"
    pe = {**raw["pe"], **_valuation_evidence(raw["pe"], end, "PE_TTM")}
    if state in {"STALE", "UNVERIFIED"}:
        pe["state"] = state
        pe["percentile"] = None
    valuation = {
        "state": "PARTIAL",
        "provider": "CSINDEX",
        "sourceUri": subject["source_uri"],
        "requestedEnd": end.isoformat(),
        "pe": pe,
        "pb": {
            "state": "UNAVAILABLE",
            "metric": "PB",
            "points": [],
            "latest": None,
            "percentile": None,
            "sampleCount": 0,
        },
        "retrievedAt": raw["retrievedAt"],
        "timeBasis": "RETRIEVAL_ONLY",
        "reasons": ["官方滚动市盈率独立可查；尚无同口径PB和企业盈利证据。"],
    }
    configuration = index_policy.config_hash(subject)
    missing = sessions - observations
    last_gap = max(missing) if missing else None
    tail = [point for point in rows if last_gap is None or point["date"] > last_gap]
    trend_quality = state
    if (
        expected
        and rows[-1]["date"] == expected.isoformat()
        and not observations - sessions
    ):
        trend_quality = "READY" if len(tail) >= 200 else "INSUFFICIENT"
    window = {
        "start": tail[0]["date"] if tail else None,
        "end": tail[-1]["date"] if tail else None,
        "sampleCount": len(tail),
    }
    result = {
        **analyze(tail, quality=trend_quality),
        "market": subject["market"],
        "calendarMarket": "SSE",
        "publicationHour": None,
        "dataQuality": state,
        "collectionPolicy": index_policy.POLICY,
        "configurationHash": configuration,
        "timeBasis": "RETRIEVAL_ONLY",
        "publishedAt": None,
        "retrievedAt": raw["retrievedAt"],
        "officialPE": pe,
        "valuation": valuation,
        "performance": analyze_performance(
            rows, series_kind="INDEX_PRICE", quality=state
        ),
        "asOf": rows[-1]["date"],
        "historyQuality": state,
        "trendQuality": trend_quality,
        "trendWindow": window,
    }
    if state != "READY":
        result.update(state=state, score=None)
    if (
        state == "READY"
        and result["state"] == "UNSUPPORTED"
        and result["indicators"].get("ma200") is not None
    ):
        result.update(state="READY", score=None, technicalCoverage="CLOSE_ONLY")
    content = {
        "points": rows,
        "pe": {key: value for key, value in pe.items() if key != "retrievedAt"},
        "source": subject["source_uri"],
        "configurationHash": configuration,
        "state": state,
        "trendWindow": window,
        "trendQuality": trend_quality,
    }
    input_hash = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    async with system_connection(pool) as conn:
        # The immutable observation's availability is the actual completed HTTP time.
        async with conn.transaction():
            current = await load_subject(conn, "index", subject_id)
            if (
                not index_policy.trusted(current, retrieved)
                or index_policy.config_hash(current) != configuration
            ):
                raise DataUnavailableError("指数来源配置已变更")
            await _persist(conn, "index", subject_id, "VALUATION", valuation, retrieved)
            await conn.execute(
                """INSERT INTO market.investment_analysis_snapshots(subject_kind,subject_key,input_hash,source_uri,
              series_kind,as_of,retrieved_at,available_at,method_version,result,points)
              VALUES('index',$1,$2,$3,'INDEX_PRICE',$4,$5,$5,$6,$7::jsonb,$8::jsonb)
              ON CONFLICT(subject_kind,subject_key,input_hash,method_version) DO NOTHING""",
                subject_id,
                input_hash,
                subject["source_uri"],
                date.fromisoformat(rows[-1]["date"]),
                retrieved,
                f"{VERSION}+{index_policy.VERSION}",
                json.dumps(result),
                json.dumps(rows),
            )
    return result


async def sync_research(pool, settings, now: datetime):
    import logging
    from backend.jobs.investment_macro import sync_macro

    logger = logging.getLogger("wealthsail.investment_research")
    try:
        calendar = await ensure_trading_calendar(pool)
        logger.info("research_calendar_refresh %s", calendar)
    except Exception:  # noqa: BLE001 -- retain facts, missing calendar blocks eligibility
        logger.exception("research_calendar_refresh_failed")
    macro_observations = None
    try:
        macro_stats = await asyncio.wait_for(sync_macro(pool, now), timeout=90)
        macro_observations = macro_stats["observations"]
        logger.info("research_macro_sync observations=%s", macro_observations)
        if macro_observations == 0:
            logger.warning("research_macro_history_empty observations=0")
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
    logger.info(
        "research_sync_completed succeeded=%d failed=%d macro_observations=%s",
        succeeded,
        failed,
        macro_observations,
    )
    return {"succeeded": succeeded, "failed": failed}
