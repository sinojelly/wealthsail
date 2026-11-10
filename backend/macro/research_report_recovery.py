"""Replay one saved report; preserve its original failure in a recovery audit."""

from datetime import datetime
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID

from backend.macro.research_acceptance_replay import run_offline_sanitization

VERSION = "research-sanitize-replay-v1"
MATERIAL_LIMIT = 48 * 1024 * 1024
READ_RUN = """SELECT r.run_id,r.public_packet,md5(r.public_packet::text) AS packet_digest,
 t.status,t.recorded_at,t.failure_stage,t.error_class
 FROM research.daily_research_runs r JOIN research.daily_research_results t USING(run_id)
 WHERE r.run_id=$1"""


def _object(value):
    return json.loads(value) if isinstance(value, str) else value


def report_recovery_times(report, run_id, recorded_at):
    """A restored report's original completion is distinct from publication."""
    metadata = report.get("researchRecovery") if isinstance(report, dict) else None
    if not isinstance(metadata, dict) or metadata.get("version") != VERSION or metadata.get("sourceRunId") != str(run_id):
        return None
    try:
        original = datetime.fromisoformat(metadata["originalCompletedAt"])
        replayed = datetime.fromisoformat(metadata["replayedAt"])
        if original.utcoffset() is None or replayed.utcoffset() is None or original > replayed or replayed > recorded_at:
            return None
    except (KeyError, ValueError, TypeError):
        return None
    return {**metadata, "originalCompletedAt": original, "replayedAt": replayed}


def read_recovery_materials(settings, record):
    identity = str(UUID(str(record["run_id"])))
    directory = str(getattr(settings, "daily_research_audit_dir", "") or "").strip()
    if not directory:
        raise ValueError("AUDIT_DIRECTORY_REQUIRED")
    path = Path(directory) / f"{identity}.json"
    with path.open("rb") as handle:
        content = handle.read(MATERIAL_LIMIT + 1)
    if len(content) > MATERIAL_LIMIT:
        raise ValueError("AUDIT_SIZE_LIMIT")
    audit = json.loads(content)
    if audit.get("runId") != identity:
        raise ValueError("AUDIT_RUN_MISMATCH")
    raw, packet = _object(audit["raw"]), _object(record["public_packet"])
    if not isinstance(packet, dict):
        raise ValueError("PUBLIC_PACKET_INVALID")
    at = datetime.fromisoformat(audit["completedAt"])
    if at.utcoffset() is None:
        raise ValueError("AUDIT_CLOCK_REQUIRED")
    raw_bytes = json.dumps(raw, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    return {
        "researchId": identity, "packet": packet, "raw": raw,
        "sourceChecks": _object(audit.get("sourceChecks")) or {},
        "validatedAt": at.isoformat(), "rawSha256": sha256(raw_bytes).hexdigest(),
        "packetDigest": record["packet_digest"],
    }


async def enrich_recovery_payload(conn, payload, materials, now):
    """Attach real local fund matches and price references without any fetch/AI."""
    from backend.investment.live_fund_plans import refresh_report_fund_plans
    from backend.investment.research_fund_references import resolve_report_fund_references

    try:
        async with conn.transaction():
            enriched = await resolve_report_fund_references(conn, payload, materials["packet"], now)
            return await refresh_report_fund_plans(
                conn, enriched, now, packet=materials["packet"], include_series=True,
            )
    except Exception as error:  # noqa: BLE001 -- optional prices cannot discard saved reasoning
        payload.setdefault("validationWarnings", []).append({
            "path": "fundReferences", "code": "REFERENCE_REFRESH_PENDING",
            "message": f"真实基金价位将于页面读取时补齐（{type(error).__name__}）。",
        })
        return payload


async def refresh_recovery_nav(conn, payload, now):
    """Fill only missing public NAV references before explicit publication."""
    from backend.funds.nav_history import local, valid_points
    from backend.investment.live_fund_plans import _code, _plans
    from backend.jobs.fund_nav_sync import fetch_on_demand_nav_history

    needed = set()
    for plan in _plans(payload):
        code = _code(plan)
        if code and not any(
            track.get("entryZoneMin") is not None and track.get("entryZoneMax") is not None
            for track in plan.get("tracks", []) if isinstance(track, dict)
        ):
            needed.add(code)
    if not needed:
        return {"requested": 0, "persisted": 0, "failed": 0}
    rows = await conn.fetch(
        """SELECT fund_code FROM reference.research_fund_catalog
        WHERE fund_code=ANY($1::text[]) AND market='FUND_CN'
          AND trade_form IN ('FUND','LOF') AND currency='CNY'
          AND fund_name !~ '美元|港币|港元|外币|现汇|现钞'
        GROUP BY fund_code HAVING count(*)=1""", sorted(needed),
    )
    codes = sorted({row["fund_code"] for row in rows})
    results = await asyncio.gather(
        *(fetch_on_demand_nav_history(code, now) for code in codes), return_exceptions=True,
    )
    persisted = 0
    for code, history in zip(codes, results):
        try:
            if isinstance(history, BaseException):
                continue
            retrieved = local(datetime.fromisoformat(history["retrievedAt"]))
            if not valid_points(history, code, retrieved):
                continue
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO market.fund_nav_chart_histories
                    (fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$2,$3::jsonb)""",
                    code, retrieved, json.dumps(history, ensure_ascii=False, allow_nan=False),
                )
            persisted += 1
        except Exception:  # noqa: BLE001 -- one provider/cache failure cannot discard research
            continue
    return {"requested": len(needed), "persisted": persisted, "failed": len(needed) - persisted}


async def recover_report(conn, settings, run_id, *, now, publish=False, refresh_nav=False):
    identity = UUID(str(run_id))
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("RECOVERY_CLOCK_REQUIRED")
    record = await conn.fetchrow(READ_RUN, identity)
    if record is None:
        raise ValueError("RESEARCH_RUN_NOT_FOUND")
    materials = read_recovery_materials(settings, record)
    payload = await run_offline_sanitization(materials)
    payload = await enrich_recovery_payload(conn, payload, materials, now)
    nav_refresh = None
    if publish and refresh_nav:
        from backend.dependencies import now_shanghai

        try:
            nav_refresh = await refresh_recovery_nav(conn, payload, now)
            now = max(now, now_shanghai())
            payload = await enrich_recovery_payload(conn, payload, materials, now)
        except Exception as error:  # noqa: BLE001 -- source refresh is optional, saved insights are durable
            nav_refresh = {"state": "RETRY_PENDING", "errorClass": type(error).__name__}
    payload["researchRecovery"] = {
        "version": VERSION, "sourceRunId": str(identity), "modelCalls": 0,
        "originalStatus": record["status"],
        "originalCompletedAt": record["recorded_at"].isoformat(),
        "replayedAt": now.isoformat(), "rawSha256": materials["rawSha256"],
    }
    from backend.investment.live_fund_plans import _code, _plans

    fund_codes, priced_codes, chart_codes, price_gaps = set(), set(), set(), {}
    for plan in _plans(payload):
        if code := _code(plan):
            fund_codes.add(code)
            if any(
                all(track.get(key) is not None for key in (
                    "entryZoneMin", "entryZoneMax", "takeProfitT1", "takeProfitT2", "stopLossPrice",
                )) for track in plan.get("tracks", []) if isinstance(track, dict)
            ):
                priced_codes.add(code)
            if isinstance(plan.get("series"), dict) and plan["series"].get("points"):
                chart_codes.add(code)
            if code not in priced_codes:
                price_gaps[code] = (plan.get("priceRefresh") or {}).get("reason", "PRICE_REFERENCE_PENDING")
    gap_counts = {}
    for code in fund_codes - priced_codes:
        reason = price_gaps.get(code, "PRICE_REFERENCE_PENDING")
        gap_counts[reason] = gap_counts.get(reason, 0) + 1
    result = {
        "runId": str(identity), "operation": "PUBLISH" if publish else "PREVIEW",
        "published": False, "modelCalls": 0,
        "directionCount": len(payload.get("valueDirections", [])),
        "topicCount": len(payload.get("researchTopics", payload.get("topicCatalog", []))),
        "fundPlanCount": sum(len(row.get("plans", [])) for row in payload.get("valueDirections", [])),
        "uniqueFundCount": len(fund_codes),
        "pricedFundCount": len(priced_codes),
        "chartFundCount": len(chart_codes),
        "priceGapCounts": gap_counts,
        "warningCount": len(payload.get("validationWarnings", [])),
        "rawSha256": materials["rawSha256"],
        "originalAsOf": payload.get("asOf"),
    }
    if nav_refresh is not None:
        result["navRefresh"] = nav_refresh
    if publish:
        published = await conn.fetchrow(
            "SELECT * FROM research.publish_sanitized_daily_report($1,$2::jsonb,$3,$4,$5)",
            identity, json.dumps(payload, ensure_ascii=False, allow_nan=False),
            materials["packetDigest"], materials["rawSha256"], now,
        )
        result.update(
            published=bool(published["published"]),
            alreadyPublished=bool(published["already_published"]),
        )
    return result
