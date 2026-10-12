from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

from backend.investment import repository
from backend.investment.schemas import ScreenQuery
from backend.errors import NotFoundError
from backend.dependencies import now_shanghai
from backend.market.research_history import published_day


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def macro_dto(row):
    verified = (
        row.get("published_at") is not None and row.get("quality_status") == "FRESH"
    )
    value = row.get("value")
    state = (
        "READY" if verified else "UNVERIFIED" if value is not None else "NOT_COLLECTED"
    )
    published = row.get("published_at")
    if isinstance(published, datetime) and datetime.now(
        published.tzinfo
    ) - published > timedelta(days=45):
        state = "STALE"
    return {
        "code": row["field_code"],
        "label": row.get("label", row["field_code"]),
        "dimension": row.get("dimension"),
        "value": value,
        "asOf": _iso(row.get("as_of")),
        "publishedAt": _iso(published),
        "retrievedAt": _iso(row.get("retrieved_at")),
        "source": row.get("source_name"),
        "revision": row.get("revision"),
        "state": state,
        "assessment": None,
    }


async def market_context(conn):
    items = [macro_dto(dict(row)) for row in await repository.macro(conn)]
    return {
        "items": items,
        "assessment": None,
        "state": "READY"
        if items and all(x["state"] == "READY" for x in items)
        else "UNVERIFIED",
    }


async def directions(conn):
    return [
        {
            "id": r["id"],
            "parentId": r["parent_id"],
            "marketId": r["market_id"],
            "styleId": r["style_id"],
            "marketLabel": r["market_label"],
            "label": r["label"],
            "framework": r["framework"],
            "indexCount": r["index_count"],
            "state": "NOT_EVALUATED",
            "assessment": None,
        }
        for r in await repository.directions(conn)
    ]


async def indices(conn, direction_id):
    return [
        {
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "market": r["market"],
            "exposure": r["exposure"],
            "fundCount": r["fund_count"],
            "state": "NOT_COLLECTED",
        }
        for r in await repository.indices(conn, direction_id)
    ]


def fund_dto(r):
    return {
        "instrumentId": str(r["instrument_id"]) if r["instrument_id"] else None,
        "fundCode": r["fund_code"],
        "fundName": r["fund_name"],
        "fundType": r["fund_type"],
        "tradeForm": r["trade_form"],
        "market": r["market"],
        "currency": r["currency"],
        "latestValue": r.get("latest_nav"),
        "latestDate": _iso(r.get("latest_nav_date")),
        "navRangePosition": r.get("current_percentile"),
        "updatedAt": _iso(r.get("updated_at")),
        "technicalScore": r.get("technical_score"),
        "technicalState": r.get("technical_state"),
        "historyDays": r.get("history_days", 0),
        "dataState": "READY"
        if r.get("technical_score") is not None
        else r.get("technical_state")
        if r.get("technical_state") is not None
        else "UNAVAILABLE"
        if r.get("data_source") == "UNAVAILABLE"
        else "RANGE_ONLY"
        if r.get("latest_nav") is not None
        else "NOT_COLLECTED",
    }


async def screen(conn, query):
    query = query.model_copy(
        update={"analysis_at": query.analysis_at or datetime.now(timezone.utc)}
    )
    rows, total = await repository.screen(conn, query)
    return {
        "items": [fund_dto(dict(row)) for row in rows],
        "total": total,
        "page": query.page,
        "pageSize": query.page_size,
        "analysisAsOf": _iso(query.analysis_at),
    }


async def search(conn, keyword):
    keyword = keyword.strip()
    if not keyword:
        return []
    indices_rows = await repository.search_indices(conn, keyword)
    result = [
        {
            "kind": "index",
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "indexId": r["id"],
            "directionId": r["direction_id"],
            "marketId": r["market_id"],
            "styleId": r["style_id"],
        }
        for r in indices_rows
    ]
    rows, _ = await repository.screen(conn, ScreenQuery(keyword=keyword, pageSize=10))
    for row in rows:
        item = fund_dto(dict(row))
        loc = (
            await repository.location(conn, row["instrument_id"])
            if row["instrument_id"]
            else None
        )
        result.append(
            {
                **item,
                "kind": "fund",
                "id": item["instrumentId"] or f"FUND_CN:{item['fundCode']}",
                "code": item["fundCode"],
                "name": item["fundName"],
                "indexId": loc["index_id"] if loc else None,
                "directionId": loc["direction_id"] if loc else None,
                "marketId": loc["market_id"] if loc else None,
                "styleId": loc["style_id"] if loc else None,
            }
        )
    return result[:20]


async def analysis(conn, kind, subject_id):
    row = await conn.fetchrow(
        """
        SELECT result,as_of,input_hash,source_uri,points,retrieved_at,series_kind
        FROM market.investment_analysis_snapshots
        WHERE subject_kind=$1 AND subject_key=$2 ORDER BY retrieved_at DESC LIMIT 1
    """,
        kind,
        subject_id,
    )
    if row is None:
        return {
            "state": "NOT_COLLECTED",
            "score": None,
            "indicators": {},
            "reasons": [],
            "points": [],
        }
    result = row["result"]
    if isinstance(result, str):
        result = json.loads(result)
    if result.get("score") is not None:
        now = now_shanghai()
        cutoff = published_day(now.date(), result.get("publicationHour"), now)
        expected = await conn.fetchval(
            """SELECT max(calendar_date)
          FROM reference.trading_calendars WHERE market=$1 AND is_trading_day
            AND calendar_date <= $2""",
            result.get("market"),
            cutoff,
        )
        if expected is None or row["as_of"] < expected:
            result = {
                **result,
                "score": None,
                "state": "STALE" if expected else "UNVERIFIED",
            }
    points = row.get("points", [])
    return {
        **result,
        "asOf": _iso(row["as_of"]),
        "inputHash": row["input_hash"],
        "source": row["source_uri"],
        "points": json.loads(points) if isinstance(points, str) else points,
        "retrievedAt": _iso(row.get("retrieved_at")),
        "seriesKind": row.get("series_kind"),
    }


async def enqueue_refresh(conn, tenant_id, body):
    if body.kind == "nav":
        from backend.funds.service import enqueue_fetch

        return await enqueue_fetch(conn, tenant_id=tenant_id, fund_code=body.id)
    from backend.jobs.investment_research import load_subject

    await load_subject(conn, body.kind, body.id)
    key = f"investment-refresh:{body.kind}:{body.id}"
    row = await conn.fetchrow(
        """
      INSERT INTO workflow.job_runs(tenant_id,job_key,job_type,payload,status,available_at)
      VALUES($1,$2,'INVESTMENT_RESEARCH_REFRESH',$3::jsonb,'PENDING',now())
      ON CONFLICT(tenant_id,job_key) DO NOTHING RETURNING id
    """,
        tenant_id,
        key,
        json.dumps(body.model_dump()),
    )
    created = row is not None
    if row is None:
        row = await conn.fetchrow(
            """
          UPDATE workflow.job_runs SET status='PENDING',available_at=now(),next_retry_at=NULL,
            attempt_count=0,started_at=NULL,finished_at=NULL,error_class=NULL,lease_token=NULL,
            lease_owner=NULL,lease_until=NULL
          WHERE tenant_id=$1 AND job_key=$2 AND (status='COMPLETED' OR (status='FAILED' AND next_retry_at IS NULL))
          RETURNING id
        """,
            tenant_id,
            key,
        )
        created = row is not None
    if row is None:
        row = await conn.fetchrow(
            "SELECT id FROM workflow.job_runs WHERE tenant_id=$1 AND job_key=$2",
            tenant_id,
            key,
        )
    return {"runId": str(row["id"]), "created": created, "status": "QUEUED"}


async def refresh_status(conn, tenant_id, run_id):
    row = await conn.fetchrow(
        """SELECT status,finished_at,error_class,next_retry_at
      FROM workflow.job_runs WHERE tenant_id=$1 AND id=$2 AND job_type IN ('INVESTMENT_RESEARCH_REFRESH','FUND_NAV_RANGE_FETCH')""",
        tenant_id,
        UUID(run_id),
    )
    if not row:
        raise NotFoundError("更新任务不存在")
    status = "QUEUED" if row["status"] == "PENDING" else row["status"]
    if status == "FAILED" and row["next_retry_at"] is not None:
        status = "QUEUED"
    return {
        "status": status,
        "completedAt": _iso(row["finished_at"]),
        "errorCode": row["error_class"],
    }
