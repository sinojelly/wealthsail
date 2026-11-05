"""Immutable, tenant-scoped topic observations. Never a formal-report consumer."""

import base64
from datetime import date
import json
import re

from backend.errors import ForbiddenError, NotFoundError, ValidationError_
from backend.macro import research_acceptance_repository as acceptance

STATUSES = ("FORMAL", "OBSERVATION_ONLY", "PENDING_REVIEW", "QUARANTINED")
REGISTRY_LIMIT = 1024 * 1024
ITEM_LIMIT = 16 * 1024


class TopicRegistrySizeError(ValidationError_):
    """Only an explicit resource boundary may omit the optional sidecar."""


def _oversize():
    raise TopicRegistrySizeError("主题目录超过有界存储预算，原始材料仍保留")


def _invalid():
    raise ValidationError_("主题目录结构、身份或存储预算不满足要求")


def _text(value, maximum=1000, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str):
        _invalid()
    if len(value) > maximum:
        _oversize()
    return value


def _texts(value, maximum=64, length=600):
    if not isinstance(value, list):
        _invalid()
    if len(value) > maximum:
        _oversize()
    return [_text(item, length) for item in value]


def _tasks(value):
    if not isinstance(value, list):
        _invalid()
    if len(value) > 2880:
        _oversize()
    result = []
    for task in value:
        if not isinstance(task, dict):
            _invalid()
        window = task.get("window")
        if not isinstance(window, dict):
            _invalid()
        try:
            start, end = [date.fromisoformat(window[key]) for key in ("start", "end")]
        except (ValueError, TypeError, KeyError):
            _invalid()
        days = window.get("lookbackDays")
        if type(days) is not int or not 1 <= days <= 366 or start > end:
            _invalid()
        if task.get("status") not in {
            "PLANNED",
            "DEFERRED",
            "PENDING_REVIEW",
            "PLANNED_DEFERRED",
        }:
            _invalid()
        if not re.fullmatch(r"audit:[a-f0-9]{32}", str(task.get("taskId"))):
            _invalid()
        from backend.macro.industry_cycle import RISK_CHECKS

        kind = _text(task.get("kind"), 80)
        if kind not in RISK_CHECKS or task.get("reasonCode") not in {
            None,
            "AUDIT_BUDGET_DEFERRED",
            "AUDIT_INCOMPLETE",
            "INDUSTRY_IDENTITY_PENDING",
        }:
            _invalid()
        # The producer owns the five-kind vocabulary; no arbitrary task bodies survive.
        projected = {
            "taskId": task["taskId"],
            "industryId": _text(task.get("industryId"), 120),
            "kind": kind,
            "query": _text(task.get("query"), 1000),
            "window": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "lookbackDays": days,
            },
            "status": task["status"],
            "reasonCode": _text(task.get("reasonCode"), 80, optional=True),
        }
        if task.get("searchTerm") is not None:
            projected["searchTerm"] = _text(task["searchTerm"], 200)
        result.append(projected)
    return result


def prepare_topic_registry(value, run_id, validated_at):
    """Project only observation fields, refusing truncation and forged provenance."""
    from backend.macro.research_topics import TOPIC_MESSAGES

    if not isinstance(value, dict) or str(value.get("researchId")) != str(run_id):
        _invalid()
    try:
        stamp = acceptance._aware(value.get("validatedAt"))
        if stamp != acceptance._aware(validated_at):
            _invalid()
    except (TypeError, ValueError):
        _invalid()
    rows = value.get("items")
    if not isinstance(rows, list):
        _invalid()
    if len(rows) > 72:
        _oversize()
    items, identities = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in STATUSES:
            _invalid()
        identity = row.get("topicId")
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"topic:[a-f0-9]{32}", identity)
            or identity in identities
        ):
            _invalid()
        identities.add(identity)
        eligible = row.get("investmentEligible")
        if type(eligible) is not bool or (eligible and row["status"] != "FORMAL"):
            _invalid()
        reason = row.get("reasonCode")
        if reason not in TOPIC_MESSAGES:
            _invalid()
        match = row.get("match")
        if not isinstance(match, dict) or match.get("status") not in {
            "STANDARD",
            "STRICT_ALIAS",
            "COMPOSITE",
            "NEW_TOPIC",
            "UNMATCHED",
        }:
            _invalid()
        item = {
            "topicId": identity,
            "key": _text(row.get("key"), 120),
            "title": _text(row.get("title"), 200),
            "summary": _text(row.get("summary"), 4000),
            "status": row["status"],
            "reasonCode": reason,
            "reason": TOPIC_MESSAGES[reason],
            "impact": _text(row.get("impact"), 4000),
            "measures": _texts(row.get("measures"), 32, 1000),
            "pendingTasks": _tasks(row.get("pendingTasks")),
            "sourceIds": _texts(row.get("sourceIds"), 24, 80),
            "match": {
                "status": match["status"],
                "industryIds": _texts(match.get("industryIds"), 64, 120),
                "relatedIndustryIds": _texts(match.get("relatedIndustryIds"), 64, 120),
                "evidence": _texts(match.get("evidence"), 64, 200),
                "registryVersion": _text(match.get("registryVersion"), 80),
                "scopeVerified": match.get("scopeVerified")
                if type(match.get("scopeVerified")) is bool
                else None,
            },
            "investmentEligible": eligible,
        }
        for field in ("unknowns", "followUp"):
            if field in row:
                item[field] = _texts(row[field], 8, 300)
        if "insightCategory" in row:
            if row["insightCategory"] not in {"DYNAMIC", "INDUSTRY", "MULTI_SCOPE"}:
                _invalid()
            item["insightCategory"] = row["insightCategory"]
        if "evidenceCoverageScore" in row:
            score = row["evidenceCoverageScore"]
            if type(score) is not int or not 0 <= score <= 100:
                _invalid()
            item["evidenceCoverageScore"] = score
        proposals = match.get("proposedScopes", [])
        if not isinstance(proposals, list) or len(proposals) > 8:
            _invalid()
        item["match"]["unresolvedClaims"] = _texts(
            match.get("unresolvedClaims", []), 8, 200
        )
        item["match"]["proposedScopes"] = []
        for proposal in proposals:
            if not isinstance(proposal, dict) or not re.fullmatch(
                r"TOPIC_SCOPE:[a-f0-9]{32}", str(proposal.get("industryId"))
            ):
                _invalid()
            item["match"]["proposedScopes"].append(
                {
                    "industryId": proposal["industryId"],
                    "searchTerm": _text(proposal.get("searchTerm"), 200),
                    "registryVersion": _text(proposal.get("registryVersion"), 80),
                }
            )
        if len(acceptance._encode(item).encode("utf-8")) > ITEM_LIMIT:
            _oversize()
        items.append(item)
    coverage = value.get("coverage")
    if not isinstance(coverage, dict):
        _invalid()
    resource_issues = value.get("issues")
    if (
        not items
        and coverage.get("hasMore") is True
        and isinstance(resource_issues, list)
        and any(
            isinstance(issue, dict)
            and issue.get("reasonCode") == "REGISTRY_OUTPUT_LIMIT"
            for issue in resource_issues
        )
    ):
        # A bounded core can retain an explicit omission marker instead of a
        # large projection. Archive it as unavailable, never as a zero-topic
        # completed catalog. The unchanged frozen model output is authoritative.
        _oversize()
    safe_coverage = {}
    for key in (
        "plannedScopeCount",
        "reviewedScopeCount",
        "pendingScopeCount",
        "deferredScopeCount",
        "maxTopics",
        "maxFormalDirections",
        "maxScopes",
        "maxQueries",
        "maxSources",
        "maxModelCalls",
        "additionalModelCalls",
        "structureCompleteScopeCount",
        "executionVerifiedScopeCount",
        "evidenceVerifiedCheckCount",
        "executionVerifiedCheckCount",
        "strictDirectionCount",
        "autoPublishBlockerCount",
        "executionPlannedTaskCount",
        "executionMatchedTaskCount",
        "executionPendingDirectionCount",
        "discoveredTopicCount",
        "evidenceBackedTopicCount",
        "evaluableFundDirectionCount",
        "qualifiedFormalDirectionCount",
        "validEventCount",
        "validUpcomingCount",
        "unresolvedReferenceCount",
        "pendingVerificationTopicCount",
    ):
        if key in coverage:
            if type(coverage[key]) is not int or not 0 <= coverage[key] <= 10000:
                _invalid()
            safe_coverage[key] = coverage[key]
    if "evidenceBackedTopicRatioPercent" in coverage:
        ratio = coverage["evidenceBackedTopicRatioPercent"]
        if ratio is not None and (type(ratio) not in {int, float} or not 0 <= ratio <= 100):
            _invalid()
        safe_coverage["evidenceBackedTopicRatioPercent"] = ratio
    if "formalEligibilityReasons" in coverage:
        from backend.macro.research_topics import FORMAL_ELIGIBILITY_REASONS

        reasons = coverage["formalEligibilityReasons"]
        if not isinstance(reasons, dict) or any(
            key not in FORMAL_ELIGIBILITY_REASONS or type(count) is not int or not 0 <= count <= 10000
            for key, count in reasons.items()
        ):
            _invalid()
        safe_coverage["formalEligibilityReasons"] = dict(reasons)
    for key, allowed in (
        ("researchDeliveryStatus", {"VALIDATED_READ_ONLY", "OBSERVATION_READ_ONLY", "UNAVAILABLE"}),
        ("investmentQualificationStatus", {"QUALIFIED_CANDIDATES", "NO_QUALIFIED_OPPORTUNITIES", "BLOCKED"}),
    ):
        if key in coverage:
            if not isinstance(coverage[key], str) or coverage[key] not in allowed:
                _invalid()
            safe_coverage[key] = coverage[key]
    for key in ("executionObservedQueryCount", "executionUnmatchedQueryHashCount"):
        if key in coverage:
            number = coverage[key]
            if number is not None and (type(number) is not int or not 0 <= number <= 1_000_000):
                _invalid()
            safe_coverage[key] = number
    if "executionMatchStatus" in coverage:
        status = coverage["executionMatchStatus"]
        if not isinstance(status, str) or status not in {
            "EXECUTION_UNMATCHED", "EXECUTION_MATCHED", "EXECUTION_PARTIALLY_MATCHED", "EXECUTION_NOT_OBSERVED", "EXECUTION_UNVERIFIED",
        }:
            _invalid()
        safe_coverage["executionMatchStatus"] = status
    from backend.integrations.research_execution import sanitize_execution_transport_reason
    if "executionReceiptTransportReason" in coverage:
        reason = sanitize_execution_transport_reason(coverage["executionReceiptTransportReason"])
        if reason is None:
            _invalid()
        safe_coverage["executionReceiptTransportReason"] = reason
    for key, maximum in (
        ("maxModelCalls", 1),
        ("additionalModelCalls", 0),
        ("maxInputBytes", 4194304),
        ("maxOutputBytes", 2097152),
        ("maxExecutionSeconds", 1200),
    ):
        if key in coverage:
            if type(coverage[key]) is not int or not 0 <= coverage[key] <= maximum:
                _invalid()
            safe_coverage[key] = coverage[key]
    for key in ("strictRevalidated", "autoPublishAllowed"):
        if key in coverage:
            if type(coverage[key]) is not bool:
                _invalid()
            safe_coverage[key] = coverage[key]
    counts = {
        status: sum(row["status"] == status for row in items) for status in STATUSES
    }
    if coverage.get("hasMore") is True:
        reported = coverage.get("totalCount", len(items))
        if type(reported) is not int or not len(items) <= reported <= 10000:
            _invalid()
        original_counts = coverage.get("countsByStatus", counts)
        if not isinstance(original_counts, dict) or any(
            type(original_counts.get(status, 0)) is not int
            or not 0 <= original_counts.get(status, 0) <= 10000
            for status in STATUSES
        ):
            _invalid()
        counts = {status: original_counts.get(status, 0) for status in STATUSES}
    else:
        reported = len(items)
    safe_coverage.update(
        totalCount=reported,
        storedCount=len(items),
        countsByStatus=counts,
        searchExecutionVerified=coverage.get("searchExecutionVerified") is True,
        hasMore=coverage.get("hasMore") is True,
    )
    revision = value.get("revision")
    if revision is not None and (
        not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision)
    ):
        _invalid()
    build = value.get("buildCommit")
    if build is not None and (
        not isinstance(build, str)
        or not re.fullmatch(r"[a-f0-9]{40}(?:[a-f0-9]{24})?", build)
    ):
        _invalid()
    tasks = _tasks(value.get("auditTasks", []))
    pending, seen = [], set()
    for task in [*tasks, *(task for row in items for task in row["pendingTasks"])]:
        identity = (task["industryId"], acceptance._encode(task["window"]))
        if identity not in seen:
            pending.append(
                {
                    "industryId": task["industryId"],
                    "window": task["window"],
                    "taskId": task["taskId"],
                    "reasonCode": task["reasonCode"] or "AUDIT_INCOMPLETE",
                    **(
                        {"searchTerm": task["searchTerm"]}
                        if "searchTerm" in task
                        else {}
                    ),
                }
            )
            seen.add(identity)
    issues = []
    raw_issues = value.get("issues", [])
    if not isinstance(raw_issues, list) or len(raw_issues) > 1024:
        _invalid()
    for issue in raw_issues:
        if not isinstance(issue, dict) or issue.get("reasonCode") not in TOPIC_MESSAGES:
            _invalid()
        index = issue.get("itemIndex")
        issues.append(
            {
                "reasonCode": issue["reasonCode"],
                "reason": TOPIC_MESSAGES[issue["reasonCode"]],
                "itemIndex": index
                if type(index) is int and 0 <= index <= 9999
                else None,
            }
        )
    result = {
        "researchId": str(run_id),
        "validatedAt": stamp.isoformat(),
        "version": _text(value.get("version"), 80),
        "revision": revision,
        "revisionStatus": "AVAILABLE" if revision else "UNAVAILABLE",
        "buildCommit": build,
        "items": items,
        "coverage": safe_coverage,
        "auditTasks": tasks,
        "pendingScopes": pending,
        "issues": issues,
        "actionable": False,
    }
    if len(acceptance._encode(result).encode("utf-8")) > REGISTRY_LIMIT:
        _oversize()
    return result


def encode_cursor(snapshot_id, ordinal, status=None):
    return (
        base64.urlsafe_b64encode(
            acceptance._encode(
                {"snapshot": str(snapshot_id), "ordinal": ordinal, "status": status}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


def decode_cursor(cursor, snapshot_id, status=None):
    if cursor is None:
        return 0
    try:
        if not isinstance(cursor, str) or len(cursor) > 512:
            raise ValueError
        value = json.loads(
            base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            )
        )
        if (
            set(value) != {"snapshot", "ordinal", "status"}
            or value["snapshot"] != str(snapshot_id)
            or value["status"] != status
            or type(value["ordinal"]) is not int
            or not 0 <= value["ordinal"] <= 72
        ):
            raise ValueError
        return value["ordinal"]
    except (ValueError, TypeError, KeyError):
        raise ValidationError_("主题分页游标无效或不属于当前快照及筛选") from None


async def check_topic_storage_budget(conn, registry, packet_descriptor):
    """Measure every PostgreSQL JSONB byte constraint before freezing the outbox."""
    content = packet_descriptor.get("content")
    packet = json.loads(content) if content is not None else {}
    if not isinstance(packet, dict):
        _invalid()
    discovery = packet.get("researchDiscovery", {})
    if not isinstance(discovery, dict):
        _invalid()
    manifest = discovery.get("auditManifest", {})
    if not isinstance(manifest, dict):
        _invalid()
    selected = manifest.get("selectedScopes", [])
    if not isinstance(selected, list) or len(selected) > 10:
        _invalid()
    sizes = await conn.fetchrow(
        """SELECT octet_length($1::jsonb::text) AS registry_bytes,
        octet_length((($1::jsonb-'items')||jsonb_build_object('selectedScopes',$2::jsonb))::text)
          +COALESCE(sum(octet_length(item::text)),0) AS total_bytes,
        COALESCE(max(octet_length(item::text)),0) AS item_bytes
        FROM jsonb_array_elements($1::jsonb->'items') item""",
        acceptance._encode(registry),
        acceptance._encode(selected),
    )
    if sizes is None or any(
        type(sizes[key]) is not int or sizes[key] < 0
        for key in ("registry_bytes", "total_bytes", "item_bytes")
    ):
        _invalid()
    if (
        sizes["registry_bytes"] > REGISTRY_LIMIT
        or sizes["total_bytes"] > REGISTRY_LIMIT
        or sizes["item_bytes"] > ITEM_LIMIT
    ):
        _oversize()


async def save_topic_registry(conn, run_id, registry, validated_at):
    import asyncpg

    projected = prepare_topic_registry(registry, run_id, validated_at)
    try:
        return await conn.fetchval(
            "SELECT research.save_topic_registry($1,$2::jsonb)",
            run_id,
            acceptance._encode(projected),
        )
    except asyncpg.CheckViolationError:
        raise ValidationError_("主题目录与服务器冻结材料或预算不一致") from None


async def save_topic_registry_omission(conn, run_id, descriptor, validated_at):
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("content") is not None
        or descriptor.get("omissionReason") != "SIZE_LIMIT"
        or not re.fullmatch(r"[a-f0-9]{64}", str(descriptor.get("sha256")))
        or type(descriptor.get("byteLength")) is not int
        or descriptor["byteLength"] < 0
    ):
        _invalid()
    value = {
        "researchId": str(run_id),
        "validatedAt": acceptance._aware(validated_at).isoformat(),
        "items": [],
        "registryAvailable": False,
        "topicArchiveIssue": "REGISTRY_OUTPUT_LIMIT",
        "omission": descriptor,
        "actionable": False,
    }
    return await conn.fetchval(
        "SELECT research.save_topic_registry($1,$2::jsonb)",
        run_id,
        acceptance._encode(value),
    )


async def read_topic_registry(
    conn, run_id, now=None, *, limit=12, cursor=None, status=None
):
    if limit not in {12, 20, 30} or (status is not None and status not in STATUSES):
        raise ValidationError_("主题分页数量或状态无效")
    allowed = await conn.fetchval(
        "SELECT research.validation_live_member(research.validation_current_tenant(),false)"
    )
    if not allowed:
        raise ForbiddenError("需要当前有效租户成员身份")
    snapshot = await conn.fetchrow(
        "SELECT * FROM research.research_topic_snapshots WHERE run_id=$1", run_id
    )
    if snapshot is None:
        pending = await conn.fetchrow(
            "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
            run_id,
        )
        if pending and pending["status"] != "ARCHIVED":
            return {
                "researchId": str(run_id),
                "registryAvailable": False,
                "archiveState": pending["status"],
                "items": [],
                "actionable": False,
            }
        raise NotFoundError("该研究没有主题目录存档")
    snapshot = dict(snapshot)
    after = decode_cursor(cursor, snapshot["id"], status)
    rows = await conn.fetch(
        "SELECT ordinal,body FROM research.research_topic_entries WHERE snapshot_id=$1 AND ordinal>$2 AND ($3::text IS NULL OR status=$3) ORDER BY ordinal LIMIT $4",
        snapshot["id"],
        after,
        status,
        limit + 1,
    )
    count = await conn.fetchval(
        "SELECT count(*) FROM research.research_topic_entries WHERE snapshot_id=$1 AND ($2::text IS NULL OR status=$2)",
        snapshot["id"],
        status,
    )
    meta = acceptance._object(snapshot["metadata"])
    if meta.get("registryAvailable") is False:
        admin = await conn.fetchval(
            "SELECT research.validation_live_member(research.validation_current_tenant(),true)"
        )
        return {
            "researchId": str(run_id),
            "snapshotId": str(snapshot["id"]),
            "registryAvailable": False,
            "archiveState": "UNRECOVERABLE",
            "topicArchiveIssue": "REGISTRY_OUTPUT_LIMIT",
            "reason": "主题目录超过存储预算，完整原始材料仍可由管理员核对。",
            "items": [],
            "actionable": False,
            "canInspectArtifacts": bool(admin),
            "artifactsUrl": f"/investment/daily-research/validation/{run_id}/artifacts"
            if admin
            else None,
        }
    page = rows[:limit]
    more = len(rows) > limit
    safe_meta = {
        key: meta[key]
        for key in (
            "version",
            "revision",
            "revisionStatus",
            "buildCommit",
            "coverage",
            "auditTasks",
            "issues",
        )
        if key in meta
    }
    safe_items = prepare_topic_registry(
        {
            **safe_meta,
            "researchId": str(run_id),
            "validatedAt": acceptance._aware(snapshot["validated_at"]).isoformat(),
            "items": [acceptance._object(row["body"]) for row in page],
        },
        run_id,
        snapshot["validated_at"],
    )["items"]
    return {
        **safe_meta,
        "snapshotId": str(snapshot["id"]),
        "researchId": str(run_id),
        "validatedAt": acceptance._aware(snapshot["validated_at"]).isoformat(),
        "registryAvailable": True,
        "archiveState": "ARCHIVED",
        "items": safe_items,
        "totalCount": snapshot["total_count"],
        "filteredCount": count,
        "countsByStatus": meta["coverage"]["countsByStatus"],
        "actionable": False,
        "hasMore": more,
        "nextCursor": encode_cursor(snapshot["id"], page[-1]["ordinal"], status)
        if more
        else None,
    }


async def read_pending_topic_scopes(conn, tenant_id=None, limit=64):
    if type(limit) is not int or not 1 <= limit <= 64:
        raise ValidationError_("待审范围读取数量无效")
    rows = await conn.fetch(
        """WITH visible AS (
        SELECT run_id,validated_at,metadata FROM research.research_topic_snapshots WHERE tenant_id IS NULL OR tenant_id=$1::uuid),
        selected AS (SELECT scope,max(validated_at) AS last_planned FROM visible,
          LATERAL jsonb_array_elements_text(COALESCE(metadata->'selectedScopes','[]'::jsonb)) scope GROUP BY scope),
        pending AS (SELECT DISTINCT ON (task->>'industryId') run_id,validated_at,task FROM visible,
          LATERAL jsonb_array_elements(COALESCE(metadata->'pendingScopes','[]'::jsonb)) task
          ORDER BY task->>'industryId',validated_at,run_id)
        SELECT p.run_id,p.task FROM pending p LEFT JOIN selected s ON s.scope=p.task->>'industryId'
        ORDER BY s.last_planned ASC NULLS FIRST,p.validated_at,p.task->>'industryId' LIMIT $2""",
        tenant_id,
        limit,
    )
    return [
        {**acceptance._object(row["task"]), "sourceRunId": str(row["run_id"])}
        for row in rows
    ]
