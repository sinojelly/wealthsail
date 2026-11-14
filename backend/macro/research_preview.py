"""Read-only historical simulation from original, integrity-checked evidence.

No draft, replay admission, lock, publication, collector or live price lookup is
used here. The original validation clock remains the only evidence clock.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from uuid import UUID

from backend.errors import DataUnavailableError, NotFoundError, ValidationError_
from backend.macro import research_acceptance_repository as acceptance
from backend.macro.research_acceptance_replay import (
    OfflineReplayError,
    check_frozen_contract,
    run_offline_repair,
)
from backend.macro.research_frozen_bundle import (
    FrozenBundleError,
    MATERIAL_LIMITS,
    decode_frozen_bundle,
)

VERSION = "research-preview-v1"
MAX_FUNDS = 256
OUTPUT_LIMIT = 8 * 1024**2
_RUN_SELECT = """SELECT r.run_id,r.dispatched_at,r.source_batch_id,
 t.status,t.recorded_at,t.error_class,
 COALESCE(v.validated_at,(q.safe_summary->>'validatedAt')::timestamptz) AS validated_at
 FROM research.daily_research_runs r
 JOIN research.daily_research_results t ON t.run_id=r.run_id
 LEFT JOIN research.research_validation_records v ON v.run_id=r.run_id AND v.kind='INITIAL'
 LEFT JOIN research.research_validation_archive_queue q ON q.run_id=r.run_id"""
_PERMISSIONS = {
    "investmentEligible",
    "orderPriceAvailable",
    "entryReady",
    "preferredEntryEligible",
    "actionable",
    "eligible",
    "valueEligible",
    "canSubscribe",
    "canCreatePlan",
    "canGenerate",
    "canPublish",
}


def _stamp(value):
    return acceptance._aware(value)


def _readonly(value):
    if isinstance(value, dict):
        return {
            key: False if key in _PERMISSIONS else _readonly(row)
            for key, row in value.items()
        }
    if isinstance(value, list):
        return [_readonly(row) for row in value]
    return deepcopy(value)


def _run_clock(record, bundle, run_id, now):
    if record is None or record.get("status") not in {"READY", "FAILED"}:
        raise NotFoundError("该研究尚无可预览的已完成记录")
    try:
        if str(UUID(str(record["run_id"]))) != str(run_id) or str(
            UUID(str(bundle["researchId"]))
        ) != str(run_id):
            raise ValueError
        validated = _stamp(bundle["validatedAt"])
        if not (
            _stamp(record["dispatched_at"])
            <= validated
            <= _stamp(record["recorded_at"])
            <= _stamp(now)
        ):
            raise ValueError
        if validated != _stamp(record["validated_at"]):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValidationError_("原始研究身份或冻结校验时刻不一致") from None
    return validated


def _metadata_complete(bundle):
    items = bundle.get("items")
    if (
        bundle.get("complete") is not True
        or not isinstance(items, list)
        or len(items) != 3
    ):
        return False
    seen = set()
    for row in items:
        if not isinstance(row, dict):
            return False
        kind, size, digest = row.get("kind"), row.get("byteLength"), row.get("sha256")
        if (
            not isinstance(kind, str)
            or kind not in MATERIAL_LIMITS
            or kind in seen
            or row.get("omissionReason") is not None
            or type(size) is not int
            or not 0 < size <= MATERIAL_LIMITS[kind]
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            return False
        seen.add(kind)
    return seen == set(MATERIAL_LIMITS)


async def list_preview_runs(conn, *, limit=30, now):
    """Cheap capability metadata; the GET separately verifies every body hash."""
    rows = await conn.fetch(
        _RUN_SELECT
        + " WHERE (v.run_id IS NOT NULL OR q.run_id IS NOT NULL) AND t.status IN ('READY','FAILED') AND r.dispatched_at<=$1 AND t.recorded_at<=$1 ORDER BY t.recorded_at DESC,r.run_id DESC LIMIT $2",
        _stamp(now),
        max(1, min(int(limit), 30)),
    )
    result = []
    for record in rows:
        missing = []
        try:
            bundle = await acceptance.read_artifacts_admin(
                conn, record["run_id"], include_content=False
            )
            _run_clock(record, bundle, record["run_id"], now)
            if not _metadata_complete(bundle):
                missing.append("FROZEN_MATERIALS_INCOMPLETE")
        except NotFoundError:
            # The existing artifact RPC is the tenant authorization boundary.
            # Do not reveal IDs or failure timing for an invisible run.
            continue
        except (ValidationError_, ValueError, TypeError, KeyError):
            missing.append("FROZEN_MATERIALS_UNAVAILABLE")
        result.append(
            {
                "runId": str(record["run_id"]),
                "status": record["status"],
                "startedAt": _stamp(record["dispatched_at"]).isoformat(),
                "finishedAt": _stamp(record["recorded_at"]).isoformat(),
                "errorClass": record.get("error_class"),
                "canPreview": not missing,
                "previewMissingReasons": missing,
            }
        )
    return result


def _source_binding(frozen):
    from backend.macro.research_topics import frozen_manifest

    manifest = frozen_manifest(frozen.packet)
    binding = frozen.source_checks.get("__wealthsail_execution_v1")
    if binding is not None:
        if (
            not isinstance(binding, dict)
            or binding.get("researchId") != frozen.research_id
            or _stamp(binding.get("validatedAt")) != frozen.validated_at
            or (
                manifest is not None
                and (
                    binding.get("manifestHash") != manifest["manifestHash"]
                    or binding.get("inputAsOf") != manifest["asOf"]
                )
            )
        ):
            raise ValidationError_("原始研究执行凭证与冻结材料身份不一致")


async def _histories(conn, frozen, record):
    packet = frozen.packet
    direct = packet.get("fundHistories")
    if isinstance(direct, list):
        return direct
    alpha = packet.get("alphaPublic")
    direct = alpha.get("fundHistories") if isinstance(alpha, dict) else None
    if isinstance(direct, list) and direct:
        return direct
    try:
        batch = UUID(str(packet.get("sourceBatchId")))
        if batch != UUID(str(record.get("source_batch_id"))):
            return []
    except ValueError:
        return []
    row = await conn.fetchrow(
        "SELECT id,status,completed_at,payload FROM market.alpha_public_batches WHERE id=$1",
        batch,
    )
    if (
        row is None
        or row.get("id") != batch
        or row.get("status") not in {"READY", "PARTIAL"}
        or row.get("completed_at") is None
        or _stamp(row["completed_at"]) > _stamp(packet["asOf"])
    ):
        return []
    payload = acceptance._object(row["payload"])
    return (
        payload.get("fundHistories")
        if isinstance(payload.get("fundHistories"), list)
        else []
    )


def _inventory(raw, packet):
    from backend.macro.industry_contract import same_fund_identity
    from backend.macro.research_mentions import extract_mentioned_funds

    universe = [
        row for row in packet.get("discoveryUniverse", []) if isinstance(row, dict)
    ]
    anchors = [
        row
        for row in packet.get("discoveryActionableZones", [])
        if isinstance(row, dict)
    ]
    funds = []

    def add(proposal, direction_key, *, mention_location=None):
        if not isinstance(proposal, dict):
            return
        kind, subject, code = (
            proposal.get("subjectKind"),
            proposal.get("subjectId"),
            proposal.get("fundCode"),
        )
        if not isinstance(kind, str) or kind not in {"fund", "nav"}:
            if isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code):
                kind, subject = "nav", code
            else:
                return
        if not isinstance(subject, str) or not 1 <= len(subject) <= 200:
            return
        identity = {"subjectKind": kind, "subjectId": subject}
        if isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code):
            identity["fundCode"] = code
        elif kind == "nav" and re.fullmatch(r"[0-9]{6}", subject):
            identity["fundCode"] = subject
        matches = [
            row for row in [*universe, *anchors] if same_fund_identity(identity, row)
        ]
        codes = {
            row.get("fundCode")
            for row in matches
            if isinstance(row.get("fundCode"), str)
        }
        if len(codes) == 1:
            identity["fundCode"] = next(iter(codes))
        previous = next(
            (row for row in funds if same_fund_identity(row, identity)), None
        )
        if previous is None:
            if len(funds) >= MAX_FUNDS:
                raise ValidationError_("原报告基金清单超出只读预览范围")
            previous = {
                **identity,
                "directionKeys": [],
                "originalProposals": [],
                "mentionLocations": [],
                "_identityConflict": len(codes) > 1,
            }
            funds.append(previous)
        if direction_key and direction_key not in previous["directionKeys"]:
            previous["directionKeys"].append(direction_key)
        if mention_location is not None:
            if mention_location not in previous["mentionLocations"]:
                previous["mentionLocations"].append(mention_location)
            return
        reference = {"directionKey": direction_key}
        for field in ("track", "note"):
            if isinstance(proposal.get(field), str):
                reference[field] = proposal[field][:600]
        if reference not in previous["originalProposals"]:
            previous["originalProposals"].append(reference)

    def walk(value, direction_key=None):
        if isinstance(value, dict):
            kind = value.get("subjectKind")
            if (
                isinstance(kind, str) and kind in {"fund", "nav"}
            ) or "fundCode" in value:
                add(value, direction_key)
            for key, row in value.items():
                if key in {"quantitativeFundCodes", "fundCodes"} and isinstance(
                    row, list
                ):
                    for code in row:
                        add({"fundCode": code}, direction_key)
                elif isinstance(row, (dict, list)):
                    walk(row, direction_key)
        elif isinstance(value, list):
            for row in value:
                walk(row, direction_key)

    directions = raw.get("valueDirections")
    if isinstance(directions, list):
        for direction in directions:
            if isinstance(direction, dict):
                walk(
                    direction,
                    direction.get("key")
                    if isinstance(direction.get("key"), str)
                    else None,
                )
    walk({key: row for key, row in raw.items() if key != "valueDirections"})
    for mention in extract_mentioned_funds(raw, packet):
        for direction_key in mention["directionKeys"] or [None]:
            for location in mention["mentionLocations"]:
                add(mention, direction_key, mention_location=location)
    return funds, anchors, universe


def _analysis(history, anchor, at):
    from backend.funds.nav_history import full_history_proven, valid_points
    from backend.investment.theme_graph import _disclosed_nav_quality
    from backend.investment.live_fund_plans import _safe_history

    if (
        anchor.get("priceBasis") != "UNIT_NAV"
        or anchor.get("market") not in (None, "FUND_CN")
        or anchor.get("currency", "CNY") != "CNY"
        or not isinstance(anchor.get("unit"), str)
        or not isinstance(history.get("unit", "CNY"), str)
        or not _safe_history(history)
        or not _safe_history({"unit": anchor["unit"], "label": anchor.get("label")})
    ):
        return None

    points = valid_points(history, anchor.get("fundCode"), at)
    if not points:
        return None
    complete = isinstance(history.get("historyProof"), dict) and full_history_proven(
        history, points, at
    )
    complete = complete or _disclosed_nav_quality(history, points, at)[0] == "READY"
    try:
        if (
            not complete
            or history.get("asOf") != points[-1]["date"]
            or str(anchor["asOf"])[:10] != points[-1]["date"]
            or Decimal(str(anchor["currentPrice"])) != Decimal(points[-1]["close"])
        ):
            return None
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    values = [float(row["close"]) for row in points]
    if not all(math.isfinite(value) for value in values):
        return None
    return {
        "state": "READY",
        "score": None,
        "seriesKind": "UNIT_NAV",
        "asOf": anchor["asOf"],
        "source": history["sourceUri"],
        "retrievedAt": history["retrievedAt"],
        "indicators": deepcopy(anchor.get("indicators", {})),
        "reasons": [],
        "points": [
            {"date": row["date"], "close": value} for row, value in zip(points, values)
        ],
    }


def _fund_views(raw, frozen, report, histories):
    from backend.macro.daily_research import _public_actionable_plan
    from backend.macro.industry_contract import same_fund_identity

    funds, anchors, universe = _inventory(raw, frozen.packet)
    directions = {row["key"]: row for row in (report or {}).get("valueDirections", [])}
    result = []
    for identity in funds:
        missing = []
        matching = [row for row in anchors if same_fund_identity(identity, row)]
        anchor = None
        if identity["_identityConflict"] or len(matching) > 1:
            missing.append("FROZEN_FUND_IDENTITY_CONFLICT")
        elif len(matching) == 1:
            original = matching[0]
            anchor = _public_actionable_plan(
                original,
                original["subjectId"],
                frozen.validated_at,
                subject_kind=original["subjectKind"],
            )
            if anchor is not None:
                for key in ("fundCode", "market", "discoverySector"):
                    if isinstance(original.get(key), str):
                        anchor[key] = original[key]
                if isinstance(anchor.get("industryCycle"), dict):
                    anchor["industryCycle"] = acceptance._cycle_projection(
                        anchor["industryCycle"]
                    )
                anchor = _readonly(anchor)
                anchor.update(readOnly=True, investmentEligible=False)
        if anchor is None:
            missing.append("FROZEN_ANCHOR_MISSING")
        elif not anchor.get("tracks"):
            missing.append("FROZEN_TRACKS_MISSING")
        metadata = next(
            (row for row in universe if same_fund_identity(identity, row)), {}
        )
        code = identity.get("fundCode")
        candidates = [
            row
            for row in histories
            if isinstance(row, dict) and row.get("fundCode") == code
        ]
        analysis = None
        if anchor is not None and len(candidates) == 1:
            analysis = _analysis(candidates[0], anchor, _stamp(frozen.packet["asOf"]))
        if analysis is None:
            missing.append(
                "FROZEN_NAV_HISTORY_UNVERIFIED"
                if candidates
                else "FROZEN_NAV_HISTORY_MISSING"
            )
        reasons = sorted(
            {
                reason
                for key in identity["directionKeys"]
                for reason in directions.get(key, {}).get(
                    "formalEligibilityReasons", []
                )
            }
        )
        result.append(
            {
                "key": hashlib.sha256(
                    acceptance._encode(
                        {
                            key: identity.get(key)
                            for key in ("subjectKind", "subjectId", "fundCode")
                        }
                    ).encode()
                ).hexdigest()[:24],
                "subjectKind": identity["subjectKind"],
                "subjectId": identity["subjectId"],
                "fundCode": code,
                "market": metadata.get("market") or (anchor or {}).get("market"),
                "label": (anchor or {}).get("label")
                or metadata.get("label")
                or code
                or identity["subjectId"],
                "directionKeys": identity["directionKeys"],
                "originalProposals": identity["originalProposals"],
                "mentionLocations": identity["mentionLocations"],
                "investmentEligible": False,
                "formalEligibilityReasons": reasons,
                "anchor": anchor,
                "analysis": analysis,
                "missingReasons": missing,
            }
        )
    return result


def _report_view(value, packet=None, cutoff=None):
    from backend.investment.opportunity_fund_matching import annotate_topic_primary_funds

    formal = value.get("formalReport")
    if isinstance(formal, dict):
        allowed = {
            "methodVersion",
            "asOf",
            "summary",
            "macroCycle",
            "cycleInsights",
            "coverage",
            "sources",
            "events",
            "upcoming",
            "energy",
            "valueDirections",
            "industryCycleReviews",
            "industryResearchCoverage",
            "topicCatalog",
            "topicRegistry",
            "mentionedFunds",
        }
        report = {key: _readonly(row) for key, row in formal.items() if key in allowed}
    else:
        report = acceptance._accepted(
            (value.get("originalOutcome") or {}).get("accepted")
        )
        report.update(
            summary="原报告未完整通过校验，以下保留已核验的只读研究内容。",
            coverage=[],
            energy=[],
        )
    if packet is not None and cutoff is not None:
        from backend.macro.cycle_insights import project_cycle_insights

        # Read representative frozen prices into the descriptive cycle stage
        # before removing all actionable plans from historical preview.
        report = project_cycle_insights(report, packet, cutoff)
    report["actionablePlans"] = []
    for direction in report.get("valueDirections", []):
        direction.update(
            signal="AVOID" if direction.get("signal") == "AVOID" else "WATCH",
            qualificationStatus="OBSERVATION_ONLY",
            investmentEligible=False,
            plans=[],
            quantitativeFundCodes=[],
            quantitativeEvidence=[],
        )
    return annotate_topic_primary_funds(report)


def _fact_view(row, fields, at):
    if not isinstance(row, dict):
        return None
    for key in (
        "publishedAt",
        "retrievedAt",
        "availableAt",
        "observedAt",
        "quoteTimestamp",
    ):
        if row.get(key) is not None:
            try:
                if _stamp(row[key]) > at:
                    return None
            except (ValidationError_, ValueError, TypeError):
                return None
    for key in ("asOf", "date", "referenceDate"):
        if isinstance(row.get(key), str):
            try:
                if date.fromisoformat(row[key][:10]) > at.date():
                    return None
            except ValueError:
                return None
    result = {}
    for key in fields:
        value = row.get(key)
        if key not in row:
            continue
        if value is None or type(value) in (bool, int) or isinstance(value, str):
            result[key] = value[:8000] if isinstance(value, str) else value
        elif isinstance(value, float) and math.isfinite(value):
            result[key] = value
    if "sourceUri" in result and result["sourceUri"] is not None:
        from backend.macro.daily_research import _url

        try:
            result["sourceUri"] = _url(result["sourceUri"])
        except (ValueError, TypeError):
            result.pop("sourceUri")
    return result


def _context(packet, report, run_id, at):
    # Every nested public structure has its own whitelist; never return packet,
    # deployment parameters, portfolio inputs, or arbitrary source metadata.
    report_at = _stamp(at)
    at = _stamp(packet["asOf"])
    common = (
        "code",
        "label",
        "value",
        "unit",
        "asOf",
        "publishedAt",
        "retrievedAt",
        "availableAt",
        "sourceUri",
        "sourceName",
        "state",
        "referencePeriod",
        "referenceDate",
        "frequency",
        "timeBasis",
        "methodVersion",
    )
    statistics = (
        "previousValue",
        "change3Months",
        "change6Months",
        "median",
        "percentile",
        "sampleCount",
        "firstPeriod",
        "lastPeriod",
        "change5Samples",
        "change20Samples",
        "changeUnit",
        "periodChange",
    )
    metadata = (
        "definitionKey",
        "definitionVersion",
        "accumulationStart",
        "accumulationEnd",
    )

    def facts(rows, fields):
        return (
            [safe for row in rows if (safe := _fact_view(row, fields, at)) is not None]
            if isinstance(rows, list)
            else []
        )

    items = facts(
        packet.get("macros"),
        (*common, "publicationStatus", "valueVerification", "periodChange"),
    )
    for item, original in zip(
        items,
        [
            row
            for row in packet.get("macros", [])
            if _fact_view(row, common, at) is not None
        ],
    ):
        if isinstance(original.get("statistics"), dict):
            item["statistics"] = _fact_view(original["statistics"], statistics, at)
        if isinstance(original.get("history"), list):
            item["history"] = facts(original["history"], (*common, "revision"))
    series = facts(packet.get("series"), common)
    originals = [
        row
        for row in packet.get("series", [])
        if _fact_view(row, common, at) is not None
    ]
    for item, original in zip(series, originals):
        item["changes"] = _fact_view(original.get("changes"), statistics, at) or {
            "sampleCount": 0
        }
        item["metadata"] = (
            _fact_view(
                original.get("metadata") or original.get("definition"), metadata, at
            )
            or {}
        )
        item["history"] = facts(
            original.get("history"), (*common, "date", "revision", *metadata)
        )
    original_alpha = packet.get("alphaPublic")
    original_alpha = original_alpha if isinstance(original_alpha, dict) else {}
    alpha = {
        "asOf": original_alpha.get("asOf"),
        "observations": facts(
            original_alpha.get("observations"), (*common, "sourceKey")
        ),
        "catalysts": facts(
            original_alpha.get("catalysts"),
            (
                *common,
                "id",
                "title",
                "content",
                "phase",
                "importance",
                "factVerification",
            ),
        ),
        "odds": facts(
            original_alpha.get("odds"),
            (
                *common,
                "marketId",
                "question",
                "outcome",
                "impliedProbabilityPct",
                "observedAt",
                "quoteTimestamp",
            ),
        ),
        "launches": facts(
            original_alpha.get("launches"),
            (*common, "id", "name", "phase", "scheduledAt"),
        ),
        "sourceResults": {},
    }
    source_results = original_alpha.get("sourceResults")
    if isinstance(source_results, dict):
        alpha["sourceResults"] = {
            key: safe
            for key, row in source_results.items()
            if acceptance._token(key)
            and (safe := _fact_view(row, ("status", "records", "retrievedAt"), at))
            is not None
        }
    for item, original in zip(
        alpha["observations"],
        [
            row
            for row in original_alpha.get("observations", [])
            if _fact_view(row, common, at) is not None
        ],
    ):
        item["metadata"] = _fact_view(original.get("metadata"), metadata, at) or {}
    if alpha["observations"]:
        from backend.macro.alpha_repository import merge_alpha_series

        for derived in merge_alpha_series([], alpha, at):
            if not derived.get("history"):
                continue
            existing = next(
                (row for row in series if row.get("code") == derived["code"]), None
            )
            if existing is None:
                series.append(
                    {
                        key: deepcopy(derived[key])
                        for key in (*common, "history", "changes", "metadata")
                        if key in derived
                    }
                )
            elif not existing["history"] and all(
                existing.get(key) == derived.get(key)
                for key in ("unit", "frequency", "sourceUri")
            ):
                existing["history"] = deepcopy(derived["history"])
    schedules = facts(
        packet.get("schedules"),
        ("code", "status", "scheduledAt", "referencePeriod", "sourceUri"),
    )
    for item in items:
        next_release = next(
            (row for row in schedules if row.get("code") == item.get("code")), None
        )
        if next_release:
            item["nextRelease"] = deepcopy(next_release)
    return {
        "state": "HISTORICAL_SIMULATION",
        "items": items,
        "series": series,
        "alphaPublic": alpha,
        "schedules": schedules,
        "energy": facts(
            packet.get("energy"),
            (
                *common,
                "id",
                "previousValue",
                "previousDate",
                "change",
                "changePct",
                "sampleCount",
            ),
        ),
        "events": [],
        "actionableZones": [],
        "dailyResearch": {
            "methodVersion": VERSION,
            "state": "READY",
            "runId": str(run_id),
            "reportRunId": str(run_id),
            "reportCompletedAt": report_at.isoformat(),
            "scheduledFor": None,
            "startedAt": None,
            "completedAt": report_at.isoformat(),
            "asOf": report_at.isoformat(),
            "nextRunAt": None,
            "report": report,
        },
    }


async def preview_research(conn, run_id, *, now):
    run_id = UUID(str(run_id))
    record = await conn.fetchrow(
        _RUN_SELECT
        + " WHERE r.run_id=$1 AND (v.run_id IS NOT NULL OR q.run_id IS NOT NULL)",
        run_id,
    )
    if record is None:
        raise NotFoundError("该研究没有可访问的历史材料")
    bundle = await acceptance.read_artifacts_admin(conn, run_id, include_content=True)
    _run_clock(record, bundle, run_id, now)
    try:
        frozen = decode_frozen_bundle(bundle, run_id, observed_at=now)
        check_frozen_contract(frozen.packet)
        _source_binding(frozen)
    except (FrozenBundleError, OfflineReplayError, ValueError, TypeError, KeyError):
        raise ValidationError_(
            "冻结材料缺失、身份或时刻不符、超限或完整性校验失败，无法预览"
        ) from None
    try:
        value = await run_offline_repair(frozen.repair_payload())
    except (OfflineReplayError, OSError):
        raise DataUnavailableError(
            "历史材料离线校验未能完成，未执行实时研究或数据补抓"
        ) from None
    if (
        not isinstance(value, dict)
        or value.get("validatedAt") != frozen.validated_at.isoformat()
        or (value.get("originalOutcome") or {}).get("researchId") != str(run_id)
    ):
        raise DataUnavailableError("历史离线校验返回的身份或时刻不一致")
    report = _report_view(value, frozen.packet, frozen.validated_at)
    from backend.macro.research_topic_repository import prepare_topic_registry

    registry = value.get("topicRegistry")
    if registry is not None:
        registry = _readonly(
            prepare_topic_registry(registry, run_id, frozen.validated_at)
        )
    histories = await _histories(conn, frozen, record)
    try:
        funds = _fund_views(json.loads(frozen.raw), frozen, report, histories)
        context = _context(frozen.packet, report, run_id, frozen.validated_at)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValidationError_(
            "原始报告或冻结公开事实的字段结构不完整，无法生成完整只读预览"
        ) from None
    missing = sorted({reason for row in funds for reason in row["missingReasons"]})
    if value.get("formalReport") is None:
        missing.append("STRICT_REPORT_UNAVAILABLE")
    result = {
        "version": VERSION,
        "mode": "HISTORICAL_SIMULATION",
        "runId": str(run_id),
        "validatedAt": frozen.validated_at.isoformat(),
        "generatedAt": _stamp(now).isoformat(),
        "validatorVersion": (value.get("originalOutcome") or {}).get(
            "validatorVersion"
        ),
        "readOnly": True,
        "investmentEligible": False,
        "state": "PARTIAL" if missing else "COMPLETE",
        "sourceDigests": frozen.material_hashes,
        "report": report,
        "opportunityContext": context,
        "topicRegistry": registry,
        "funds": funds,
        "missingReasons": missing,
    }
    if len(acceptance._encode(result).encode("utf-8")) > OUTPUT_LIMIT:
        raise DataUnavailableError("历史只读预览超出完整显示范围，未截断或替换历史材料")
    return result
