"""Deterministic V2 discovery acceptance; only a strict projection is actionable."""

from collections import Counter
from copy import deepcopy
import json
import re

from pydantic import ValidationError

from backend.macro.daily_research import (
    ResearchValidationError,
    _now,
    _previous_industry_states,
    _url,
    validate_report,
)
from backend.macro.industry_contract import plan_industry_ids
from backend.macro.research_acceptance import (
    _parse,
    build_revision,
    validate_report_outcome,
    validator_revision,
)
from backend.macro.research_topics import (
    MAX_REGISTRY_ITEMS,
    REGISTRY_VERSION,
    TOPIC_MESSAGES,
    TopicProposal,
    digest,
    freeze_discovery_packet,
    frozen_manifest,
    match_topic,
    topic_tasks,
)


def _issue(code, *, key=None, index=None):
    return {
        "reasonCode": code,
        "reason": TOPIC_MESSAGES[code],
        "key": key,
        "itemIndex": index,
    }


_SYSTEMIC = re.compile(
    r"系统性风险|金融危机|全行业.{0,8}(?:信用)?违约|systemic risk|financial crisis",
    re.IGNORECASE,
)


def evaluate_research_batch(
    raw,
    packet,
    checks,
    *,
    research_id,
    validated_at,
    rule_version=None,
    _strict_validator=None,
):
    """Preserve every bounded proposal while withholding unreviewed fund plans."""
    from backend.macro.research_repair import (
        _NEGATIVE,
        _annex,
        _block,
        _clearly_nonnegative,
        _has_cycle_risk,
        _protection_unchanged,
        _text,
    )

    stamp = _now(validated_at)
    current_revision = validator_revision()
    if rule_version is not None and (
        not isinstance(rule_version, str) or rule_version != current_revision
    ):
        raise ValueError("unsupported research rule version")
    packet = freeze_discovery_packet(packet, stamp)
    manifest = frozen_manifest(packet)
    original = validate_report_outcome(
        raw,
        packet,
        checks,
        research_id=research_id,
        completed_at=stamp,
        _strict_validator=_strict_validator,
    )
    registry = {
        "version": REGISTRY_VERSION,
        "researchId": str(research_id),
        "validatedAt": stamp.isoformat(),
        "revision": current_revision,
        "buildCommit": build_revision(),
        "items": [],
        "coverage": {},
        "auditTasks": [],
        "issues": [],
    }
    result = {
        "originalOutcome": original,
        "formalReport": None,
        "registry": registry,
        "repairSummary": {},
        "autoPublishAllowed": False,
        "candidateRaw": None,
        "safetyBlocked": False,
        "hardBlockers": [],
        "autoBlockers": [],
        "quarantined": [],
    }
    try:
        parsed = _parse(raw)
        from backend.macro.daily_research import _safe_narrative

        _safe_narrative(parsed)
        if not isinstance(checks, dict):
            raise ValueError("missing source verification")
    except (ValueError, TypeError, RecursionError):
        result["hardBlockers"].append(_block("UNSAFE_INPUT"))
        parsed = {}
    raw_topics = parsed.get("topicCatalog", [])
    # Scan the original bounded JSON before schema isolation or resource
    # omission can replace an item's text. This is an unresolved signal only.
    if _SYSTEMIC.search(json.dumps(raw_topics, ensure_ascii=False, allow_nan=False)):
        result["autoBlockers"].append(_block("NARRATIVE_RISK_REVIEW_REQUIRED"))
    directions = parsed.get("valueDirections", [])
    if (
        not isinstance(raw_topics, list)
        or len(raw_topics) > 64
        or not isinstance(directions, list)
        or len(directions) > 8
    ):
        result["hardBlockers"].append(_block("UNSAFE_INPUT"))
        raw_topics, directions = [], []
    accepted = original.accepted
    if any(not isinstance(row, dict) for row in raw_topics):
        accepted = validate_report_outcome(
            {**parsed, "topicCatalog": []},
            packet,
            checks,
            research_id=research_id,
            completed_at=stamp,
            _strict_validator=_strict_validator,
        ).accepted
    from backend.macro.industry_cycle import RISK_CHECKS

    structured_reviews = {
        row["industry"]: row for row in accepted.get("industryCycleReviews", [])
    }
    from backend.macro.research_assurance import audit_assurance

    assurance = audit_assurance(
        structured_reviews, manifest, checks, stamp, research_id=research_id
    )
    completed_kinds = assurance["completedKinds"]
    reviews = {
        industry
        for industry, kinds in completed_kinds.items()
        if kinds == set(RISK_CHECKS)
    }
    proofs_by_url = {row["url"]: row for row in accepted.get("sources", [])}
    sources = {}
    for row in (
        parsed.get("sources", []) if isinstance(parsed.get("sources", []), list) else []
    ):
        if not isinstance(row, dict):
            continue
        try:
            uri = _url(row.get("url"))
        except ValueError:
            continue
        if uri in proofs_by_url:
            sources[row.get("id")] = {**proofs_by_url[uri], "id": row.get("id")}
    systemic_unresolved = any(
        isinstance(row, dict)
        and _SYSTEMIC.search(
            str(row.get("title", "")) + " " + str(row.get("quote", ""))
        )
        for row in parsed.get("sources", [])
        if isinstance(parsed.get("sources"), list)
    )
    if systemic_unresolved:
        result["autoBlockers"].append(_block("NARRATIVE_RISK_REVIEW_REQUIRED"))
    retained, removed, changed = [], set(), False
    direction_states = {}
    direction_required = {}
    history = _previous_industry_states(packet, stamp)
    for index, raw_direction in enumerate(directions):
        from backend.macro.value_directions import ValueDirectionProposal

        try:
            direction = ValueDirectionProposal.model_validate(
                raw_direction
            ).model_dump()
        except ValidationError:
            if (
                not isinstance(raw_direction, dict)
                or not re.fullmatch(
                    r"[a-z][a-z0-9_-]{0,63}", str(raw_direction.get("key") or "")
                )
                or not isinstance(raw_direction.get("sourceIds"), list)
                or any(not isinstance(ref, str) for ref in raw_direction["sourceIds"])
            ):
                result["hardBlockers"].append(_block("UNSAFE_INPUT"))
                continue
            removed.add(raw_direction["key"])
            changed = True
            direction_states[raw_direction["key"]] = "FORMAL_DIRECTION_PENDING"
            raw_plans = raw_direction.get("plans")
            direction_required[raw_direction["key"]] = (
                {
                    industry
                    for plan in raw_plans
                    if isinstance(plan, dict)
                    for industry in plan_industry_ids(packet, plan)
                }
                if isinstance(raw_plans, list)
                else set()
            )
            cycle = raw_direction.get("industryCycle")
            cycle_checks = cycle.get("riskChecks") if isinstance(cycle, dict) else None
            if isinstance(cycle_checks, list) and any(
                isinstance(check, dict) and check.get("status") == "FOUND"
                for check in cycle_checks
            ):
                result["hardBlockers"].append(_block("NEGATIVE_RISK_QUARANTINE"))
            if ("key", raw_direction["key"]) in history:
                result["hardBlockers"].append(_block("HISTORICAL_RISK_QUARANTINE"))
            result["quarantined"].append(
                _annex(
                    raw_direction,
                    index,
                    "valueDirections",
                    "WATCH_REVIEW_UNLINKED",
                    sources,
                )
            )
            if raw_direction.get("risks") or _NEGATIVE.search(_text(raw_direction)):
                result["autoBlockers"].append(
                    _block(
                        "NARRATIVE_RISK_REVIEW_REQUIRED",
                        path=f"valueDirections[{index}]",
                    )
                )
            continue
        required = {
            code
            for plan in direction["plans"]
            for code in plan_industry_ids(packet, plan)
        }
        direction_required[direction["key"]] = required
        exact = bool(direction["plans"]) and all(
            plan_industry_ids(packet, plan) for plan in direction["plans"]
        )
        declared = set(direction["reviewIndustryIds"])
        if exact and required <= reviews:
            # The only automatic link repair comes from exact server products.
            corrected = sorted((declared & reviews) | required)
            changed |= set(corrected) != declared
            direction["reviewIndustryIds"] = corrected
            retained.append(direction)
        elif not direction["plans"] and declared and declared <= reviews:
            retained.append(direction)
        else:
            removed.add(direction["key"])
            changed = True
            direction_states[direction["key"]] = "FORMAL_DIRECTION_PENDING"
            result["quarantined"].append(
                _annex(
                    raw_direction,
                    index,
                    "valueDirections",
                    "WATCH_REVIEW_UNLINKED",
                    sources,
                )
            )
            if _has_cycle_risk(direction) or ("key", direction["key"]) in history:
                result["hardBlockers"].append(
                    _block(
                        "HISTORICAL_RISK_QUARANTINE"
                        if ("key", direction["key"]) in history
                        else "NEGATIVE_RISK_QUARANTINE",
                        path=f"valueDirections[{index}]",
                    )
                )
            if direction.get("risks") or _NEGATIVE.search(_text(direction)):
                result["autoBlockers"].append(
                    _block(
                        "NARRATIVE_RISK_REVIEW_REQUIRED",
                        path=f"valueDirections[{index}]",
                    )
                )
    from backend.macro.value_directions import validate_value_directions

    canonical_sources = {row["id"]: row for row in accepted.get("sources", [])}
    aliases = {
        raw_id: proofs_by_url[source["url"]]["id"] for raw_id, source in sources.items()
    }
    validated_retained = []
    for direction in retained:
        try:
            validate_value_directions(
                [direction],
                packet,
                stamp,
                aliases,
                sources=canonical_sources,
                industry_reviews=accepted.get("industryCycleReviews", []),
                historical_cycles=history,
            )
        except ValueError:
            index = next(
                index
                for index, row in enumerate(directions)
                if isinstance(row, dict) and row.get("key") == direction["key"]
            )
            raw_direction = directions[index]
            removed.add(direction["key"])
            changed = True
            direction_states[direction["key"]] = "FORMAL_DIRECTION_PENDING"
            result["quarantined"].append(
                _annex(
                    raw_direction, index, "valueDirections", "NO_SAFE_REPAIR", sources
                )
            )
            if _has_cycle_risk(direction) or ("key", direction["key"]) in history:
                result["hardBlockers"].append(_block("NEGATIVE_RISK_QUARANTINE"))
            if direction.get("risks") or _NEGATIVE.search(_text(direction)):
                result["autoBlockers"].append(_block("NARRATIVE_RISK_REVIEW_REQUIRED"))
        else:
            validated_retained.append(direction)
    retained = validated_retained
    candidate = deepcopy(parsed)
    candidate["valueDirections"] = retained
    # Discovery proposals are archived separately; they are not formal directions.
    candidate["topicCatalog"] = []
    for component, kind in (("events", "EVENT"), ("upcoming", "UPCOMING")):
        kept = []
        for index, row in enumerate(parsed.get(component, [])):
            if not isinstance(row, dict):
                result["hardBlockers"].append(_block("UNSAFE_INPUT"))
                continue
            refs = set(row.get("affectedDirectionIds") or []) & removed
            if not refs:
                kept.append(row)
                continue
            changed = True
            result["quarantined"].append(
                _annex(
                    row,
                    index,
                    component,
                    f"DEPENDENT_{kind}_QUARANTINED",
                    sources,
                    dependencies=[
                        {"itemType": "VALUE_DIRECTION", "businessKey": key}
                        for key in sorted(refs)
                    ],
                )
            )
            text = (
                _text(row)
                + " "
                + " ".join(
                    sources[ref].get("quote", "")
                    for ref in row.get("sourceIds", [])
                    if ref in sources
                )
            )
            if (
                row.get("importance") != "LOW"
                or not _clearly_nonnegative(row)
                or _NEGATIVE.search(text)
            ):
                result["autoBlockers"].append(
                    _block(
                        "HIGH_IMPORTANCE_DEPENDENCY"
                        if row.get("importance") == "HIGH"
                        else "DEPENDENT_NEGATIVE_UNPROVEN",
                        path=f"{component}[{index}]",
                    )
                )
        candidate[component] = kept
    if removed:
        narrative = parsed.get("narrativeDirectionIds")
        if narrative is None:
            result["autoBlockers"].append(_block("HEADER_PROVENANCE_MISSING"))
        elif any(set(refs) & removed for refs in narrative.values()):
            result["autoBlockers"].append(_block("HEADER_DEPENDENCY_QUARANTINED"))
        header = json.dumps(
            {key: parsed.get(key) for key in ("summary", "macroCycle", "coverage")},
            ensure_ascii=False,
        )
        if any(
            row["key"] in removed and (row["key"] in header or row["title"] in header)
            for row in directions
            if isinstance(row, dict)
            and isinstance(row.get("key"), str)
            and isinstance(row.get("title"), str)
        ):
            result["autoBlockers"].append(_block("HEADER_DEPENDENCY_QUARANTINED"))
    formal = None
    if parsed and not any(
        row["code"] == "UNSAFE_INPUT" for row in result["hardBlockers"]
    ):
        try:
            formal = (_strict_validator or validate_report)(
                candidate, packet, checks, completed_at=stamp
            )
        except ResearchValidationError:
            result["hardBlockers"].append(_block("STRICT_REVALIDATION_FAILED"))
        else:
            if not _protection_unchanged(accepted, formal, candidate):
                result["hardBlockers"].append(_block("RETAINED_PROTECTION_CHANGED"))
    result["candidateRaw"] = candidate if formal is not None else None
    formal_by_key = {
        row["key"]: row for row in (formal or {}).get("valueDirections", [])
    }
    proposals = list(raw_topics)
    retained_by_key = {row["key"]: row for row in retained}
    retained_scopes = {
        industry
        for row in retained
        for industry in direction_required.get(row["key"], set())
    }
    retained_refs = {ref for row in retained for ref in row.get("sourceIds", [])}
    topic_keys = {
        row["key"]
        for row in proposals
        if isinstance(row, dict) and isinstance(row.get("key"), str)
    }
    for direction in directions:
        if (
            not isinstance(direction, dict)
            or not isinstance(direction.get("key"), str)
            or direction["key"] in topic_keys
        ):
            continue
        proposals.append(
            {
                "key": direction.get("key"),
                "title": direction.get("title"),
                "thesis": direction.get("thesis"),
                "industryClaims": retained_by_key.get(
                    direction.get("key"), direction
                ).get("reviewIndustryIds", []),
                "relatedIndustryIds": [],
                "sourceIds": direction.get("sourceIds", []),
                "unknowns": direction.get("watchFor", []),
                "pendingTasks": [],
            }
        )
    discovered_count = len(proposals)
    if len(proposals) > MAX_REGISTRY_ITEMS:
        registry["issues"].append(_issue("REGISTRY_OUTPUT_LIMIT"))
        proposals = []
    counts = Counter(
        row["key"]
        for row in proposals
        if isinstance(row, dict) and isinstance(row.get("key"), str)
    )
    for index, raw_topic in enumerate(proposals):
        try:
            proposal = TopicProposal.model_validate(raw_topic).model_dump()
            from backend.macro.daily_research import _safe_narrative

            _safe_narrative(proposal)
        except (ValidationError, ResearchValidationError):
            registry["issues"].append(_issue("TOPIC_SCHEMA_INVALID", index=index))
            proposal = {
                "key": f"quarantined-{index}",
                "title": "结构异常的主题",
                "thesis": "原始材料保留于冻结输出。",
                "industryClaims": [],
                "relatedIndustryIds": [],
                "sourceIds": [],
                "unknowns": [],
                "pendingTasks": [],
            }
            status, code = "QUARANTINED", "TOPIC_SCHEMA_INVALID"
        else:
            status, code = "OBSERVATION_ONLY", "OBSERVATION_ONLY"
        proof = [sources[ref] for ref in proposal["sourceIds"] if ref in sources]
        match = match_topic(proposal, sources=proof)
        claimed_industries = set(match["industryIds"])
        server_industries = direction_required.get(proposal["key"], set())
        if server_industries:
            match["industryIds"] = sorted(claimed_industries | server_industries)
            match["evidence"].append("SERVER_FUND_CATALOG")
            match["scopeVerified"] = (
                match["scopeVerified"] or set(match["industryIds"]) <= server_industries
            )
            if len(match["industryIds"]) > 1 and match["status"] not in {
                "NEW_TOPIC",
                "UNMATCHED",
            }:
                match["status"] = "COMPOSITE"
        topic_systemic = bool(
            _SYSTEMIC.search(proposal["title"] + " " + proposal["thesis"])
        )
        if topic_systemic:
            result["autoBlockers"].append(_block("NARRATIVE_RISK_REVIEW_REQUIRED"))
        industries = set(match["industryIds"])
        if counts[proposal["key"]] > 1:
            status, code = "QUARANTINED", "TOPIC_KEY_DUPLICATE"
        elif set(proposal["sourceIds"]) - sources.keys():
            status, code = "QUARANTINED", "SOURCE_REFERENCE_INVALID"
        elif status != "QUARANTINED":
            if proposal["key"] in formal_by_key:
                plans = formal_by_key[proposal["key"]].get("plans", [])
                required = {
                    industry
                    for plan in plans
                    for industry in plan_industry_ids(packet, plan)
                }
                if required and (
                    not required <= claimed_industries
                    or match["status"] in {"NEW_TOPIC", "UNMATCHED"}
                ):
                    status, code = "PENDING_REVIEW", "SOURCE_SCOPE_UNVERIFIED"
                    result["autoBlockers"].append(_block("NO_SAFE_REPAIR"))
                else:
                    status, code = (
                        ("FORMAL", "FORMAL_VALIDATED")
                        if plans
                        else ("OBSERVATION_ONLY", "OBSERVATION_ONLY")
                    )
            elif proposal["key"] in direction_states:
                status, code = "PENDING_REVIEW", "FORMAL_DIRECTION_PENDING"
            elif industries & set(manifest["deferredScopes"]):
                status, code = "PENDING_REVIEW", "AUDIT_BUDGET_DEFERRED"
            elif match["status"] in {"NEW_TOPIC", "UNMATCHED"}:
                proposed_ids = {
                    scope["industryId"] for scope in match.get("proposedScopes", [])
                }
                if proposed_ids and proposed_ids <= reviews and match["scopeVerified"]:
                    status, code = "OBSERVATION_ONLY", "OBSERVATION_ONLY"
                    match["evidence"].append("DYNAMIC_SCOPE_REVIEW_SUPPORTED")
                else:
                    status, code = "PENDING_REVIEW", "INDUSTRY_IDENTITY_PENDING"
            elif industries - reviews:
                status, code = "PENDING_REVIEW", "AUDIT_INCOMPLETE"
            elif not match["scopeVerified"]:
                status, code = "PENDING_REVIEW", "SOURCE_SCOPE_UNVERIFIED"
        dependency_risk = (
            proposal["key"] not in formal_by_key
            and not match["scopeVerified"]
            and _NEGATIVE.search(
                proposal["thesis"]
                + " "
                + " ".join(source.get("quote", "") for source in proof)
            )
            and (
                bool(
                    (set(match["industryIds"]) | set(match["relatedIndustryIds"]))
                    & retained_scopes
                )
                or bool(set(proposal["sourceIds"]) & retained_refs)
            )
        )
        if (
            topic_systemic or systemic_unresolved or dependency_risk
        ) and status != "QUARANTINED":
            status, code = "PENDING_REVIEW", "DEPENDENCY_REVIEW_REQUIRED"
            match["evidence"].append(
                "SCOPE_UNRESOLVED_RETAINED_DEPENDENCY"
                if dependency_risk
                else "SCOPE_UNRESOLVED_SYSTEMIC_SIGNAL"
            )
            result["autoBlockers"].append(_block("NARRATIVE_RISK_REVIEW_REQUIRED"))
        tasks = (
            [
                task
                for task in topic_tasks(match, manifest)
                if task["kind"] not in completed_kinds.get(task["industryId"], set())
            ]
            if status == "PENDING_REVIEW"
            else []
        )
        item = {
            "topicId": "topic:"
            + digest(
                [
                    proposal["key"],
                    match["industryIds"],
                    [scope["industryId"] for scope in match.get("proposedScopes", [])],
                    index if status == "QUARANTINED" else None,
                ]
            )[:32],
            "key": proposal["key"],
            "title": proposal["title"],
            "summary": proposal["thesis"],
            "status": status,
            "reasonCode": code,
            "reason": TOPIC_MESSAGES[code],
            "impact": "不可用于投资计划、买入提醒或解除既有风险。"
            if status != "FORMAL"
            else "正式计划仍受原有风险、价格与入场条件约束。",
            "measures": ["保留原始材料与来源引用", "未执行额外模型调用"]
            + (["相关计划未放行"] if status != "FORMAL" else ["完整严格复验"]),
            "pendingTasks": tasks,
            "sourceIds": list(proposal["sourceIds"]),
            "match": match,
            "investmentEligible": status == "FORMAL"
            and not result["hardBlockers"]
            and not result["autoBlockers"],
        }
        if len(json.dumps(item, ensure_ascii=False).encode()) > 16 * 1024:
            item.update(
                status="QUARANTINED",
                reasonCode="REGISTRY_OUTPUT_LIMIT",
                reason=TOPIC_MESSAGES["REGISTRY_OUTPUT_LIMIT"],
                pendingTasks=[],
                investmentEligible=False,
            )
        registry["items"].append(item)
    all_scopes = set(manifest["selectedScopes"]) | set(manifest["deferredScopes"])
    dynamic_search_terms = {}
    for item in registry["items"]:
        all_scopes.update(item["match"]["industryIds"])
        all_scopes.update(
            scope["industryId"] for scope in item["match"].get("proposedScopes", [])
        )
        dynamic_search_terms.update(
            {
                scope["industryId"]: scope["searchTerm"]
                for scope in item["match"].get("proposedScopes", [])
            }
        )
    from backend.macro.research_topics import make_task

    registry["auditTasks"] = [
        make_task(
            industry,
            kind,
            f"{dynamic_search_terms.get(industry, industry)} {terms}",
            manifest["window"],
            status="DEFERRED"
            if industry in manifest["deferredScopes"]
            else "PENDING_REVIEW",
            reason_code="AUDIT_BUDGET_DEFERRED"
            if industry in manifest["deferredScopes"]
            else "AUDIT_INCOMPLETE",
            search_term=dynamic_search_terms.get(industry),
        )
        for industry in sorted(all_scopes - reviews)
        for kind, terms in RISK_CHECKS.items()
        if kind not in completed_kinds.get(industry, set())
    ]
    task_by_id = {task["taskId"]: task for task in registry["auditTasks"]}
    for item in registry["items"]:
        for task in item["pendingTasks"]:
            if task.get("searchTerm") or task["taskId"] not in task_by_id:
                task_by_id[task["taskId"]] = deepcopy(task)
    registry["auditTasks"] = list(task_by_id.values())
    registry["coverage"] = {
        "totalCount": discovered_count,
        "storedCount": len(registry["items"]),
        "countsByStatus": {
            status: sum(item["status"] == status for item in registry["items"])
            for status in (
                "FORMAL",
                "OBSERVATION_ONLY",
                "PENDING_REVIEW",
                "QUARANTINED",
            )
        },
        "plannedScopeCount": len(manifest["selectedScopes"]),
        "reviewedScopeCount": len(set(manifest["selectedScopes"]) & reviews),
        "pendingScopeCount": len(all_scopes - reviews),
        "deferredScopeCount": len(manifest["deferredScopes"]),
        **{key: value for key, value in assurance.items() if key != "completedKinds"},
        "hasMore": bool(discovered_count and not registry["items"]),
        "additionalModelCalls": 0,
        **manifest["budget"],
    }
    if len(json.dumps(registry, ensure_ascii=False).encode()) > 1024 * 1024:
        registry["items"] = []
        registry["auditTasks"] = []
        registry["issues"].append(_issue("REGISTRY_OUTPUT_LIMIT"))
        registry["coverage"]["hasMore"] = True
        registry["coverage"]["storedCount"] = 0
    result["safetyBlocked"] = bool(result["hardBlockers"])
    result["autoPublishAllowed"] = (
        formal is not None and not result["hardBlockers"] and not result["autoBlockers"]
    )
    result["formalReport"] = formal if result["autoPublishAllowed"] else None
    result["strictFormalReport"] = formal
    result["strictPass"] = formal is not None
    result["historicalPublishAllowed"] = (
        formal is not None and not result["hardBlockers"]
    )
    registry["coverage"].update(
        strictRevalidated=formal is not None,
        strictDirectionCount=len(formal_by_key),
        autoPublishAllowed=result["autoPublishAllowed"],
        autoPublishBlockerCount=len(result["autoBlockers"]) + len(result["hardBlockers"]),
    )
    result["decision"] = "REPAIRED" if result["historicalPublishAllowed"] else "BLOCKED"
    if not result["autoPublishAllowed"]:
        for item in registry["items"]:
            item["investmentEligible"] = False
    result["repairSummary"] = {
        "version": "research-batch-v2",
        "originalDecision": original.decision,
        "strictRevalidated": formal is not None,
        "quarantinedCount": len(result["quarantined"]),
        "coverage": deepcopy(registry["coverage"]),
        "changed": changed,
        "repairRevision": registry["revision"],
        "buildCommit": registry["buildCommit"],
    }
    # Match the existing READY notice contract without leaking private annexes.
    from backend.macro.research_repair import REPAIR_MESSAGES

    result["repairSummary"]["quarantined"] = [
        {key: row[key] for key in ("itemType", "itemIndex", "businessKey", "ruleId")}
        | {"reason": REPAIR_MESSAGES[row["ruleId"]]}
        for row in result["quarantined"]
    ]
    return result
