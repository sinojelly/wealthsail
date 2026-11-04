"""Tenant-local immutable historical repairs, separate from every READY consumer."""

from hashlib import sha256
import json
import logging
import re
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg

from backend.errors import (
    DataUnavailableError,
    ForbiddenError,
    IdempotencyConflictError,
    NotFoundError,
    RateLimitedError,
    ValidationError_,
)
from backend.macro import research_acceptance_repository as acceptance
from backend.macro.research_acceptance_repository import ReplayFailure
from backend.macro.research_acceptance_replay import OfflineReplayError

logger = logging.getLogger("wealthsail.history_repair")
RESULT_LIMIT = 4 * 1024 * 1024
ITEM_TYPES = {
    "SOURCE",
    "INDUSTRY_REVIEW",
    "VALUE_DIRECTION",
    "FUND_PLAN",
    "EVENT",
    "UPCOMING",
    "REPORT",
}


def _digest(value):
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)
        else None
    )


def _reason(row):
    from backend.macro.research_acceptance import ISSUE_MESSAGES
    from backend.macro.research_repair import REPAIR_MESSAGES

    code, rule = row.get("code"), row.get("ruleId")
    return (
        REPAIR_MESSAGES.get(
            code,
            REPAIR_MESSAGES.get(
                rule, ISSUE_MESSAGES.get(rule, "该项未通过历史修复核查，已隔离")
            ),
        )
        if isinstance(code, (str, type(None))) and isinstance(rule, (str, type(None)))
        else "该项未通过历史修复核查，已隔离"
    )


def _annex(rows, *, admin=False):
    output = []
    for row in rows[:1000] if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        original = (
            row.get("originalItem") if isinstance(row.get("originalItem"), dict) else {}
        )
        item = {
            "itemType": row.get("itemType")
            if isinstance(row.get("itemType"), str) and row["itemType"] in ITEM_TYPES
            else "REPORT",
            "reason": _reason(row),
        }
        index = row.get("itemIndex")
        item["itemIndex"] = index if type(index) is int and 0 <= index <= 9999 else None
        if "mandatory" in row or "ruleId" in row:
            item["mandatory"] = True
        from backend.macro.research_acceptance import ISSUE_MESSAGES
        from backend.macro.research_repair import REPAIR_MESSAGES

        for field, allowed in (
            ("ruleId", {**ISSUE_MESSAGES, **REPAIR_MESSAGES}),
            ("code", REPAIR_MESSAGES),
        ):
            if isinstance(row.get(field), str) and row[field] in allowed:
                item[field] = row[field]
        for field in ("title", "summary"):
            if isinstance(row.get(field), str):
                item[field] = row[field][:1000]
        for field in ("watchFor", "unknowns", "channels"):
            values = row.get(field, original.get(field))
            if isinstance(values, list):
                item[field] = [
                    text[:300] for text in values[:16] if isinstance(text, str)
                ]
        scenarios = row.get("scenarios", original.get("scenarios"))
        if isinstance(scenarios, list):
            item["scenarios"] = [
                {
                    field: value[field][:300]
                    for field in ("condition", "impact", "counter")
                    if isinstance(value.get(field), str)
                }
                for value in scenarios[:16]
                if isinstance(value, dict)
            ]
        for field in ("risks", "catalysts"):
            risks = row.get(field, original.get(field))
            if not isinstance(risks, list):
                continue
            item[field] = []
            for risk in risks[:16]:
                if isinstance(risk, str):
                    item[field].append({"summary": risk[:300], "sourceIds": []})
                elif isinstance(risk, dict) and isinstance(risk.get("summary"), str):
                    refs = risk.get("sourceIds")
                    item[field].append(
                        {
                            "summary": risk["summary"][:300],
                            "sourceIds": [
                                ref for ref in refs[:24] if acceptance._token(ref)
                            ]
                            if isinstance(refs, list)
                            else [],
                        }
                    )
        cycle = row.get("industryCycle", original.get("industryCycle"))
        if isinstance(cycle, dict):
            item["industryCycle"] = acceptance._cycle_projection(cycle)
        if row.get("importance") in {"HIGH", "MEDIUM", "LOW"}:
            item["importance"] = row["importance"]
        if isinstance(row.get("sourceIds"), list):
            item["sourceIds"] = [
                value for value in row["sourceIds"][:24] if acceptance._token(value)
            ]
        if isinstance(row.get("sourceEvidence"), list):
            item["sourceEvidence"] = acceptance._accepted(
                {"sources": row["sourceEvidence"]}
            )["sources"]
        if admin:
            for field in ("path", "businessKey"):
                if isinstance(row.get(field), str):
                    item[field] = row[field][:1000]
            if isinstance(row.get("dependencies"), list):
                item["dependencies"] = acceptance._issues(
                    [{"dependencies": row["dependencies"]}], admin=True
                )[0]["dependencies"]
        output.append(item)
    return output


def _annex_excerpt(rows, projected):
    def shortened(before, after):
        if (
            isinstance(before, str)
            and isinstance(after, dict)
            and isinstance(after.get("summary"), str)
        ):
            return len(after["summary"]) < len(before)
        if isinstance(before, dict) and isinstance(after, dict):
            return any(
                shortened(before.get(key), value)
                for key, value in after.items()
                if key not in {"url", "reason", "sourceIds"}
            )
        if isinstance(before, list) and isinstance(after, list):
            visible = [value for value in before if isinstance(value, (dict, str))]
            return len(visible) > len(after) or any(
                shortened(old, new) for old, new in zip(visible, after)
            )
        return (
            isinstance(before, str)
            and isinstance(after, str)
            and len(after) < len(before)
        )

    originals = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        original = (
            row.get("originalItem") if isinstance(row.get("originalItem"), dict) else {}
        )
        originals.append({**original, **row})
    return shortened(originals, projected)


def safe_repair_summary(
    value, *, source_run_id, now, admin=False, original_outcome=None
):
    original = original_outcome or value["originalOutcome"]
    stamp = acceptance._aware(value["validatedAt"])
    observed = acceptance._aware(now)
    strict = value.get("strictPass") is True
    blocked = value.get("safetyBlocked") is True or bool(value.get("hardBlockers"))
    report = value.get("formalReport")
    if isinstance(report, dict) and not any(
        isinstance(row, dict) and str(row.get("code", "")).startswith("HEADER_")
        for row in value.get("autoBlockers", [])
    ):
        from backend.macro.research_acceptance import _overview

        report = {
            **report,
            "macroOverview": _overview(
                report,
                [
                    row["id"]
                    for row in report.get("sources", [])
                    if isinstance(row, dict) and acceptance._token(row.get("id"))
                ],
            ),
        }
    accepted = acceptance._accepted(report)
    revision = _digest(value.get("repairRevision") or value.get("revision"))
    return {
        "sourceRunId": str(UUID(str(source_run_id))),
        "kind": "HISTORICAL_REPAIR",
        "publicationScope": "HISTORICAL_READ_ONLY",
        "actionable": False,
        "validatedAt": stamp.isoformat(),
        "originalStatus": "FAILED",
        "currentFreshness": "EXPIRED"
        if stamp.astimezone(ZoneInfo("Asia/Shanghai")).date()
        < observed.astimezone(ZoneInfo("Asia/Shanghai")).date()
        else "HISTORICAL_ONLY",
        "decision": value.get("decision")
        if value.get("decision") in {"NO_CHANGE", "BLOCKED", "REPAIRED"}
        else "BLOCKED",
        "strictPass": strict,
        "autoPublishAllowed": value.get("autoPublishAllowed") is True,
        "historicalPublishAllowed": strict
        and isinstance(report, dict)
        and value.get("historicalPublishAllowed") is True
        and not blocked,
        "safetyBlocked": blocked,
        "repairVersion": acceptance._token(value.get("repairVersion")),
        "repairRevision": revision,
        "buildCommit": value.get("buildCommit", "").lower()
        if isinstance(value.get("buildCommit"), str)
        and re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", value["buildCommit"])
        else None,
        "originalOutcome": acceptance.safe_outcome_summary(original),
        "accepted": accepted,
        "acceptedContentComplete": not acceptance._has_excerpt(report, accepted)
        and not _annex_excerpt(
            value.get("quarantined"), _annex(value.get("quarantined"))
        ),
        "quarantined": _annex(value.get("quarantined"), admin=admin),
        "blockers": _annex(value.get("blockers"), admin=admin),
        "autoBlockers": _annex(value.get("autoBlockers"), admin=admin),
        "hardBlockers": _annex(value.get("hardBlockers"), admin=admin),
    }


async def _rpc(conn, name, *args):
    placeholders = ",".join(f"${index}" for index in range(1, len(args) + 1))
    try:
        return await conn.fetchval(f"SELECT research.{name}({placeholders})", *args)
    except asyncpg.InsufficientPrivilegeError:
        raise ForbiddenError(
            "需要当前有效管理员操作，并由另一位管理员独立审核"
        ) from None
    except asyncpg.CheckViolationError:
        raise ValidationError_("历史修复状态、材料身份或发布条件不满足") from None


async def _source(conn, run_id):
    from backend.macro.research_frozen_bundle import decode_frozen_bundle

    meta = acceptance._object(await _rpc(conn, "history_repair_source_admin", run_id))
    if not meta:
        raise NotFoundError("该研究不具备完整已归档的失败材料，不能历史修复")
    observed = await conn.fetchval("SELECT clock_timestamp()")
    bundle = await acceptance.read_artifacts_admin(conn, run_id)
    frozen = decode_frozen_bundle(bundle, run_id, observed_at=observed)
    if (
        frozen.validated_at != acceptance._aware(meta["validatedAt"])
        or frozen.material_hashes != meta["sourceDigests"]
        or frozen.validated_at < acceptance._aware(meta["dispatchedAt"])
    ):
        raise ValidationError_("原始材料身份或冻结时刻不一致")
    return meta, frozen, observed


async def _repair(conn, run_id):
    from backend.macro.research_acceptance_replay import (
        check_frozen_contract,
        run_offline_repair,
    )

    meta, frozen, observed = await _source(conn, run_id)
    check_frozen_contract(frozen.packet)
    value = await run_offline_repair(frozen.repair_payload())
    if (
        not isinstance(value, dict)
        or acceptance._aware(value.get("validatedAt")) != frozen.validated_at
        or str(value.get("originalOutcome", {}).get("researchId")) != str(run_id)
    ):
        raise OfflineReplayError("INVALID_OUTPUT")
    if len(acceptance._encode(value).encode("utf-8")) > RESULT_LIMIT:
        raise OfflineReplayError("OUTPUT_LIMIT")
    return meta, value, observed


async def preview_history_repair(conn, run_id, actor, tenant, now):
    if not await _rpc(conn, "try_history_repair_locks", run_id):
        raise RateLimitedError("历史修复正在执行，请稍后重试")
    try:
        meta, value, observed = await _repair(conn, run_id)
    except (OfflineReplayError, ValueError) as error:
        raise _failure(
            error.code if isinstance(error, OfflineReplayError) else "MATERIALS_INVALID"
        ).error from None
    result = safe_repair_summary(
        value,
        source_run_id=run_id,
        now=observed,
        admin=True,
        original_outcome=meta["originalOutcome"],
    )
    result.update(
        state="PREVIEW",
        canGenerate=True,
        canReview=False,
        canReject=False,
        canPublish=False,
        canInspectRepairDetails=True,
        sourceDigests=meta["sourceDigests"],
    )
    logger.info(
        "history_repair_preview source_run_id=%s actor_id=%s tenant_id=%s decision=%s",
        run_id,
        actor,
        tenant,
        result["decision"],
    )
    return result


def _failure(code):
    if code == "INCOMPATIBLE":
        return ReplayFailure(ValidationError_("该冻结契约版本暂不受支持"))
    if code in {"BUSY", "ACTOR_LIMIT", "RUN_LIMIT"}:
        return ReplayFailure(
            RateLimitedError(
                {
                    "BUSY": "历史修复正在执行，请稍后重试同一请求",
                    "ACTOR_LIMIT": "今日离线校验已达到每人10次上限",
                    "RUN_LIMIT": "该研究离线校验已达到20次或20 MiB总量上限",
                }[code]
            )
        )
    if code == "NOT_FOUND":
        return ReplayFailure(
            NotFoundError("该研究不具备完整已归档的失败材料，不能历史修复")
        )
    if code == "MATERIALS_INVALID":
        return ReplayFailure(
            ValidationError_("历史修复材料缺失、身份不符或哈希核验失败")
        )
    return ReplayFailure(
        DataUnavailableError(
            "历史修复超时，已停止校验"
            if code == "TIMEOUT"
            else "历史修复已取消并停止校验"
            if code == "CANCELLED"
            else "历史修复未能完成，未生成或发布衍生记录"
        )
    )


async def _retry_failure(conn, run_id, code):
    # INCOMPATIBLE cannot be added to historical attempt CHECK constraints.
    # Re-read only the immutable contract; never re-run or charge a same key.
    if code == "VALIDATOR_FAILED":
        from backend.macro.research_acceptance_replay import check_frozen_contract

        try:
            _, frozen, _ = await _source(conn, run_id)
            check_frozen_contract(frozen.packet)
        except (OfflineReplayError, NotFoundError, ValidationError_, ValueError) as error:
            return _failure(
                error.code
                if isinstance(error, OfflineReplayError)
                else "MATERIALS_INVALID"
            )
    return _failure(code)


async def generate_history_repair(conn, run_id, actor, tenant, command_key, now):
    from backend.macro.research_acceptance import validator_revision

    reserved = acceptance._object(
        await _rpc(
            conn, "reserve_history_repair", run_id, command_key, validator_revision()
        )
    )
    status = reserved.get("status")
    if status == "CONFLICT":
        raise IdempotencyConflictError("该命令已用于另一份历史修复")
    if status == "COMPLETE":
        return await read_history_repair(
            conn, UUID(reserved["result_id"]), now, admin=True
        )
    if status != "RUNNING":
        return await _retry_failure(
            conn,
            run_id,
            status
            if status in {"BUSY", "ACTOR_LIMIT", "RUN_LIMIT", "NOT_FOUND"}
            else reserved.get("error_code")
        )
    if reserved.get("newAttempt") is not True:
        return _failure("BUSY")
    # A committed RUNNING attempt is never resumed by launching another child.
    # Normal calls insert/complete within one request transaction; concurrent calls fail the try-lock.
    attempt_id = UUID(reserved["id"])
    try:
        meta, value, observed = await _repair(conn, run_id)
        summary = safe_repair_summary(
            value,
            source_run_id=run_id,
            now=observed,
            original_outcome=meta["originalOutcome"],
        )
        encoded, safe = acceptance._encode(value), acceptance._encode(summary)
        sizes = await conn.fetchrow(
            "SELECT octet_length($1::jsonb::text)+octet_length($2::jsonb::text)+octet_length($3::jsonb::text) AS total_bytes,octet_length($2::jsonb::text) AS summary_bytes",
            encoded,
            safe,
            acceptance._encode(meta["sourceDigests"]),
        )
        if (
            sizes["total_bytes"] > RESULT_LIMIT
            or sizes["summary_bytes"] > acceptance.OUTCOME_LIMIT
        ):
            raise OfflineReplayError("OUTPUT_LIMIT")
    except (
        OfflineReplayError,
        NotFoundError,
        ValidationError_,
        ValueError,
        OSError,
    ) as error:
        code = (
            error.code
            if isinstance(error, OfflineReplayError)
            else "MATERIALS_INVALID"
            if isinstance(error, (NotFoundError, ValidationError_, ValueError))
            else "VALIDATOR_FAILED"
        )
        await _rpc(
            conn,
            "finish_history_repair_failure",
            attempt_id,
            "VALIDATOR_FAILED" if code == "INCOMPATIBLE" else code,
        )
        return _failure(code)
    try:
        async with conn.transaction():
            identity = await _rpc(
                conn,
                "save_history_repair",
                attempt_id,
                encoded,
                safe,
                sha256(encoded.encode("utf-8")).hexdigest(),
            )
    except ValidationError_:
        await _rpc(conn, "finish_history_repair_failure", attempt_id, "INVALID_OUTPUT")
        return _failure("INVALID_OUTPUT")
    return await read_history_repair(conn, identity, now, admin=True)


def _dto(raw, *, now, admin=False):
    if not raw:
        return None
    value = acceptance._object(raw)
    stored = acceptance._object(value["summary"])
    allowed = (
        "sourceRunId",
        "kind",
        "publicationScope",
        "actionable",
        "validatedAt",
        "originalStatus",
        "decision",
        "strictPass",
        "autoPublishAllowed",
        "historicalPublishAllowed",
        "safetyBlocked",
        "repairVersion",
        "repairRevision",
        "buildCommit",
        "acceptedContentComplete",
    )
    summary = {field: stored[field] for field in allowed if field in stored}
    summary.update(
        kind="HISTORICAL_REPAIR",
        actionable=False,
        publicationScope="HISTORICAL_READ_ONLY",
        originalStatus="FAILED",
    )
    summary["accepted"] = acceptance._accepted(stored.get("accepted"))
    summary["originalOutcome"] = acceptance.safe_outcome_summary(
        stored["originalOutcome"]
    )
    for field in ("quarantined", "blockers", "autoBlockers", "hardBlockers"):
        summary[field] = _annex(stored.get(field))
    stamp = acceptance._aware(value["validatedAt"])
    summary["currentFreshness"] = (
        "EXPIRED"
        if stamp.astimezone(ZoneInfo("Asia/Shanghai")).date()
        < acceptance._aware(now).astimezone(ZoneInfo("Asia/Shanghai")).date()
        else "HISTORICAL_ONLY"
    )
    for field in (
        "id",
        "sourceRunId",
        "state",
        "createdAt",
        "validatedAt",
        "canInspectRepairDetails",
        "canGenerate",
        "canReview",
        "canReject",
        "canPublish",
    ):
        summary[field] = value.get(field)
    if admin:
        private = acceptance._object(value.get("privateResult"))
        for field in ("creatorId", "reviewerId", "candidateDigest", "sourceDigests"):
            summary[field] = value.get(field)
        for field in ("quarantined", "blockers", "autoBlockers", "hardBlockers"):
            summary[field] = _annex(private.get(field), admin=True)
        retained = private.get("retained")
        if isinstance(retained, dict):
            summary["retained"] = {
                "counts": retained.get("counts", {}),
                "directionKeys": [
                    item
                    for item in retained.get("directionKeys", [])[:8]
                    if acceptance._token(item)
                ],
                "planIdentities": [],
            }
    return summary


async def read_history_repair(conn, derivation_id, now, *, admin=False):
    return _dto(
        await _rpc(conn, "read_history_repair", derivation_id, admin),
        now=now,
        admin=admin,
    )


async def read_history_repair_details(conn, derivation_id, now):
    value = acceptance._object(
        await _rpc(conn, "read_history_repair", derivation_id, True)
    )
    if not value:
        return None
    source = acceptance._object(
        await _rpc(conn, "history_repair_source_admin", UUID(value["sourceRunId"]))
    )
    return {
        **{
            field: value[field]
            for field in (
                "id",
                "sourceRunId",
                "state",
                "validatedAt",
                "createdAt",
                "candidateDigest",
                "sourceDigests",
            )
        },
        "publicationScope": "HISTORICAL_READ_ONLY",
        "actionable": False,
        "provenance": {
            field: source.get(field)
            for field in (
                "initialRecordId",
                "dispatchedAt",
                "validatedAt",
                "sourceDigests",
            )
        },
        "privateResult": acceptance._object(value["privateResult"]),
    }


async def list_history_repairs(conn, source_run_id, now, *, admin=False):
    rows = await _rpc(conn, "list_history_repairs", source_run_id, admin, 20)
    rows = json.loads(rows) if isinstance(rows, str) else rows
    return [_dto(row, now=now, admin=admin) for row in rows or []]


async def _event(conn, identity, command_key, kind, now):
    result = acceptance._object(
        await _rpc(conn, "record_history_repair_event", identity, command_key, kind)
    )
    if result.get("status") == "CONFLICT":
        raise IdempotencyConflictError("该命令已用于另一历史审核或发布操作")
    if result.get("status") == "NOT_FOUND":
        raise NotFoundError("历史修复不存在")
    return await read_history_repair(conn, identity, now, admin=True)


async def review_history_repair(
    conn, identity, actor, tenant, command_key, decision, now
):
    if decision not in {"APPROVE", "REJECT"}:
        raise ValidationError_("未知历史修复审核动作")
    return await _event(conn, identity, command_key, decision, now)


async def publish_history_repair(conn, identity, actor, tenant, command_key, now):
    return await _event(conn, identity, command_key, "PUBLISH", now)
