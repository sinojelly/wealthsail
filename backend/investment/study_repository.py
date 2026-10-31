"""Actor-parameterized append-only study history. Never creates ledger facts."""

import json
from copy import deepcopy
from datetime import date, datetime
import re
from uuid import UUID

from backend.errors import (
    IdempotencyConflictError,
    NotFoundError,
    VersionConflictError,
    ValidationError_,
)
from backend.investment.decision import semantic_hash


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


_LIVE_TRACK_PRICES = (
    "track",
    "entryZoneMin",
    "entryZoneMax",
    "stopLossPrice",
    "takeProfitT1",
    "takeProfitT2",
    "sellZoneMin",
    "sellZoneMax",
    "estimatedProfitMinPct",
    "estimatedProfitMaxPct",
    "currentToSellMaxPct",
)


def live_plan_coordinate(plan):
    """Stable price identity excludes request-clock refresh metadata."""
    if not isinstance(plan.get("priceRefresh"), dict):
        return None
    selected = plan.get("selectedTrack", plan.get("track"))
    tracks = [row for row in plan.get("tracks", []) if row.get("track") == selected]
    if len(tracks) != 1:
        raise ValidationError_("当前基金价格轨道不唯一，无法冻结")
    return {
        "methodVersion": plan.get("methodVersion"),
        "asOf": plan.get("asOf"),
        "currentPrice": plan.get("currentPrice"),
        "track": {
            key: deepcopy(tracks[0][key])
            for key in _LIVE_TRACK_PRICES
            if key in tracks[0]
        },
    }


async def _refresh_origin_plan(conn, plan, plan_track, now, *, packet=None):
    """Reprice a newly selected CNY fund after authenticating its report facts."""
    from backend.investment.live_fund_plans import _code, refresh_report_fund_plans
    from backend.macro.alpha_research import _program_plan
    from backend.macro.alpha_repository import read_alpha_public

    # New follows retain the original scope's explicit-market requirement;
    # the display helper may infer a missing market for a chart-only NAV row.
    if plan.get("market") != "FUND_CN" or _code(plan) is None:
        return deepcopy(plan)
    public = await read_alpha_public(conn, now)
    refreshed = await refresh_report_fund_plans(
        conn,
        {"actionablePlans": [{**deepcopy(plan), "selectedTrack": plan_track}]},
        now,
        histories=public.get("fundHistories"),
        packet=packet,
    )
    current = refreshed["actionablePlans"][0]
    if (current.get("priceRefresh") or {}).get("state") != "READY":
        raise ValidationError_("本地基金净值历史不可用或不完整，请更新净值后再跟踪")
    try:
        _program_plan(current, plan_track, now, require_theme=False)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError_(
            "本地基金净值尚不能形成所选轨道的研究价格，请更新后再跟踪"
        ) from exc
    return current


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


async def daily_direction_origin(
    conn, run_id, direction_key, subject_kind, subject_id, plan_track, now
):
    """Authenticate original research, then freeze current local fund prices."""
    from backend.macro.alpha_research import _program_plan
    from backend.macro.daily_research import _visible
    from backend.investment.study import direction_macro_evidence
    from backend.macro.value_directions import direction_quantitative_evidence

    row = await conn.fetchrow(
        """SELECT r.run_id,r.public_packet,t.payload,t.recorded_at FROM research.daily_research_runs r
        JOIN research.daily_research_results t USING(run_id)
        WHERE r.run_id=$1 AND t.status='READY' AND t.recorded_at<=$2 AND r.dispatched_at<=$2""",
        run_id,
        now,
    )
    if row is None:
        raise NotFoundError("有效研究报告不存在")
    report = decode(row["payload"])
    try:
        stamp = datetime.fromisoformat(report["asOf"])
        if (
            stamp.tzinfo is None
            or stamp > now
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", direction_key)
        ):
            raise ValueError("unavailable direction date or key")
        directions = [
            item
            for item in report.get("valueDirections", [])
            if isinstance(item, dict) and item.get("key") == direction_key
        ]
        if len(directions) != 1:
            raise ValueError("missing or ambiguous direction")
        direction = directions[0]
        if any(
            not isinstance(direction.get(key), str) or not direction[key].strip()
            for key in ("title", "thesis")
        ):
            raise ValueError("missing direction explanation")
        plans = [
            item
            for item in direction.get("plans", [])
            if isinstance(item, dict)
            and item.get("subjectKind") == subject_kind
            and item.get("subjectId") == subject_id
            and item.get("selectedTrack", item.get("track")) == plan_track
        ]
        if len(plans) != 1:
            raise ValueError("missing or ambiguous plan")
        plan = plans[0]
        _program_plan(plan, plan_track, stamp, require_theme=False)
        refs = set(direction.get("sourceIds") or [])
        sources = [
            source
            for source in report.get("sources", [])
            if isinstance(source, dict) and source.get("id") in refs
        ]
        if {source["id"] for source in sources} != refs or any(
            not _visible(source, stamp) for source in sources
        ):
            raise ValueError("missing direction sources")
        macro_evidence = (
            direction_macro_evidence(
                direction["macroEvidence"], direction.get("macroCodes") or [], now=stamp
            )
            if "macroEvidence" in direction
            else None
        )
        quantitative = direction_quantitative_evidence(
            direction.get("quantitativeEvidence") or [],
            direction.get("quantitativeFundCodes") or [],
            stamp,
            plans=direction.get("plans") or [],
        )
        if not refs and not quantitative:
            raise ValueError("missing direction evidence")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError_("该日报方向或标的计划无效，无法冻结") from exc
    from backend.investment.service import fund_risk_context, protect_fund_plan

    plan = protect_fund_plan(
        plan, fund_risk_context(report, now, packet=decode(row.get("public_packet")))
    )
    plan = await _refresh_origin_plan(
        conn, plan, plan_track, now, packet=decode(row.get("public_packet"))
    )
    frozen_direction = {
        key: deepcopy(direction[key])
        for key in (
            "key",
            "title",
            "thesis",
            "signal",
            "sourceIds",
            "macroCodes",
            "watchFor",
            "catalysts",
            "risks",
            "quantitativeFundCodes",
            "industryCycle",
            "industryRiskWarning",
            "reviewIndustryIds",
        )
        if key in direction
    }
    if macro_evidence is not None:
        frozen_direction["macroEvidence"] = macro_evidence
    if quantitative:
        frozen_direction["quantitativeEvidence"] = quantitative
    return {
        "originType": "DAILY_DIRECTION",
        "reportRunId": str(row["run_id"]),
        "valueDirectionKey": direction_key,
        "scanReuseAllowed": sum(
            any(
                isinstance(item, dict)
                and item.get("subjectKind") == subject_kind
                and item.get("subjectId") == subject_id
                and item.get("selectedTrack", item.get("track")) == plan_track
                for item in candidate.get("plans", [])
            )
            for candidate in report.get("valueDirections", [])
            if isinstance(candidate, dict)
        )
        == 1,
        "reportAsOf": report["asOf"],
        "title": f"{direction['title']} · {plan.get('label') or subject_id}",
        "summary": direction["thesis"],
        "watchFor": deepcopy(direction.get("watchFor") or []),
        "sources": deepcopy(sources),
        "valueDirection": frozen_direction,
        "actionablePlan": deepcopy(plan),
    }


async def direction_plan_scope(conn, plan):
    """Resolve identity using the report's exact product and market."""
    kind, identity, market = (
        plan.get("subjectKind"),
        plan.get("subjectId"),
        plan.get("market"),
    )
    if kind == "index":
        if not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM reference.research_indices WHERE id=$1)",
            identity,
        ):
            raise NotFoundError("日报中的指数标的不存在")
        return {"indexId": identity}
    if market != "FUND_CN" or plan.get("priceBasis") != "UNIT_NAV":
        raise ValidationError_("日报基金净值口径或市场不匹配")
    if (
        kind == "nav"
        and isinstance(identity, str)
        and re.fullmatch(r"[0-9]{6}", identity)
    ):
        if plan.get("fundCode") not in {None, identity}:
            raise ValidationError_("日报基金代码不匹配")
        return {"fundCode": identity, "fundMarket": "FUND_CN"}
    if kind != "fund":
        raise ValidationError_("日报标的身份无效")
    try:
        instrument = UUID(identity)
    except (ValueError, TypeError) as exc:
        raise ValidationError_("日报基金身份无效") from exc
    rows = await conn.fetch(
        """SELECT DISTINCT c.fund_code,c.market FROM reference.research_fund_catalog c
        JOIN reference.instruments i ON i.id=c.instrument_id AND i.lifecycle_status='ACTIVE'
          AND i.asset_type IN ('EXCHANGE_FUND','OFF_EXCHANGE_FUND')
        WHERE c.instrument_id=$1 AND c.market=$2 AND ($3::text IS NULL OR c.fund_code=$3)""",
        instrument,
        market,
        plan.get("fundCode"),
    )
    if len(rows) != 1:
        raise NotFoundError("日报基金与实际产品目录不匹配")
    return {
        "instrumentId": str(instrument),
        "fundCode": rows[0]["fund_code"],
        "fundMarket": rows[0]["market"],
    }


async def daily_quant_scan_origin(
    conn, run_id, subject_kind, subject_id, plan_track, now
):
    """Authenticate one original public scan coordinate at the report's clock."""
    from backend.investment.quantitative_scan import (
        public_quantitative_scan,
        report_with_quantitative_scan,
    )
    from backend.macro.alpha_research import _program_plan
    from backend.macro.daily_research import _stamp

    row = await conn.fetchrow(
        """SELECT r.run_id,r.public_packet,t.payload,t.recorded_at
        FROM research.daily_research_runs r JOIN research.daily_research_results t USING(run_id)
        WHERE r.run_id=$1 AND t.status='READY' AND t.recorded_at<=$2 AND r.dispatched_at<=$2""",
        run_id,
        now,
    )
    if row is None:
        raise NotFoundError("有效研究报告不存在")
    report = decode(row["payload"])
    try:
        if not isinstance(report, dict):
            raise ValueError("missing scan report payload")
        stamp = _stamp(report.get("asOf"))
        if stamp is None or stamp > now or subject_kind not in {"fund", "nav"}:
            raise ValueError("unavailable scan report clock or subject")
        report = report_with_quantitative_scan(
            report, decode(row.get("public_packet")), now
        )
        scan = public_quantitative_scan(report.get("quantitativeScan"), stamp)
        if scan is None:
            raise ValueError("report has no quantitative scan")
        plans = [
            item
            for item in scan["items"]
            if item["subjectKind"] == subject_kind and item["subjectId"] == subject_id
        ]
        if len(plans) != 1:
            raise ValueError("missing or ambiguous scan fund")
        plan = plans[0]
        if (
            len([signal for signal in plan["signals"] if signal["track"] == plan_track])
            != 1
        ):
            raise ValueError("unavailable scan track")
        _program_plan(plan, plan_track, stamp, require_theme=False)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError_("该日报的基金量化扫描或轨道无效，无法冻结") from exc
    original_plan = deepcopy(plan)
    plan = await _refresh_origin_plan(
        conn, plan, plan_track, now, packet=decode(row.get("public_packet"))
    )
    plan = {**deepcopy(plan), "selectedTrack": plan_track}
    return {
        "originType": "DAILY_QUANT_SCAN",
        "reportRunId": str(row["run_id"]),
        "reportAsOf": report["asOf"],
        "subjectKind": subject_kind,
        "subjectId": subject_id,
        "planTrack": plan_track,
        "title": f"{plan['label']} · 量化位置研究",
        "summary": "来自原日报完整基金扫描的真实净值与程序研究区间；技术条件和实际入区分别观察。",
        "watchFor": ["确认净值与冻结买区的关系、轨道技术条件及后续产业催化。"],
        "sources": [
            {
                "id": "quant-scan:" + plan["fundCode"],
                "title": plan["label"] + "真实净值历史",
                "url": original_plan["sourceUri"],
                "retrievedAt": original_plan.get("retrievedAt"),
            }
        ],
        "actionablePlan": plan,
    }


async def matching_report_plan_track(conn, tenant_id, user_id, origin, now):
    """Reuse the other entry point for this exact report, asset and track only."""
    if origin.get("scanReuseAllowed") is False:
        return None
    plan = origin["actionablePlan"]
    opposite = (
        "DAILY_DIRECTION"
        if origin["originType"] == "DAILY_QUANT_SCAN"
        else "DAILY_QUANT_SCAN"
    )
    coordinate = live_plan_coordinate(plan)
    current_price_filter = ""
    if coordinate is not None:
        current_price_filter = """
          AND jsonb_build_object(
            'methodVersion',v.snapshot#>'{actionablePlan,methodVersion}',
            'asOf',v.snapshot#>'{actionablePlan,asOf}',
            'currentPrice',v.snapshot#>'{actionablePlan,currentPrice}',
            'track',(SELECT jsonb_object_agg(entry.key,entry.value)
              FROM jsonb_array_elements(CASE
                WHEN jsonb_typeof(v.snapshot#>'{actionablePlan,tracks}')='array'
                THEN v.snapshot#>'{actionablePlan,tracks}' ELSE '[]'::jsonb END) selected
              CROSS JOIN LATERAL jsonb_each(selected) entry
              WHERE selected->>'track'=$7 AND entry.key=ANY($10::text[]))
          )=$9::jsonb"""
    return await conn.fetchval(
        """SELECT t.id FROM research.study_tracks t
        JOIN LATERAL(SELECT snapshot FROM research.study_versions v
          WHERE v.tenant_id=t.tenant_id AND v.user_id=t.user_id AND v.track_id=t.id
            AND v.created_at<=$3 ORDER BY version DESC LIMIT 1) v ON TRUE
        WHERE t.tenant_id=$1 AND t.user_id=$2 AND t.created_at<=$3
          AND v.snapshot#>>'{origin,reportRunId}'=$4
          AND v.snapshot#>>'{origin,originType}'=$8
          AND v.snapshot#>>'{actionablePlan,subjectKind}'=$5
          AND v.snapshot#>>'{actionablePlan,subjectId}'=$6
          AND COALESCE(v.snapshot#>>'{actionablePlan,selectedTrack}',v.snapshot#>>'{actionablePlan,track}')=$7
        """
        + current_price_filter
        + " ORDER BY t.created_at,t.id LIMIT 1",
        tenant_id,
        user_id,
        now,
        origin["reportRunId"],
        plan["subjectKind"],
        plan["subjectId"],
        plan.get("selectedTrack", plan.get("track")),
        opposite,
        *(
            (dump(coordinate), list(_LIVE_TRACK_PRICES))
            if coordinate is not None
            else ()
        ),
    )


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
