"""Actor-parameterized append-only study history. Never creates ledger facts."""

import json
from datetime import date

from backend.errors import IdempotencyConflictError, NotFoundError, VersionConflictError
from backend.investment.decision import semantic_hash


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


async def daily_origin(conn, run_id, event_id, now):
    row = await conn.fetchrow(
        """SELECT r.run_id,t.payload,t.recorded_at FROM research.daily_research_runs r
        JOIN research.daily_research_results t USING(run_id)
        WHERE r.run_id=$1 AND t.status='READY' AND t.recorded_at<=$2 AND r.dispatched_at<=$2""",
        run_id,
        now,
    )
    if row is None:
        raise NotFoundError("有效研究报告不存在")
    report = decode(row["payload"])
    event = next(
        (event for event in report.get("events", []) if event.get("id") == event_id),
        None,
    )
    if event is None:
        raise NotFoundError("该报告中不存在所选事件")
    refs = set(event.get("sourceIds") or [])
    return {
        "originType": "DAILY_EVENT",
        "reportRunId": str(row["run_id"]),
        "eventId": event_id,
        "reportAsOf": report.get("asOf") or row["recorded_at"].isoformat(),
        "event": event,
        "sources": [
            source for source in report.get("sources", []) if source.get("id") in refs
        ],
    }


async def lock_track(conn, tenant_id, user_id, track_id, expected_generation=None):
    row = await conn.fetchrow(
        "SELECT * FROM research.study_tracks WHERE tenant_id=$1 AND user_id=$2 AND id=$3 FOR UPDATE",
        tenant_id,
        user_id,
        track_id,
    )
    if row is None:
        raise NotFoundError("研究主题不存在")
    if expected_generation is not None and row["generation"] != expected_generation:
        raise VersionConflictError("研究主题已更新，请刷新后重试")
    return dict(row)


async def latest_version(conn, tenant_id, user_id, track_id):
    row = await conn.fetchrow(
        "SELECT * FROM research.study_versions WHERE tenant_id=$1 AND user_id=$2 AND track_id=$3 ORDER BY version DESC LIMIT 1",
        tenant_id,
        user_id,
        track_id,
    )
    if row is None:
        raise NotFoundError("研究版本不存在")
    return {**dict(row), "snapshot": decode(row["snapshot"])}


async def version_by_id(conn, tenant_id, user_id, track_id, version_id):
    row = await conn.fetchrow(
        "SELECT * FROM research.study_versions WHERE tenant_id=$1 AND user_id=$2 AND track_id=$3 AND id=$4",
        tenant_id,
        user_id,
        track_id,
        version_id,
    )
    if row is None:
        raise NotFoundError("研究版本不存在")
    return {**dict(row), "snapshot": decode(row["snapshot"])}


async def command_replayed(
    conn, table, tenant_id, user_id, track_id, command_key, command_hash
):
    if table not in {"study_versions", "study_notes"}:
        raise ValueError("unsupported command history")
    row = await conn.fetchrow(
        f"SELECT command_hash FROM research.{table} WHERE tenant_id=$1 AND user_id=$2 AND track_id=$3 AND command_key=$4",
        tenant_id,
        user_id,
        track_id,
        command_key,
    )
    if row and row["command_hash"] != command_hash:
        raise IdempotencyConflictError("幂等键已用于其他研究命令")
    return row is not None


async def add_version(
    conn, track, snapshot, version_number, reason, command_key, command_hash, now
):
    return await conn.fetchrow(
        """INSERT INTO research.study_versions(tenant_id,user_id,track_id,version,generation,snapshot,reason,command_key,command_hash,created_at)
        VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10) RETURNING *""",
        track["tenant_id"],
        track["user_id"],
        track["id"],
        version_number,
        track["generation"],
        dump(snapshot),
        reason,
        command_key,
        command_hash,
        now,
    )


async def add_note(
    conn, track, version_id, kind, payload, command_key, command_hash, now
):
    return await conn.fetchrow(
        """INSERT INTO research.study_notes(tenant_id,user_id,track_id,version_id,generation,kind,payload,command_key,command_hash,created_at)
        VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10) ON CONFLICT DO NOTHING RETURNING *""",
        track["tenant_id"],
        track["user_id"],
        track["id"],
        version_id,
        track["generation"],
        kind,
        dump(payload),
        command_key,
        command_hash,
        now,
    )


async def advance(conn, track, now, *, enabled=None):
    row = await conn.fetchrow(
        """UPDATE research.study_tracks SET enabled=$4,generation=generation+1,updated_at=$5
        WHERE tenant_id=$1 AND user_id=$2 AND id=$3 RETURNING *""",
        track["tenant_id"],
        track["user_id"],
        track["id"],
        track["enabled"] if enabled is None else enabled,
        now,
    )
    return dict(row)


async def latest_check(conn, track, version_id, now):
    row = await conn.fetchrow(
        """SELECT * FROM research.study_checks WHERE tenant_id=$1 AND user_id=$2 AND track_id=$3
        AND version_id=$4 AND generation=$5 AND evaluated_at<=$6 ORDER BY evaluated_at DESC,id DESC LIMIT 1""",
        track["tenant_id"],
        track["user_id"],
        track["id"],
        version_id,
        track["generation"],
        now,
    )
    return {**dict(row), "result": decode(row["result"])} if row else None


def check_fingerprint(result):
    return semantic_hash(
        {
            "input": result.get("inputHash"),
            "quality": result.get("quality"),
            "state": result.get("state"),
            "conditions": [
                {key: item.get(key) for key in ("id", "result")}
                for item in result.get("conditionResults", [])
            ],
        }
    )


async def add_check(conn, track, version, result, now):
    # Compare only the immediate predecessor. A returned fact (A -> B -> A)
    # is a new historical transition, while repeated retrieval of A is not.
    latest = await latest_check(conn, track, version["id"], now)
    fingerprint = check_fingerprint(result)
    if latest and check_fingerprint(latest["result"]) == fingerprint:
        return None
    key = semantic_hash(
        {
            "semantic": fingerprint,
            "previousCheckId": str(latest["id"]) if latest else None,
        }
    )
    row = await conn.fetchrow(
        """INSERT INTO research.study_checks(tenant_id,user_id,track_id,version_id,generation,evaluated_at,input_hash,method_version,result)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb) ON CONFLICT DO NOTHING RETURNING *""",
        track["tenant_id"],
        track["user_id"],
        track["id"],
        version["id"],
        track["generation"],
        now,
        key,
        result.get("methodVersion", "study-conditions-v1"),
        dump(result),
    )
    return row


def track_dto(row):
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "scope": decode(row["scope"]),
        "enabled": row["enabled"],
        "generation": row["generation"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "status": row.get("state") or ("OBSERVING" if row["enabled"] else "ENDED"),
        "unreadCount": int(row.get("unread_count") or 0),
    }


def version_dto(row):
    return {
        "id": str(row["id"]),
        "version": row["version"],
        "generation": row["generation"],
        "snapshot": decode(row["snapshot"]),
        "reason": row["reason"],
        "createdAt": row["created_at"],
    }


def check_dto(row):
    return {
        "id": str(row["id"]),
        "versionId": str(row["version_id"]),
        "generation": row["generation"],
        "evaluatedAt": row["evaluated_at"],
        "result": decode(row["result"]),
    }


def note_dto(row):
    return {
        "id": str(row["id"]),
        "versionId": str(row["version_id"]),
        "generation": row["generation"],
        "kind": row["kind"],
        "payload": decode(row["payload"]),
        "createdAt": row["created_at"],
    }


def link_dto(row):
    return {
        "id": str(row["id"]),
        "versionId": str(row["version_id"]),
        "fillId": str(row["fill_id"]),
        "linkedAt": row["linked_at"],
        "reason": row["reason"],
        "executionTimePrecision": "DAY",
        "timingRelation": row["timing_relation"],
        "fill": decode(row["fill"]),
    }


_TRACK_PROJECTION = """SELECT t.*,CASE WHEN NOT t.enabled THEN 'ENDED' ELSE
 COALESCE((SELECT c.result->>'state' FROM research.study_checks c WHERE c.tenant_id=t.tenant_id
 AND c.user_id=t.user_id AND c.track_id=t.id AND c.generation=t.generation AND c.evaluated_at<=$3
 ORDER BY c.evaluated_at DESC,c.id DESC LIMIT 1),'OBSERVING') END AS state,
 (SELECT count(*) FROM research.study_checks c WHERE c.tenant_id=t.tenant_id AND c.user_id=t.user_id
 AND c.track_id=t.id AND c.generation=t.generation AND c.evaluated_at<=$3 AND c.result->>'meaningfulChange'='true'
 AND c.evaluated_at>COALESCE((SELECT max((n.payload->>'throughCheckedAt')::timestamptz)
   FROM research.study_notes n WHERE n.tenant_id=t.tenant_id AND n.user_id=t.user_id AND n.track_id=t.id
   AND n.kind='ACK' AND n.created_at<=$3),'-infinity'::timestamptz)) AS unread_count
 FROM research.study_tracks t WHERE t.tenant_id=$1 AND t.user_id=$2 AND t.created_at<=$3"""


async def list_tracks(conn, tenant_id, user_id, now, *, limit=50, offset=0):
    rows = await conn.fetch(
        _TRACK_PROJECTION + " ORDER BY t.updated_at DESC,t.id LIMIT $4 OFFSET $5",
        tenant_id,
        user_id,
        now,
        limit,
        offset,
    )
    count = await conn.fetchval(
        "SELECT count(*) FROM research.study_tracks WHERE tenant_id=$1 AND user_id=$2 AND created_at<=$3",
        tenant_id,
        user_id,
        now,
    )
    return {"items": [track_dto(row) for row in rows], "total": count}


async def detail_rows(conn, tenant_id, user_id, track_id, now, *, limit=50, offset=0):
    track = await conn.fetchrow(
        _TRACK_PROJECTION + " AND t.id=$4", tenant_id, user_id, now, track_id
    )
    if track is None:
        raise NotFoundError("研究主题不存在")
    history, counts = {}, {}
    for name, column, convert in (
        ("versions", "created_at", version_dto),
        ("checks", "evaluated_at", check_dto),
        ("notes", "created_at", note_dto),
        ("trade_links", "linked_at", link_dto),
    ):
        rows = await conn.fetch(
            f"SELECT * FROM research.study_{name} WHERE tenant_id=$1 AND user_id=$2 AND track_id=$3 AND {column}<=$4 ORDER BY {column} DESC,id DESC LIMIT $5 OFFSET $6",
            tenant_id,
            user_id,
            track_id,
            now,
            limit,
            offset,
        )
        history[name] = [convert(row) for row in rows]
        counts[name] = await conn.fetchval(
            f"SELECT count(*) FROM research.study_{name} WHERE tenant_id=$1 AND user_id=$2 AND track_id=$3 AND {column}<=$4",
            tenant_id,
            user_id,
            track_id,
            now,
        )
    version = await latest_version(conn, tenant_id, user_id, track_id)
    return {
        "track": track_dto(track),
        "version": version_dto(version),
        "versions": history["versions"],
        "checks": history["checks"],
        "notes": history["notes"],
        "tradeLinks": history["trade_links"],
        "history": {
            "limit": limit,
            "offset": offset,
            "hasMore": any(count > offset + limit for count in counts.values()),
            "totals": counts,
        },
        "capabilities": {
            "canEdit": True,
            "canCheck": track["enabled"],
            "canLinkTrade": True,
        },
    }


async def eligible_trades(conn, tenant_id, user_id, scope, now):
    rows = await conn.fetch(
        """SELECT f.*,i.canonical_name,i.trading_currency AS currency FROM ledger.trade_fills f JOIN reference.instruments i ON i.id=f.instrument_id
        WHERE f.tenant_id=$1 AND f.status='CONFIRMED' AND f.ledger_event_id IS NOT NULL AND f.confirmed_at<=$3
        AND f.trade_date<=($3 AT TIME ZONE 'Asia/Shanghai')::date
        AND f.quantity>0 AND f.unit_price>0 AND f.fee>=0
        AND f.quantity::text NOT IN ('NaN','Infinity','-Infinity')
        AND f.unit_price::text NOT IN ('NaN','Infinity','-Infinity')
        AND f.fee::text NOT IN ('NaN','Infinity','-Infinity')
        AND ($4::text IS NULL OR f.instrument_id::text=$4)
        AND EXISTS(SELECT 1 FROM identity.account_members owner WHERE owner.tenant_id=f.tenant_id
          AND owner.account_id=f.account_id AND owner.user_id=$2
          AND owner.relationship IN ('BENEFICIAL_OWNER','JOINT_OWNER')
          AND owner.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
          AND (owner.effective_to IS NULL OR owner.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date))
        AND ($5::text IS NULL OR EXISTS(SELECT 1 FROM reference.fund_index_links l JOIN reference.research_indices idx ON idx.id=l.index_id
          WHERE l.instrument_id=f.instrument_id AND l.index_id=$5 AND l.verified_at<=$3 AND idx.verified_at<=$3
            AND l.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
            AND (l.effective_to IS NULL OR l.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date)))
        AND ($6::text IS NULL OR EXISTS(SELECT 1 FROM reference.fund_index_links l
          JOIN reference.research_indices idx ON idx.id=l.index_id AND idx.verified_at<=$3
          JOIN reference.direction_index_links d ON d.index_id=l.index_id AND d.direction_id=$6
          WHERE l.instrument_id=f.instrument_id AND l.verified_at<=$3
          AND l.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
          AND (l.effective_to IS NULL OR l.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date)))
        ORDER BY f.trade_date DESC,f.id LIMIT 200""",
        tenant_id,
        user_id,
        now,
        scope.get("instrumentId"),
        scope.get("indexId"),
        scope.get("directionId"),
    )
    return [
        {
            "id": str(row["id"]),
            "fillId": str(row["id"]),
            "accountId": str(row["account_id"]),
            "instrumentId": str(row["instrument_id"]),
            "instrumentName": row["canonical_name"],
            "side": row["side"],
            "quantity": str(row["quantity"]),
            "unitPrice": str(row["unit_price"]),
            "fee": str(row["fee"]),
            "currency": row["currency"],
            "tradeDate": row["trade_date"].isoformat(),
            "status": "CONFIRMED",
            "sourceType": row["source_type"],
            "executionTimePrecision": "DAY",
        }
        for row in rows
    ]


async def add_trade_link(conn, track, version, fill, reason, now):
    created = version["created_at"].astimezone(now.tzinfo).date()
    traded = date.fromisoformat(fill["tradeDate"])
    timing = (
        "BEFORE_TRADE"
        if created < traded
        else "POST_HOC"
        if created > traded
        else "UNKNOWN"
    )
    return await conn.fetchrow(
        """INSERT INTO research.study_trade_links(tenant_id,user_id,track_id,version_id,generation,fill_id,linked_at,reason,timing_relation,fill)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb) ON CONFLICT DO NOTHING RETURNING *""",
        track["tenant_id"],
        track["user_id"],
        track["id"],
        version["id"],
        track["generation"],
        fill["fillId"],
        now,
        reason,
        timing,
        dump(fill),
    )
