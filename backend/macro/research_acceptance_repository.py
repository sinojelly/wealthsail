"""Immutable validation evidence and local replay; no collection or execution authority."""

from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

import asyncpg

from backend.errors import (
    ForbiddenError,
    IdempotencyConflictError,
    NotFoundError,
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
)
SAFE_COLUMNS = "id,run_id,kind,validator_version,validated_at,recorded_at,summary"


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
    limits = {"sources": 24, "industryCycleReviews": 64, "valueDirections": 8, "events": 12, "upcoming": 12}
    for component, allowed in fields.items():
        rows = value.get(component)
        output[component] = (
            [_safe_item(row, allowed) for row in rows[:limits[component]] if isinstance(row, dict)]
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
        safe_cycle = _safe_item(cycle, ("label", "evidenceSummary"))
        if cycle.get("stage") in {
            "EARNINGS_DELIVERY",
            "POLICY_CATALYST",
            "EARLY_COMMERCIALIZATION",
            "UNKNOWN",
        }:
            safe_cycle["stage"] = cycle["stage"]
        if cycle.get("riskLevel") in {"NONE", "UNPROVEN", "SUSPECTED", "CONFIRMED"}:
            safe_cycle["riskLevel"] = cycle["riskLevel"]
        for field in ("earningsVerified", "searchExecutionVerified"):
            if isinstance(cycle.get(field), bool):
                safe_cycle[field] = cycle[field]
        if safe_cycle:
            projected["industryCycle"] = safe_cycle
    return output


def _has_excerpt(original, projected, *, string_limit=1000):
    """Inspect only displayed fields; omitted private/plan fields are not excerpts."""
    if isinstance(original, dict) and isinstance(projected, dict):
        return any(
            _has_excerpt(original.get(key), value, string_limit=300 if key == "quote" else 1000)
            for key, value in projected.items()
        )
    if isinstance(original, list) and isinstance(projected, list):
        visible = [value for value in original if isinstance(value, (dict, str))]
        return len(visible) > len(projected) or any(
            _has_excerpt(before, after, string_limit=300)
            for before, after in zip(visible, projected)
        )
    return (
        isinstance(original, str) and isinstance(projected, str)
        and len(original) > string_limit and len(projected) < len(original)
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
        "acceptedContentComplete": value.get("acceptedContentComplete", True) is not False
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


async def save_research_artifacts(
    conn, run_id, packet, raw, source_checks, validated_at, *, raw_metadata=None
):
    stamp = _aware(validated_at)
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
    await conn.execute(
        """INSERT INTO research.research_validation_artifacts(run_id,validated_at,packet,raw_output,source_checks)
        VALUES($1,$2,$3::jsonb,$4::jsonb,$5::jsonb) ON CONFLICT(run_id) DO NOTHING""",
        run_id,
        stamp,
        *[_encode(value) for value in values],
    )
    row = await conn.fetchrow(
        "SELECT * FROM research.research_validation_artifacts WHERE run_id=$1", run_id
    )
    return _artifact_dto(row, content=False)


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
        try:
            frozen = await read_artifacts_admin(
                conn, row["run_id"], include_content=False
            )
            summary["canReplay"] = frozen.get("complete") is True
        except NotFoundError:
            pass
    return summary


async def read_validation_summary(conn, run_id, now, *, admin=False):
    row = await conn.fetchrow(
        f"SELECT {SAFE_COLUMNS} FROM research.research_validation_records WHERE run_id=$1 AND kind='INITIAL' AND recorded_at<=$2",
        run_id,
        _aware(now),
    )
    return await _record_dto(conn, row, admin=admin)


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
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            f"research-replay:{tenant}:{actor}:{commandKey}",
        )
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
        frozen = await read_artifacts_admin(conn, run_id, actor, tenant)
        if frozen.get("complete") is not True:
            raise ValidationError_("冻结材料缺失或超过大小上限，无法离线重放")
        bodies = {}
        for item in frozen["items"]:
            text = item.get("content")
            if (
                not isinstance(text, str)
                or sha256(text.encode("utf-8")).hexdigest() != item.get("sha256")
                or len(text.encode("utf-8")) != item.get("byteLength")
            ):
                raise ValidationError_("冻结材料校验值不一致，无法离线重放")
            bodies[item["kind"]] = text
        if set(bodies) != set(KINDS):
            raise ValidationError_("冻结材料不完整，无法离线重放")
        from backend.macro.research_acceptance import validate_report_outcome

        result = validate_report_outcome(
            bodies["RAW_MODEL"],
            json.loads(bodies["PACKET"]),
            json.loads(bodies["SOURCE_CHECKS"]),
            research_id=str(run_id),
            completed_at=_aware(frozen["validatedAt"]),
        )
        return await save_validation_outcome(
            conn,
            run_id,
            result.to_dict(),
            kind="REPLAY",
            actor_id=actor,
            command_key=commandKey,
            tenant_id=tenant,
            recorded_at=now,
        )
