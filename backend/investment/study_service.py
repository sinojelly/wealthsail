"""Personal frozen studies over existing evidence; all operations are DB-only."""

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation

from backend.errors import NotFoundError, ValidationError_, VersionConflictError
from backend.investment import study_repository as repository
from backend.investment.decision import semantic_hash
from backend.investment.study import (
    freeze_hypothesis,
    evaluate_conditions,
    review_evidence,
)


def body(value):
    return (
        value.model_dump(by_alias=True, mode="json", exclude_none=True)
        if hasattr(value, "model_dump")
        else value
    )


def command(value, key=None):
    digest = semantic_hash(body(value))
    return key or digest, digest


def explanation_texts(rows):
    output = []
    for row in rows:
        value = row.get("summary") or row.get("label") if isinstance(row, dict) else row
        if isinstance(value, str) and value.strip():
            output.append(value[:2000])
    return output[:8]


def meaningful_change(previous, result):
    changed = any(
        item.get("result") in {"TRUE", "FALSE"}
        and item.get("previousResult") in {"TRUE", "FALSE"}
        and item["result"] != item["previousResult"]
        for item in result.get("conditionResults", [])
    )
    return bool(
        previous
        and not result.get("baseline")
        and (changed or result["state"] != previous.get("state"))
    )


async def validate_scope(conn, scope, now):
    from backend.investment.decision_repository import (
        validate_scope as validate,
        resolve_instrument,
    )

    if not scope:
        return {}
    names = {
        "directionId": "direction_id",
        "indexId": "index_id",
        "instrumentId": "instrument_id",
        "fundCode": "fund_code",
        "fundMarket": "fund_market",
    }
    from backend.investment.theme_graph import THEMES

    if scope.get("themeCode") is not None and scope["themeCode"] not in THEMES:
        raise ValidationError_("未知研究主题")
    await validate(
        conn, {names[key]: value for key, value in scope.items() if key in names}, now
    )
    result = dict(scope)
    if result.get("instrumentId") or result.get("fundCode"):
        identity = await resolve_instrument(
            conn,
            result.get("instrumentId"),
            result.get("fundCode"),
            result.get("fundMarket"),
        )
        if identity:
            result["instrumentId"] = str(identity)
    return result


async def load_observations(conn, scope, now):
    """Normalize existing facts without a provider request or private portfolio."""
    from backend.investment.service import analysis, market_context
    from backend.investment.opportunity_service import market_evidence
    from backend.macro import market_repository

    output = []
    context = await market_context(conn, now=now)
    for item in context.get("items", []):
        if item.get("code") not in {"PMI", "CPI_YOY", "PPI_YOY", "M2_YOY"}:
            continue
        verified = item.get("valueVerification") == "VERIFIED" and item.get(
            "state"
        ) not in {"STALE", "CONFLICT", "UNAVAILABLE"}
        output.append(
            {
                "metric": item["code"],
                "value": item.get("value"),
                "unit": "指数点" if item["code"] == "PMI" else item.get("unit"),
                "asOf": item.get("referencePeriod") or str(item.get("asOf") or "")[:7],
                "retrievedAt": item.get("retrievedAt"),
                "availableAt": item.get("retrievedAt"),
                "publishedAt": item.get("publishedAt"),
                "sourceUri": item.get("sourceUri"),
                "verification": "VERIFIED" if verified else "UNVERIFIED",
                "state": "READY" if verified else "UNKNOWN",
                "frequency": "MONTHLY",
            }
        )
    permissions = await market_repository.read_permissions(conn)
    collected = await market_repository.read_collection(conn, now)
    series = market_repository.series_context(
        await market_repository.read_series(conn, now),
        now,
        permissions=permissions,
        source_results=collected.get("sourceResults", {}),
    )
    for item in series:
        if item.get("code") in {"US_T10_NOMINAL", "US_T10_REAL"}:
            output.append(
                {
                    "metric": item["code"],
                    "value": item.get("value"),
                    "unit": item.get("unit"),
                    "asOf": item.get("asOf"),
                    "retrievedAt": item.get("retrievedAt"),
                    "availableAt": item.get("availableAt"),
                    "publishedAt": item.get("publishedAt"),
                    "sourceUri": item.get("sourceUri"),
                    "verification": "VERIFIED"
                    if item.get("state") == "READY"
                    else "UNVERIFIED",
                    "state": item.get("state"),
                    "frequency": item.get("frequency"),
                }
            )
    indices = await conn.fetch(
        "SELECT * FROM reference.research_indices WHERE id IN ('hs300','a500','zz500') ORDER BY id"
    )
    for index in indices:
        result = await analysis(conn, "index", index["id"])
        evidence = market_evidence(index, result)
        verified = index.get("verified_at") is not None and index["verified_at"] <= now
        common = {
            "subjectKey": index["id"],
            "asOf": evidence.get("asOf"),
            "retrievedAt": result.get("retrievedAt"),
            "availableAt": result.get("retrievedAt"),
            "sourceUri": evidence.get("sourceUri"),
            "frequency": "DAILY",
        }
        trend = evidence.get("trend") or {}
        for metric, field in (
            ("INDEX_MA200_GAP", "distanceMA200"),
            ("INDEX_MOMENTUM63", "momentum63"),
        ):
            value = trend.get(field)
            try:
                value = (
                    Decimal(str(value)) * 100
                    if value is not None and not isinstance(value, bool)
                    else None
                )
                if value is not None and not value.is_finite():
                    value = None
            except (InvalidOperation, ValueError):
                value = None
            ready = verified and trend.get("state") == "READY" and value is not None
            output.append(
                {
                    **common,
                    "metric": metric,
                    "value": value,
                    "unit": "%",
                    "state": "READY" if ready else "UNKNOWN",
                    "verification": "VERIFIED" if ready else "UNVERIFIED",
                }
            )
        pe = (evidence.get("valuation") or {}).get("pe") or {}
        ready = (
            verified
            and pe.get("state") in {"READY", "INSUFFICIENT"}
            and bool(pe.get("points"))
        )
        output.append(
            {
                **common,
                "metric": "INDEX_PE",
                "value": pe.get("latest"),
                "unit": "倍",
                "asOf": pe.get("historyEnd") or common["asOf"],
                "sourceUri": pe.get("sourceUri") or common["sourceUri"],
                "state": "READY" if ready else "UNKNOWN",
                "verification": "VERIFIED" if ready else "UNVERIFIED",
            }
        )
    packet = await conn.fetchval(
        "SELECT public_packet FROM research.daily_research_runs WHERE dispatched_at<=$1 ORDER BY dispatched_at DESC,run_id DESC LIMIT 1",
        now,
    )
    packet = repository.decode(packet) if packet else {}
    for item in packet.get("energy", []):
        if item.get("code") not in {"DCOILBRENTEU", "GASDESW"}:
            continue
        unit = {"美元/桶": "USD/barrel", "美元/加仑": "USD/gallon"}.get(
            item.get("unit"), item.get("unit")
        )
        output.append(
            {
                "metric": item["code"],
                "value": item.get("value"),
                "unit": unit,
                "asOf": item.get("asOf"),
                "retrievedAt": item.get("retrievedAt"),
                "availableAt": item.get("retrievedAt"),
                "sourceUri": item.get("sourceUri"),
                "state": item.get("state"),
                "verification": "VERIFIED"
                if item.get("state") == "READY"
                else "UNVERIFIED",
                "frequency": item.get("frequency"),
            }
        )
    return output


async def get_detail(
    conn, tenant_id, user_id, track_id, now, *, limit=50, offset=0, observations=None
):
    detail = await repository.detail_rows(
        conn, tenant_id, user_id, track_id, now, limit=limit, offset=offset
    )
    snapshot = detail["version"]["snapshot"]
    observations = (
        observations
        if observations is not None
        else await load_observations(conn, snapshot.get("scope") or {}, now)
    )
    links = await conn.fetch(
        "SELECT fill,linked_at FROM research.study_trade_links WHERE tenant_id=$1 AND user_id=$2 AND track_id=$3 AND version_id=$4 AND linked_at<=$5 ORDER BY linked_at,id",
        tenant_id,
        user_id,
        track_id,
        detail["version"]["id"],
        now,
    )
    trades = [
        {
            **repository.decode(row["fill"]),
            "accessVerified": True,
            "linkedAt": row["linked_at"].isoformat(),
            "price": repository.decode(row["fill"]).get("unitPrice"),
            "fees": repository.decode(row["fill"]).get("fee"),
        }
        for row in links
    ]
    detail["review"] = review_evidence(
        snapshot, snapshot.get("observations") or [], observations, trades, now=now
    )
    excursion = await conn.fetchrow(
        "SELECT * FROM research.study_excursions WHERE tenant_id=$1 AND user_id=$2 AND track_id=$3 AND version_id=$4 AND updated_at<=$5",
        tenant_id,
        user_id,
        track_id,
        detail["version"]["id"],
        now,
    )
    detail["excursions"] = (
        {
            "mfePct": str(excursion["mfe_pct"]),
            "maePct": str(excursion["mae_pct"]),
            "baseAsOf": excursion["base_as_of"].isoformat(),
            "lastAsOf": excursion["last_as_of"].isoformat(),
            "sampleCount": excursion["sample_count"],
            "priceBasis": excursion["price_basis"],
            "unit": excursion["unit"],
        }
        if excursion
        else None
    )
    return detail


async def create_study(conn, tenant_id, user_id, request, now, *, idempotency_key=None):
    data = body(request)
    scope = await validate_scope(conn, data.get("scope") or {}, now)
    if data["originType"] == "DAILY_DIRECTION":
        origin = await repository.daily_direction_origin(
            conn,
            data["reportRunId"],
            data["valueDirectionKey"],
            data["subjectKind"],
            data["subjectId"],
            data["planTrack"],
            now,
        )
        plan = origin["actionablePlan"]
        scope = await validate_scope(
            conn, await repository.direction_plan_scope(conn, plan), now
        )
        origin_key = semantic_hash(
            {
                "originType": "DAILY_DIRECTION",
                "valueDirectionKey": data["valueDirectionKey"],
                "subjectKind": plan["subjectKind"],
                "subjectId": plan["subjectId"],
                "market": plan.get("market"),
                "planTrack": data["planTrack"],
            }
        )
    elif data["originType"] == "DAILY_EVENT":
        origin = await repository.daily_origin(
            conn, data["reportRunId"], data["eventId"], now
        )
        origin_key = semantic_hash(
            {
                "originType": "DAILY_EVENT",
                "reportRunId": data["reportRunId"],
                "eventId": data["eventId"],
                "scope": scope,
            }
        )
    else:
        from backend.investment.opportunity_service import get_context

        context = await get_context(
            conn,
            tenant_id,
            user_id,
            direction_id=scope.get("directionId"),
            index_id=scope.get("indexId"),
            instrument_id=scope.get("instrumentId"),
            fund_code=scope.get("fundCode"),
            fund_market=scope.get("fundMarket"),
            now=now,
            theme_code=scope.get("themeCode"),
        )
        model = context.get("directionModel") or {}
        selected_plan = None
        if data.get("planTrack"):
            selected_plan = (context.get("decision") or {}).get("actionableZones")
            if not isinstance(selected_plan, dict) or not any(
                row.get("track") == data["planTrack"]
                for row in selected_plan.get("tracks", [])
            ):
                raise ValidationError_("当前标的没有所选轨道的实际研究计划")
            fund_scope = bool(scope.get("instrumentId") or scope.get("fundCode"))
            expected_id = (
                (scope.get("instrumentId") or scope.get("fundCode"))
                if fund_scope
                else scope.get("indexId")
            )
            expected_kinds = {"fund", "nav"} if fund_scope else {"index"}
            if (
                selected_plan.get("subjectId") != str(expected_id)
                or selected_plan.get("subjectKind") not in expected_kinds
            ):
                raise ValidationError_("研究计划与当前标的不匹配")
            selected_plan = {
                **deepcopy(selected_plan),
                "selectedTrack": data["planTrack"],
            }
        origin = {
            "originType": "SELECTION",
            "selection": scope,
            "title": (model.get("label") or "当前方向") + "研究",
            "summary": model.get("summary"),
            "supports": explanation_texts(model.get("supports") or []),
            "counterFactors": explanation_texts(model.get("counterFactors") or []),
            "watchFor": model.get("missingEvidence") or [],
            "reportAsOf": now.isoformat(),
            **({"actionablePlan": selected_plan} if selected_plan else {}),
        }
        origin_key = semantic_hash(
            {
                "originType": "SELECTION",
                "scope": scope,
                **({"planTrack": data["planTrack"]} if data.get("planTrack") else {}),
            }
        )
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        f"study-create:{tenant_id}:{user_id}:{origin_key}",
    )
    existing = await conn.fetchval(
        "SELECT id FROM research.study_tracks WHERE tenant_id=$1 AND user_id=$2 AND origin_key=$3",
        tenant_id,
        user_id,
        origin_key,
    )
    if existing:
        return await get_detail(conn, tenant_id, user_id, existing, now)
    observations = await load_observations(conn, scope, now)
    origin["observations"] = observations
    snapshot = freeze_hypothesis(
        origin, now=now, horizon_days=data.get("horizonDays", 30), scope=scope
    )
    track = dict(
        await conn.fetchrow(
            """INSERT INTO research.study_tracks(tenant_id,user_id,origin_key,title,scope,created_at,updated_at)
        VALUES($1,$2,$3,$4,$5::jsonb,$6,$6) RETURNING *""",
            tenant_id,
            user_id,
            origin_key,
            snapshot["title"],
            repository.dump(scope),
            now,
        )
    )
    key, digest = command(data, idempotency_key)
    version = await repository.add_version(
        conn, track, snapshot, 1, "加入跟踪，冻结原始研究", key, digest, now
    )
    await repository.add_note(
        conn,
        track,
        version["id"],
        "LIFECYCLE",
        {"operation": "CREATED", "origin": snapshot["origin"]},
        "created",
        digest,
        now,
    )
    result = evaluate_conditions(snapshot, None, observations, now=now)
    result["meaningfulChange"] = False
    await repository.add_check(conn, track, dict(version), result, now)
    return await get_detail(
        conn, tenant_id, user_id, track["id"], now, observations=observations
    )


async def append_version(
    conn, tenant_id, user_id, track_id, request, now, *, idempotency_key=None
):
    data = body(request)
    track = await repository.lock_track(conn, tenant_id, user_id, track_id)
    key, digest = command(data, idempotency_key)
    if await repository.command_replayed(
        conn, "study_versions", tenant_id, user_id, track_id, key, digest
    ):
        return await get_detail(conn, tenant_id, user_id, track_id, now)
    if track["generation"] != data["expectedGeneration"]:
        raise VersionConflictError("研究主题已更新，请刷新后重试")
    previous = await repository.latest_version(conn, tenant_id, user_id, track_id)
    old = previous["snapshot"]
    origin = {
        **deepcopy(old),
        **deepcopy(old.get("origin") or {}),
        "summary": data.get("thesis", old.get("thesis")),
    }
    for field in (
        "conditions",
        "transmission",
        "counterFactors",
        "watchFor",
        "invalidationConditions",
    ):
        if field in data:
            origin[field] = data[field]
    observations = await load_observations(conn, old.get("scope") or {}, now)
    origin["observations"] = observations
    horizon = data.get("horizonDays") or max(
        1,
        min(
            365,
            (
                date.fromisoformat(old["expiresOn"])
                - date.fromisoformat(old["createdAt"][:10])
            ).days,
        ),
    )
    try:
        snapshot = freeze_hypothesis(
            origin, now=now, horizon_days=horizon, scope=old.get("scope")
        )
    except ValueError as exc:
        raise ValidationError_("研究条件或观察期限无效") from exc
    track = await repository.advance(conn, track, now)
    version = await repository.add_version(
        conn, track, snapshot, previous["version"] + 1, data["reason"], key, digest, now
    )
    await repository.add_note(
        conn,
        track,
        version["id"],
        "LIFECYCLE",
        {"operation": "VERSION_CREATED", "reason": data["reason"]},
        f"version:{track['generation']}",
        digest,
        now,
    )
    if track["enabled"]:
        result = evaluate_conditions(snapshot, None, observations, now=now)
        result["meaningfulChange"] = False
        await repository.add_check(conn, track, dict(version), result, now)
    return await get_detail(
        conn, tenant_id, user_id, track_id, now, observations=observations
    )


async def set_status(
    conn, tenant_id, user_id, track_id, request, now, *, idempotency_key=None
):
    data = body(request)
    track = await repository.lock_track(conn, tenant_id, user_id, track_id)
    key, digest = command({"operation": "STATUS", **data}, idempotency_key)
    if await repository.command_replayed(
        conn, "study_notes", tenant_id, user_id, track_id, key, digest
    ):
        return await get_detail(conn, tenant_id, user_id, track_id, now)
    if track["generation"] != data["expectedGeneration"]:
        raise VersionConflictError("研究主题已更新，请刷新后重试")
    if track["enabled"] != data["enabled"]:
        track = await repository.advance(conn, track, now, enabled=data["enabled"])
    version = await repository.latest_version(conn, tenant_id, user_id, track_id)
    await repository.add_note(
        conn,
        track,
        version["id"],
        "LIFECYCLE",
        {"operation": "RESUMED" if track["enabled"] else "STOPPED"},
        key,
        digest,
        now,
    )
    return await get_detail(conn, tenant_id, user_id, track_id, now)


async def check_study(conn, tenant_id, user_id, track_id, expected_generation, now):
    track = await repository.lock_track(
        conn, tenant_id, user_id, track_id, expected_generation
    )
    if not track["enabled"]:
        raise VersionConflictError("研究主题已停止")
    version = await repository.latest_version(conn, tenant_id, user_id, track_id)
    previous = await repository.latest_check(conn, track, version["id"], now)
    observations = await load_observations(
        conn, version["snapshot"].get("scope") or {}, now
    )
    result = evaluate_conditions(
        version["snapshot"],
        previous["result"] if previous else None,
        observations,
        now=now,
    )
    prior = previous["result"] if previous else {}
    result["meaningfulChange"] = meaningful_change(prior, result)
    saved = await repository.add_check(conn, track, version, result, now)
    return {"stored": saved is not None, "result": result}


async def append_note(
    conn, tenant_id, user_id, track_id, request, now, *, idempotency_key=None
):
    data = body(request)
    track = await repository.lock_track(conn, tenant_id, user_id, track_id)
    key, digest = command(data, idempotency_key)
    if await repository.command_replayed(
        conn, "study_notes", tenant_id, user_id, track_id, key, digest
    ):
        return await get_detail(conn, tenant_id, user_id, track_id, now)
    if track["generation"] != data["expectedGeneration"]:
        raise VersionConflictError("研究主题已更新，请刷新后重试")
    version = (
        await repository.version_by_id(
            conn, tenant_id, user_id, track_id, data["versionId"]
        )
        if data.get("versionId") and data["kind"] in {"DECISION", "REVIEW"}
        else await repository.latest_version(conn, tenant_id, user_id, track_id)
    )
    payload = {
        key: value
        for key, value in data.items()
        if key not in {"expectedGeneration", "kind", "versionId"}
    }
    payload["origin"] = "USER_ASSESSMENT"
    if data["kind"] == "ACK":
        if data.get("checkId"):
            row = await conn.fetchrow(
                "SELECT id,evaluated_at FROM research.study_checks WHERE tenant_id=$1 AND user_id=$2 AND track_id=$3 AND id=$4 AND evaluated_at<=$5",
                tenant_id,
                user_id,
                track_id,
                data["checkId"],
                now,
            )
            if row is None:
                raise NotFoundError("核对记录不存在")
        else:
            row = await repository.latest_check(conn, track, version["id"], now)
        if row:
            payload.update(
                throughCheckId=str(row["id"]),
                throughCheckedAt=row["evaluated_at"].isoformat(),
            )
    saved_note = await repository.add_note(
        conn, track, version["id"], data["kind"], payload, key, digest, now
    )
    if saved_note is not None and data["kind"] == "REVIEW":
        from backend.investment.strategy_memory import (
            refresh_strategy_memory,
            theme_for_snapshot,
        )

        await refresh_strategy_memory(
            conn, tenant_id, user_id, theme_for_snapshot(version["snapshot"]), now
        )
    return await get_detail(conn, tenant_id, user_id, track_id, now)


async def eligible_trades(conn, tenant_id, user_id, track_id, now):
    version = await repository.latest_version(conn, tenant_id, user_id, track_id)
    scope = version["snapshot"].get("scope") or {}
    rows = (
        []
        if scope.get("fundCode") and not scope.get("instrumentId")
        else await repository.eligible_trades(conn, tenant_id, user_id, scope, now)
    )
    return {"items": rows, "total": len(rows)}


async def link_trade(conn, tenant_id, user_id, track_id, request, now):
    data = body(request)
    track = await repository.lock_track(
        conn, tenant_id, user_id, track_id, data["expectedGeneration"]
    )
    version = (
        await repository.version_by_id(
            conn, tenant_id, user_id, track_id, data["versionId"]
        )
        if data.get("versionId")
        else await repository.latest_version(conn, tenant_id, user_id, track_id)
    )
    scope = version["snapshot"].get("scope") or {}
    rows = (
        []
        if scope.get("fundCode") and not scope.get("instrumentId")
        else await repository.eligible_trades(conn, tenant_id, user_id, scope, now)
    )
    fill = next((row for row in rows if row["fillId"] == str(data["fillId"])), None)
    if fill is None:
        raise NotFoundError("不存在符合研究标的和本人账户范围的已确认成交")
    saved_link = await repository.add_trade_link(
        conn, track, version, fill, data["reason"], now
    )
    if saved_link is not None:
        from backend.investment.strategy_memory import (
            refresh_memory_for_trade,
            theme_for_snapshot,
        )

        await refresh_memory_for_trade(
            conn,
            tenant_id,
            user_id,
            data["fillId"],
            theme_for_snapshot(version["snapshot"]),
            now,
        )
    return await get_detail(conn, tenant_id, user_id, track_id, now)
