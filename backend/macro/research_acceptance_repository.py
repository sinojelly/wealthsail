"""Immutable validation evidence and local replay; no collection or execution authority."""

from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

import asyncpg

from backend.errors import (
    ForbiddenError,
    AppError,
    DataUnavailableError,
    IdempotencyConflictError,
    NotFoundError,
    RateLimitedError,
    ValidationError_,
)

PACKET_LIMIT = 4 * 1024 * 1024
RAW_LIMIT = 2 * 1024 * 1024
CHECKS_LIMIT = 512 * 1024
OUTCOME_LIMIT = 1024 * 1024
KINDS = ("PACKET", "RAW_MODEL", "SOURCE_CHECKS")
COMPONENTS = (
    "sources",
    "industryCycleReviews",
    "valueDirections",
    "events",
    "upcoming",
    "fundPlans",
    "report",
    "energy",
    "macroOverview",
)
SAFE_COLUMNS = "id,run_id,kind,validator_version,validated_at,recorded_at,summary"
ARCHIVE_MAX_ATTEMPTS = 8
ARCHIVE_LEASE_SECONDS = 120
ARCHIVE_SAFE_COLUMNS = "run_id,status,attempts,created_at,updated_at,next_attempt_at,last_error_class,safe_summary"
ARCHIVE_ERRORS = frozenset(
    {
        "ConnectionError",
        "TimeoutError",
        "OSError",
        "PostgresError",
        "InterfaceError",
        "SerializationError",
        "ValidationError_",
        "ArchiveDataError",
        "UnknownArchiveError",
    }
)


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    raise TypeError("unsupported frozen value")


def _encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError_("校验时刻缺少明确时区")
    return value


def freeze_artifact(value, limit, *, raw=False):
    """Hash the complete canonical representation; omit, never cut, oversize bodies."""
    try:
        text = value if raw and isinstance(value, str) else _encode(value)
        encoded = text.encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return {
            "content": None,
            "sha256": None,
            "byteLength": None,
            "omissionReason": "UNSERIALIZABLE",
        }
    missing = raw and value is None
    return {
        "content": text if len(encoded) <= limit and not missing else None,
        "sha256": sha256(encoded).hexdigest(),
        "byteLength": len(encoded),
        "omissionReason": "MISSING"
        if missing
        else "SIZE_LIMIT"
        if len(encoded) > limit
        else None,
    }


def _text(value, limit=600):
    return value[:limit] if isinstance(value, str) else None


def _token(value):
    return (
        value
        if isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:\[\]-]{0,199}", value)
        else None
    )


def _safe_item(item, fields):
    if not isinstance(item, dict):
        return {}
    result = {}
    for field in fields:
        value = item.get(field)
        if isinstance(value, str):
            result[field] = value[:1000]
        elif field in item and value is None:
            result[field] = None
        elif isinstance(value, list):
            result[field] = [text[:300] for text in value[:16] if isinstance(text, str)]
    return result


def _cycle_projection(value):
    from backend.macro.industry_cycle import (
        EARNINGS_RISK_FLAGS,
        LEGACY_CONFIRMED_RISK_FLAGS,
        RISK_CHECKS,
    )

    if not isinstance(value, dict):
        return {}
    result = _safe_item(value, ("label", "evidenceSummary"))
    if value.get("stage") in {
        "EARNINGS_DELIVERY",
        "POLICY_CATALYST",
        "EARLY_COMMERCIALIZATION",
        "UNKNOWN",
    }:
        result["stage"] = value["stage"]
    if value.get("riskLevel") in {"NONE", "UNPROVEN", "SUSPECTED", "CONFIRMED"}:
        result["riskLevel"] = value["riskLevel"]
    flags = (
        set(RISK_CHECKS)
        | EARNINGS_RISK_FLAGS
        | LEGACY_CONFIRMED_RISK_FLAGS
        | {"PERSISTED_RISK_WATCH"}
    )
    gaps = (
        set(RISK_CHECKS)
        | {
            "CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING",
            "EARNINGS_ENTITY_OR_PERIOD_UNVERIFIED",
            "EARNINGS_INDUSTRY_SCOPE_UNVERIFIED",
            "STAGE_INDUSTRY_SCOPE_UNVERIFIED",
            "CURRENT_INDUSTRY_STAGE_EVIDENCE_MISSING",
        }
        | {"INDUSTRY_SCOPE_UNVERIFIED:" + kind for kind in RISK_CHECKS}
    )
    for field in (
        "riskFlags",
        "confirmedRiskFlags",
        "suspectedRiskFlags",
        "persistedRiskFlags",
        "evidenceGaps",
    ):
        if isinstance(value.get(field), list):
            allowed = gaps if field == "evidenceGaps" else flags
            result[field] = list(
                dict.fromkeys(
                    item
                    for item in value[field]
                    if isinstance(item, str) and item in allowed
                )
            )
    for field in ("earningsVerified", "searchExecutionVerified"):
        if isinstance(value.get(field), bool):
            result[field] = value[field]
    return result


def _accepted(value):
    value = value if isinstance(value, dict) else {}
    fields = {
        "sources": (
            "id",
            "title",
            "url",
            "quote",
            "publishedDate",
            "publishedAt",
            "verification",
        ),
        "industryCycleReviews": (
            "industry",
            "stage",
            "riskLevel",
            "evidenceSummary",
            "sourceIds",
        ),
        "valueDirections": (
            "key",
            "title",
            "thesis",
            "summary",
            "reviewIndustryIds",
            "sourceIds",
            "watchFor",
        ),
        "events": (
            "title",
            "summary",
            "phase",
            "importance",
            "sourceIds",
            "watchFor",
            "unknowns",
        ),
        "upcoming": (
            "title",
            "phase",
            "scheduledDate",
            "scheduledAt",
            "sourceIds",
            "watchFor",
        ),
    }
    output = {}
    limits = {
        "sources": 24,
        "industryCycleReviews": 64,
        "valueDirections": 8,
        "events": 12,
        "upcoming": 12,
    }
    for component, allowed in fields.items():
        rows = value.get(component)
        output[component] = (
            [
                _safe_item(row, allowed)
                for row in rows[: limits[component]]
                if isinstance(row, dict)
            ]
            if isinstance(rows, list)
            else []
        )
    for row in output["sources"]:
        if "quote" in row:
            row["quote"] = row["quote"][:300]
        if "id" in row and not _token(row["id"]):
            row.pop("id")
        if "url" in row:
            from backend.macro.daily_research import _url

            try:
                row["url"] = _url(row["url"])
            except (ValueError, TypeError):
                row.pop("url")
    reviews = value.get("industryCycleReviews")
    for projected, original in zip(
        output["industryCycleReviews"],
        [row for row in reviews if isinstance(row, dict)]
        if isinstance(reviews, list)
        else [],
    ):
        projected.pop("stage", None)
        projected.pop("riskLevel", None)
        projected.update(_cycle_projection(original))
    directions = value.get("valueDirections")
    for projected, original in zip(
        output["valueDirections"],
        [row for row in directions if isinstance(row, dict)]
        if isinstance(directions, list)
        else [],
    ):
        cycle = original.get("industryCycle")
        if not isinstance(cycle, dict):
            continue
        safe_cycle = _cycle_projection(cycle)
        if safe_cycle:
            projected["industryCycle"] = safe_cycle
    overview = value.get("macroOverview")
    if (
        isinstance(overview, dict)
        and overview.get("verification") == "MODEL_UNVERIFIED"
    ):
        projected = _safe_item(overview, ("summary", "macroCycle"))
        projected["verification"] = "MODEL_UNVERIFIED"
        projected["sourceIds"] = (
            [item for item in overview.get("sourceIds", [])[:24] if _token(item)]
            if isinstance(overview.get("sourceIds"), list)
            else []
        )
        if isinstance(overview.get("coverage"), list):
            projected["coverage"] = []
            for row in overview["coverage"][:6]:
                if isinstance(row, dict):
                    item = _safe_item(row, ("topic", "status", "summary"))
                    if isinstance(item.get("summary"), str):
                        item["summary"] = item["summary"][:300]
                    projected["coverage"].append(item)
        output["macroOverview"] = projected
    return output


def _has_excerpt(original, projected, *, string_limit=1000):
    """Inspect only displayed fields; omitted private/plan fields are not excerpts."""
    if isinstance(original, dict) and isinstance(projected, dict):
        return any(
            _has_excerpt(
                original.get(key),
                value,
                string_limit=300
                if key in {"quote", "coverage"}
                or (key == "summary" and string_limit == 300)
                else 1000,
            )
            for key, value in projected.items()
        )
    if isinstance(original, list) and isinstance(projected, list):
        visible = [value for value in original if isinstance(value, (dict, str))]
        return len(visible) > len(projected) or any(
            _has_excerpt(before, after, string_limit=300)
            for before, after in zip(visible, projected)
        )
    return (
        isinstance(original, str)
        and isinstance(projected, str)
        and len(original) > string_limit
        and len(projected) < len(original)
    )


def _issues(value, *, admin=False, research_id=None):
    from backend.macro.research_diagnostics import (
        RULE_IDS,
        REASON_CODES,
        is_safe_item_index,
        sanitize_missing_industry_codes,
    )

    try:
        from backend.macro.research_acceptance import ISSUE_MESSAGES
    except ImportError:
        ISSUE_MESSAGES = {}
    rows = value if isinstance(value, list) else []
    output = []
    components = {
        "SOURCE": "sources",
        "INDUSTRY_REVIEW": "industryCycleReviews",
        "VALUE_DIRECTION": "valueDirections",
        "FUND_PLAN": "fundPlans",
        "EVENT": "events",
        "UPCOMING": "upcoming",
        "REPORT": "$",
    }
    for row in rows[:2048]:
        if not isinstance(row, dict):
            continue
        result = {}
        identity = row.get("issueId")
        if isinstance(identity, str) and (
            re.fullmatch(r"[a-f0-9]{32}", identity)
            or re.fullmatch(r"[a-fA-F0-9-]{36}", identity)
        ):
            result["issueId"] = identity
        if research_id is not None:
            result["researchId"] = str(UUID(str(research_id)))
        else:
            try:
                result["researchId"] = str(UUID(str(row.get("researchId"))))
            except (ValueError, TypeError):
                pass
        item_type = row.get("itemType")
        kind = (
            item_type
            if isinstance(item_type, str) and item_type in components
            else "REPORT"
        )
        result["itemType"] = kind
        if isinstance(row.get("disposition"), str) and row["disposition"] in {
            "QUARANTINED",
            "BLOCKED",
            "REJECTED",
        }:
            result["disposition"] = row["disposition"]
        if is_safe_item_index(row.get("itemIndex")):
            result["itemIndex"] = row["itemIndex"]
        else:
            result["itemIndex"] = None
        component = components[kind]
        result["path"] = (
            f"{component}[{result['itemIndex']}]"
            if component != "$" and result["itemIndex"] is not None
            else component
        )
        rule = row.get("ruleId")
        reason = row.get("reasonCode")
        if isinstance(rule, str) and (rule in RULE_IDS or rule in ISSUE_MESSAGES):
            result["ruleId"] = rule
        if isinstance(reason, str) and reason in REASON_CODES:
            result["reasonCode"] = reason
        result["message"] = (
            ISSUE_MESSAGES.get(rule, "该项未通过验收")
            if isinstance(rule, str)
            else "该项未通过验收"
        )
        if admin:
            if path := _text(row.get("path"), 1000):
                result["path"] = path
            if business := _text(row.get("businessKey"), 200):
                result["businessKey"] = business
            result["dependencies"] = []
            if isinstance(row.get("dependencies"), list):
                for item in row["dependencies"][:100]:
                    if (
                        isinstance(item, dict)
                        and _token(item.get("itemType"))
                        and isinstance(item.get("businessKey"), str)
                    ):
                        result["dependencies"].append(
                            {
                                "itemType": item["itemType"],
                                "businessKey": item["businessKey"][:200],
                            }
                        )
            codes = sanitize_missing_industry_codes(row.get("missingIndustryCodes"))
            if codes:
                result["missingIndustryCodes"] = codes
        output.append(result)
    return output


def safe_outcome_summary(value):
    value = value if isinstance(value, dict) else {}
    counts = {}
    source_counts = value.get("counts") or {}
    for component in COMPONENTS:
        row = source_counts.get(component) if isinstance(source_counts, dict) else None
        if isinstance(row, dict):
            counts[component] = {
                key: min(number, 10000)
                if isinstance(number, int)
                and not isinstance(number, bool)
                and number >= 0
                else 0
                for key in ("inputCount", "acceptedCount", "quarantinedCount")
                for number in [row.get(key)]
            }
    issues = _issues(value.get("issues"), research_id=value.get("researchId"))
    accepted = _accepted(value.get("accepted"))
    return {
        "researchId": str(UUID(str(value["researchId"]))),
        "validatorVersion": _token(value.get("validatorVersion")),
        "validatorRevision": value.get("validatorRevision")
        if isinstance(value.get("validatorRevision"), str)
        and re.fullmatch(r"[a-f0-9]{64}", value["validatorRevision"])
        else None,
        "revisionStatus": "AVAILABLE"
        if isinstance(value.get("validatorRevision"), str)
        and re.fullmatch(r"[a-f0-9]{64}", value["validatorRevision"])
        and value.get("revisionStatus") == "AVAILABLE"
        else "UNAVAILABLE",
        "validatedAt": _aware(value.get("validatedAt")).isoformat(),
        "decision": value.get("decision")
        if value.get("decision") in {"COMPLETE", "PARTIAL", "EMPTY", "REJECTED"}
        else "REJECTED",
        "formalPublishable": value.get("formalPublishable") is True,
        "actionable": False,
        "counts": counts,
        "issueCount": len(value.get("issues", []))
        if isinstance(value.get("issues"), list)
        else 0,
        "issueListComplete": value.get("issueListComplete") is True
        and len(issues) == len(value.get("issues", [])),
        "issues": issues,
        "accepted": accepted,
        "acceptedContentComplete": value.get("acceptedContentComplete", True)
        is not False
        and not _has_excerpt(value.get("accepted"), accepted),
    }


def _artifact_dto(row, *, content=True):
    if row is None:
        raise NotFoundError("该研究没有冻结校验材料")
    row = dict(row)
    items = []
    for key, kind in zip(("packet", "raw_output", "source_checks"), KINDS):
        item = _object(row[key])
        items.append(
            {"kind": kind, **item, "content": item.get("content") if content else None}
        )
    return {
        "researchId": str(row["run_id"]),
        "validatedAt": _aware(row["validated_at"]).isoformat(),
        "complete": all(
            item.get("omissionReason") is None
            and isinstance(_object(row[key]).get("content"), str)
            for item, key in zip(items, ("packet", "raw_output", "source_checks"))
        ),
        "items": items,
    }


def _freeze_inputs(packet, raw, source_checks, raw_metadata=None):
    values = [
        freeze_artifact(packet, PACKET_LIMIT),
        freeze_artifact(raw, RAW_LIMIT, raw=True),
        freeze_artifact(source_checks, CHECKS_LIMIT),
    ]
    for index, value in ((0, packet), (2, source_checks)):
        if value is None:
            values[index].update(content=None, omissionReason="MISSING")
    if isinstance(raw_metadata, dict):
        digest, size, reason = (
            raw_metadata.get(key) for key in ("sha256", "byteLength", "omissionReason")
        )
        if (
            isinstance(digest, str)
            and re.fullmatch(r"[a-f0-9]{64}", digest)
            and isinstance(size, int)
            and not isinstance(size, bool)
            and 0 <= size <= 2**63 - 1
            and reason in {"SIZE_LIMIT", "INVALID_UTF8"}
        ):
            values[1] = {
                "content": None,
                "sha256": digest,
                "byteLength": size,
                "omissionReason": reason,
            }
    return values


async def _save_frozen_artifacts(conn, run_id, validated_at, values):
    await conn.execute(
        """INSERT INTO research.research_validation_artifacts(run_id,validated_at,packet,raw_output,source_checks)
        VALUES($1,$2,$3::jsonb,$4::jsonb,$5::jsonb) ON CONFLICT(run_id) DO NOTHING""",
        run_id,
        _aware(validated_at),
        *[_encode(value) for value in values],
    )
    row = await conn.fetchrow(
        "SELECT * FROM research.research_validation_artifacts WHERE run_id=$1", run_id
    )
    return _artifact_dto(row, content=False)


async def save_research_artifacts(
    conn, run_id, packet, raw, source_checks, validated_at, *, raw_metadata=None
):
    return await _save_frozen_artifacts(
        conn,
        run_id,
        validated_at,
        _freeze_inputs(packet, raw, source_checks, raw_metadata),
    )


def _archive_status(row):
    row = dict(row)
    status = row["status"]
    return {
        "researchId": str(row["run_id"]),
        "archiveState": status,
        "archivePending": status in {"AUDIT_PENDING", "ARCHIVING"},
        "archiveMessage": "验收归档待恢复，不影响已发布的严格报告。"
        if status in {"AUDIT_PENDING", "ARCHIVING"}
        else "验收归档无法自动恢复，请管理员检查冻结材料。"
        if status == "UNRECOVERABLE"
        else "验收材料已归档。",
    }


async def enqueue_research_archive(
    conn,
    run_id,
    packet,
    raw,
    checks,
    validated_at,
    outcome_dict,
    *,
    raw_metadata=None,
    recorded_at=None,
):
    if str(outcome_dict.get("researchId")) != str(run_id):
        raise ValidationError_("校验结果与研究身份不一致")
    if len(_encode(outcome_dict).encode("utf-8")) > OUTCOME_LIMIT:
        raise ValidationError_("校验结果超过存储大小上限")
    stamp = _aware(validated_at)
    recorded = _aware(recorded_at or datetime.now(timezone.utc))
    descriptors = _freeze_inputs(packet, raw, checks, raw_metadata)
    bundle = {
        "validatedAt": stamp.isoformat(),
        "recordedAt": recorded.isoformat(),
        "artifacts": dict(zip(("packet", "raw_output", "source_checks"), descriptors)),
        "outcome": outcome_dict,
    }
    await conn.execute(
        """INSERT INTO research.research_validation_archive_queue(run_id,validated_at,frozen_bundle,safe_summary,created_at,updated_at,next_attempt_at)
        VALUES($1,$2,$3::jsonb,$4::jsonb,$5,$5,$5) ON CONFLICT(run_id) DO NOTHING""",
        run_id,
        stamp,
        _encode(bundle),
        _encode(safe_outcome_summary(outcome_dict)),
        recorded,
    )
    row = await conn.fetchrow(
        f"SELECT {ARCHIVE_SAFE_COLUMNS} FROM research.research_validation_archive_queue WHERE run_id=$1",
        run_id,
    )
    return _archive_status(row)


async def claim_research_archive(conn, now, *, run_id=None):
    now = _aware(now)
    await conn.execute(
        """WITH exhausted AS (
        SELECT run_id FROM research.research_validation_archive_queue
        WHERE status='ARCHIVING' AND lease_expires_at<=$1 AND attempts>=8 AND ($2::uuid IS NULL OR run_id=$2)
        ORDER BY lease_expires_at,run_id FOR UPDATE SKIP LOCKED LIMIT 20)
        UPDATE research.research_validation_archive_queue q SET status='UNRECOVERABLE',lease_token=NULL,lease_expires_at=NULL,updated_at=$1,last_error_class='TimeoutError'
        FROM exhausted e WHERE q.run_id=e.run_id""",
        now,
        run_id,
    )
    row = await conn.fetchrow(
        """WITH candidate AS (
        SELECT run_id FROM research.research_validation_archive_queue
        WHERE attempts<8 AND ($2::uuid IS NULL OR run_id=$2)
          AND ((status='AUDIT_PENDING' AND next_attempt_at<=$1) OR (status='ARCHIVING' AND lease_expires_at<=$1))
        ORDER BY next_attempt_at,run_id FOR UPDATE SKIP LOCKED LIMIT 1)
        UPDATE research.research_validation_archive_queue q SET status='ARCHIVING',attempts=q.attempts+1,
          lease_token=$3,lease_expires_at=$1+interval '120 seconds',updated_at=$1
        FROM candidate c WHERE q.run_id=c.run_id RETURNING q.*""",
        now,
        run_id,
        uuid4(),
    )
    if row is None:
        return None
    result = dict(row)
    result["frozen_bundle"] = _object(result["frozen_bundle"])
    return result


async def finish_research_archive(conn, claim, now, *, status, error_class=None):
    if status not in {"ARCHIVED", "AUDIT_PENDING", "UNRECOVERABLE"}:
        raise ValidationError_("未知归档状态")
    attempts = claim["attempts"]
    if status == "AUDIT_PENDING" and attempts >= ARCHIVE_MAX_ATTEMPTS:
        status = "UNRECOVERABLE"
    safe_error = (
        error_class
        if error_class in ARCHIVE_ERRORS
        else "UnknownArchiveError"
        if error_class
        else None
    )
    now = _aware(now)
    row = await conn.fetchrow(
        """UPDATE research.research_validation_archive_queue SET status=$3,updated_at=$4,next_attempt_at=$5,
          last_error_class=$6,lease_token=NULL,lease_expires_at=NULL
        WHERE run_id=$1 AND lease_token=$2 AND status='ARCHIVING' AND lease_expires_at>$4
        RETURNING status,attempts,next_attempt_at""",
        claim["run_id"],
        claim["lease_token"],
        status,
        now,
        now + timedelta(seconds=min(3600, 15 * 2 ** min(attempts, 8))),
        safe_error,
    )
    return (
        {
            "status": row["status"],
            "attempts": row["attempts"],
            "nextAttemptAt": row["next_attempt_at"].isoformat(),
        }
        if row
        else None
    )


async def persist_research_archive(conn, claim, now):
    # Fence the lease before writing. Caller wraps artifacts + receipt + finish in one transaction.
    row = await conn.fetchrow(
        """SELECT run_id FROM research.research_validation_archive_queue WHERE run_id=$1 AND lease_token=$2
        AND status='ARCHIVING' AND lease_expires_at>$3 FOR UPDATE""",
        claim["run_id"],
        claim["lease_token"],
        _aware(now),
    )
    if row is None:
        raise ValidationError_("归档租约已失效")
    bundle = _object(claim["frozen_bundle"])
    descriptors = bundle.get("artifacts", {})
    if not isinstance(descriptors, dict) or set(descriptors) != {
        "packet",
        "raw_output",
        "source_checks",
    }:
        raise ValidationError_("冻结归档描述缺失")
    await _save_frozen_artifacts(
        conn,
        claim["run_id"],
        bundle["validatedAt"],
        [descriptors[key] for key in ("packet", "raw_output", "source_checks")],
    )
    receipt = await save_validation_outcome(
        conn,
        claim["run_id"],
        bundle["outcome"],
        recorded_at=_aware(bundle["recordedAt"]),
    )
    if await finish_research_archive(conn, claim, now, status="ARCHIVED") is None:
        raise ValidationError_("归档租约已失效")
    return receipt


async def save_validation_outcome(
    conn,
    run_id,
    outcome_dict,
    *,
    kind="INITIAL",
    actor_id=None,
    command_key=None,
    tenant_id=None,
    recorded_at=None,
):
    if kind not in {"INITIAL", "REPLAY"}:
        raise ValidationError_("未知校验记录类型")
    if str(outcome_dict.get("researchId")) != str(run_id):
        raise ValidationError_("校验结果与研究身份不一致")
    encoded = _encode(outcome_dict)
    if len(encoded.encode("utf-8")) > OUTCOME_LIMIT:
        raise ValidationError_("校验结果超过存储大小上限")
    if kind == "REPLAY" and any(
        item is None for item in (actor_id, command_key, tenant_id)
    ):
        raise ValidationError_("离线重放缺少明确的操作身份")
    summary = safe_outcome_summary(outcome_dict)
    recorded = _aware(recorded_at or datetime.now(timezone.utc))
    await conn.execute(
        """INSERT INTO research.research_validation_records
        (id,run_id,kind,actor_id,actor_tenant_id,command_key,validator_version,validated_at,recorded_at,outcome,summary)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb) ON CONFLICT DO NOTHING""",
        uuid4(),
        run_id,
        kind,
        actor_id,
        tenant_id,
        command_key,
        outcome_dict["validatorVersion"],
        _aware(outcome_dict["validatedAt"]),
        recorded,
        encoded,
        _encode(summary),
    )
    if kind == "INITIAL":
        row = await conn.fetchrow(
            f"SELECT {SAFE_COLUMNS} FROM research.research_validation_records WHERE run_id=$1 AND kind='INITIAL'",
            run_id,
        )
    else:
        row = await conn.fetchrow(
            f"SELECT {SAFE_COLUMNS} FROM research.research_validation_records WHERE actor_tenant_id=$1 AND actor_id=$2 AND command_key=$3",
            tenant_id,
            actor_id,
            command_key,
        )
        if row and str(row["run_id"]) != str(run_id):
            raise IdempotencyConflictError("该命令已用于另一研究的离线重放")
    return await _record_dto(conn, row, admin=kind == "REPLAY")


async def read_artifacts_admin(
    conn, run_id, actor_id=None, tenant_id=None, *, include_content=True
):
    value = await _admin_json(
        conn,
        "SELECT research.read_validation_artifacts_admin($1,$2)",
        run_id,
        include_content,
    )
    if value is None:
        raise NotFoundError("该研究没有可访问的冻结校验材料")
    return _object(value)


async def _admin_json(conn, sql, *args):
    try:
        return await conn.fetchval(sql, *args)
    except asyncpg.InsufficientPrivilegeError:
        raise ForbiddenError("仅当前有效管理员可查看校验材料") from None


async def _record_dto(conn, row, *, admin=False):
    if row is None:
        return None
    summary = safe_outcome_summary(_object(row["summary"]))
    summary.update(
        {
            "id": str(row["id"]),
            "kind": row["kind"],
            "recordedAt": _aware(row["recorded_at"]).isoformat(),
            "canInspectArtifacts": admin,
            "canReplay": False,
            "archiveState": "ARCHIVED",
            "archivePending": False,
        }
    )
    if admin:
        original = _object(
            await _admin_json(
                conn, "SELECT research.read_validation_outcome_admin($1)", row["id"]
            )
        )
        if original:
            summary["issues"] = _issues(
                original.get("issues"), admin=True, research_id=row["run_id"]
            )
        frozen = await _admin_json(
            conn,
            "SELECT research.read_validation_artifacts_admin($1,$2)",
            row["run_id"],
            False,
        )
        summary["canReplay"] = _object(frozen).get("complete") is True
    return summary


async def read_validation_summary(conn, run_id, now, *, admin=False):
    row = await conn.fetchrow(
        f"SELECT {SAFE_COLUMNS} FROM research.research_validation_records WHERE run_id=$1 AND kind='INITIAL' AND recorded_at<=$2",
        run_id,
        _aware(now),
    )
    if row is not None:
        return await _record_dto(conn, row, admin=admin)
    queued = await conn.fetchrow(
        f"SELECT {ARCHIVE_SAFE_COLUMNS} FROM research.research_validation_archive_queue WHERE run_id=$1 AND created_at<=$2",
        run_id,
        _aware(now),
    )
    if queued is None:
        return None
    summary = safe_outcome_summary(_object(queued["safe_summary"]))
    summary.update(
        id=str(run_id),
        kind="INITIAL",
        recordedAt=queued["created_at"].isoformat(),
        canInspectArtifacts=admin,
        canReplay=False,
    )
    summary.update(_archive_status(queued))
    if admin:
        frozen = _object(
            await _admin_json(
                conn,
                "SELECT research.read_validation_artifacts_admin($1,$2)",
                run_id,
                False,
            )
        )
        summary["recoverableMaterials"] = {
            item["kind"]: item.get("omissionReason") is None
            and item.get("sha256") is not None
            for item in frozen.get("items", [])
        }
        original = _object(
            await _admin_json(
                conn,
                "SELECT research.read_validation_archive_outcome_admin($1)",
                run_id,
            )
        )
        summary["issues"] = _issues(
            original.get("issues"), admin=True, research_id=run_id
        )
    return summary


async def list_validation_replays(conn, run_id, now, *, limit=20):
    rows = await conn.fetch(
        f"SELECT {SAFE_COLUMNS} FROM research.research_validation_records WHERE run_id=$1 AND kind='REPLAY' AND recorded_at<=$2 ORDER BY recorded_at DESC,id DESC LIMIT $3",
        run_id,
        _aware(now),
        max(1, min(limit, 20)),
    )
    return [await _record_dto(conn, row, admin=True) for row in rows]


async def replay_validation(conn, run_id, actor, tenant, commandKey, now):
    async with conn.transaction():
        row = await conn.fetchrow(
            f"SELECT {SAFE_COLUMNS} FROM research.research_validation_records WHERE actor_tenant_id=$1 AND actor_id=$2 AND command_key=$3",
            tenant,
            actor,
            commandKey,
        )
        if row:
            if str(row["run_id"]) != str(run_id):
                raise IdempotencyConflictError("该命令已用于另一研究的离线重放")
            return await _record_dto(conn, row, admin=True)
        previous = await conn.fetchrow(
            "SELECT run_id,status,error_code FROM research.research_validation_replay_attempts WHERE actor_tenant_id=$1 AND actor_id=$2 AND command_key=$3",
            tenant,
            actor,
            commandKey,
        )
        if previous:
            if str(previous["run_id"]) != str(run_id):
                raise IdempotencyConflictError("该命令已用于另一研究的离线重放")
            return _replay_failure(
                "BUSY"
                if previous["status"] == "RUNNING"
                else previous["error_code"] or "VALIDATOR_FAILED"
            )
        archive = await conn.fetchrow(
            "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
            run_id,
        )
        if archive and archive["status"] != "ARCHIVED":
            return ReplayFailure(DataUnavailableError("验收归档待恢复，暂不能离线重放"))
        from backend.macro.research_acceptance import validator_revision

        reserved = _object(
            await _admin_json(
                conn,
                "SELECT research.reserve_validation_replay($1,$2,$3)",
                run_id,
                commandKey,
                validator_revision(),
            )
        )
        status = reserved.get("status")
        if status == "CONFLICT":
            raise IdempotencyConflictError("该命令已用于另一研究的离线重放")
        if status != "RUNNING":
            return _replay_failure(status)
        attempt_id = UUID(reserved["id"])
        from backend.macro.research_acceptance_replay import (
            OfflineReplayError,
            run_offline_validation,
        )

        try:
            frozen = await read_artifacts_admin(conn, run_id, actor, tenant)
            if frozen.get("complete") is not True:
                raise OfflineReplayError("MATERIALS_INVALID")
            bodies = {}
            for item in frozen.get("items", []):
                text = item.get("content")
                kind = item.get("kind")
                if (
                    kind not in KINDS
                    or kind in bodies
                    or not isinstance(text, str)
                    or sha256(text.encode("utf-8")).hexdigest() != item.get("sha256")
                    or len(text.encode("utf-8")) != item.get("byteLength")
                ):
                    raise OfflineReplayError("MATERIALS_INVALID")
                bodies[kind] = text
            if set(bodies) != set(KINDS):
                raise OfflineReplayError("MATERIALS_INVALID")
            result = await run_offline_validation(
                {
                    "raw": bodies["RAW_MODEL"],
                    "packet": json.loads(bodies["PACKET"]),
                    "sourceChecks": json.loads(bodies["SOURCE_CHECKS"]),
                    "researchId": str(run_id),
                    "validatedAt": _aware(frozen["validatedAt"]).isoformat(),
                }
            )
            if (
                str(result.get("researchId")) != str(run_id)
                or len(_encode(result).encode("utf-8")) > OUTCOME_LIMIT
            ):
                raise OfflineReplayError("INVALID_OUTPUT")
        except (OfflineReplayError, NotFoundError, ValueError, OSError) as error:
            code = (
                error.code
                if isinstance(error, OfflineReplayError)
                else "MATERIALS_INVALID"
                if isinstance(error, (NotFoundError, ValueError))
                else "VALIDATOR_FAILED"
            )
            state = (
                "TIMED_OUT"
                if code == "TIMEOUT"
                else "CANCELLED"
                if code == "CANCELLED"
                else "FAILED"
            )
            await _admin_json(
                conn,
                "SELECT research.finish_validation_replay($1,$2,$3,$4)",
                attempt_id,
                state,
                None,
                code,
            )
            return _replay_failure(code)
        receipt = await save_validation_outcome(
            conn,
            run_id,
            result,
            kind="REPLAY",
            actor_id=actor,
            command_key=commandKey,
            tenant_id=tenant,
            recorded_at=now,
        )
        if not await _admin_json(
            conn,
            "SELECT research.finish_validation_replay($1,$2,$3,$4)",
            attempt_id,
            "COMPLETE",
            UUID(receipt["id"]),
            None,
        ):
            raise ValidationError_("离线重放计数记录未能完成")
        return receipt


@dataclass(frozen=True)
class ReplayFailure:
    """Return failures so the caller commits admission before raising HTTP errors."""

    error: AppError


def _replay_failure(code):
    if code in {"BUSY", "ACTOR_LIMIT", "RUN_LIMIT"}:
        return ReplayFailure(
            RateLimitedError(
                {
                    "BUSY": "已有离线重放正在执行，请稍后重试同一请求",
                    "ACTOR_LIMIT": "今日离线重放已达到每人10次上限",
                    "RUN_LIMIT": "该研究离线重放已达到20次或20 MiB总量上限",
                }[code]
            )
        )
    if code == "NOT_FOUND":
        return ReplayFailure(NotFoundError("该研究没有可访问的冻结材料"))
    if code == "MATERIALS_INVALID":
        return ReplayFailure(
            ValidationError_("冻结材料缺失、超限或校验值不一致，无法离线重放")
        )
    return ReplayFailure(
        DataUnavailableError(
            "离线重放超时，已停止校验"
            if code == "TIMEOUT"
            else "离线重放已取消并停止校验"
            if code == "CANCELLED"
            else "离线重放未能完成，请检查冻结材料"
        )
    )
