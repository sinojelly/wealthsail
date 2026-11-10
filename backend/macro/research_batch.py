"""Research delivery keeps all insights; audit completeness is separate metadata."""

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
    diagnose_report,
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
    direction_limit,
    freeze_discovery_packet,
    frozen_manifest,
    match_topic,
    is_targeted_research,
    topic_match_version,
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
    raw, packet, checks, *, research_id, validated_at, rule_version=None,
    _strict_validator=None,
):
    """Keep the complete report; audit coverage is descriptive metadata only."""
    if _strict_validator is not None:
        return diagnose_research_batch(raw, packet, checks, research_id=research_id,
            validated_at=validated_at, rule_version=rule_version,
            _strict_validator=_strict_validator)
    stamp = _now(validated_at)
    revision = validator_revision()
    if rule_version is not None and rule_version != revision:
        raise ValueError("unsupported research rule version")
    outcome = validate_report_outcome(raw, packet, checks, research_id=research_id, completed_at=stamp)
    formal = outcome.formal_payload
    registry = {
        "version": REGISTRY_VERSION, "researchId": str(research_id),
        "validatedAt": stamp.isoformat(), "revision": revision,
        "buildCommit": build_revision(), "items": [], "coverage": {},
        "auditTasks": [], "issues": [],
    }
    # Keep the original planned search work visible for diagnostics. A missing
    # receipt changes its audit status, never the report's publication status.
    manifest = None
    try:
        manifest = frozen_manifest(packet)
        if manifest:
            registry["auditTasks"] = deepcopy(manifest["tasks"])
    except (ValueError, TypeError, KeyError):
        pass
    checked_source_ids = {row["id"] for row in (formal or {}).get("sources", []) if row.get("verification") == "SOURCE_CHECKED"}
    discovered = (formal or {}).get("topicCatalog", [])
    discovered_keys = {row["key"] for row in discovered}
    checked_topics = sum(bool(set(row.get("sourceIds", [])) & checked_source_ids) for row in discovered)
    completed_scopes = {row["industry"] for row in (formal or {}).get("industryCycleReviews", []) if row.get("auditStatus") == "AUDITED"}
    for task in registry["auditTasks"]:
        if task["industryId"].startswith("TOPIC_SCOPE:"):
            task.update(status="PLANNED_DEFERRED", reasonCode="INDUSTRY_IDENTITY_PENDING")
        elif task["industryId"] not in completed_scopes:
            task.update(status="PENDING_REVIEW", reasonCode="AUDIT_INCOMPLETE")
    registry["auditTasks"] = [task for task in registry["auditTasks"] if task["industryId"] not in completed_scopes]
    if formal is not None:
        topics = list(formal.get("topicCatalog", []))
        known = {row["key"] for row in topics}
        topics += [
            {"key": row["key"], "title": row["title"], "thesis": row["thesis"],
             "industryClaims": row.get("reviewIndustryIds", []),
             "relatedIndustryIds": [], "sourceIds": row.get("sourceIds", []),
             "unknowns": row.get("watchFor", []), "pendingTasks": [],
             "auditStatus": row.get("auditStatus"), "plans": row.get("plans", [])}
            for row in formal.get("valueDirections", []) if row["key"] not in known
        ]
        for topic in topics:
            industries = topic.get("industryClaims", [])
            audited = topic.get("auditStatus") == "AUDITED"
            linked_direction = next((row for row in formal.get("valueDirections", []) if row["key"] == topic["key"]), {})
            eligible = bool(topic.get("plans") or linked_direction.get("plans")) and audited
            status = "FORMAL" if eligible else "OBSERVATION_ONLY"
            code = "FORMAL_VALIDATED" if eligible else "OBSERVATION_ONLY"
            match = {"status": "COMPOSITE" if len(industries) > 1 else "STANDARD" if industries else "UNMATCHED",
                "industryIds": industries, "relatedIndustryIds": topic.get("relatedIndustryIds", []),
                "evidence": ["GLOBAL_INDUSTRY_IDENTITY"], "registryVersion": "industry-registry-v1",
                "scopeVerified": audited, "unresolvedClaims": topic.get("originalIndustryIds", []) if topic.get("industryMappings") else [],
                "proposedScopes": []}
            registry["items"].append({
                "topicId": "topic:" + digest([topic["key"], industries])[:32],
                "key": topic["key"], "title": topic["title"], "summary": topic["thesis"],
                "unknowns": topic.get("unknowns", []), "followUp": topic.get("pendingTasks", []),
                "status": status, "reasonCode": code, "reason": TOPIC_MESSAGES[code],
                "impact": "基于已有资讯研判；关联基金参考价位由真实行情计算。",
                "measures": ["保留完整观点与基金参考价位", "未执行额外模型调用"],
                "pendingTasks": [], "sourceIds": topic.get("sourceIds", []),
                "match": match, "investmentEligible": eligible,
            })
    count = len(registry["items"])
    direction_count = len((formal or {}).get("valueDirections", []))
    registry["coverage"] = {
        "totalCount": count, "storedCount": count, "hasMore": False,
        "countsByStatus": {status: sum(row["status"] == status for row in registry["items"]) for status in ("FORMAL", "OBSERVATION_ONLY", "PENDING_REVIEW", "QUARANTINED")},
        "discoveredTopicCount": len(discovered), "strictDirectionCount": direction_count,
        "evidenceBackedTopicCount": checked_topics,
        "pendingVerificationTopicCount": len(discovered) - checked_topics,
        "evidenceBackedTopicRatioPercent": round(100 * checked_topics / len(discovered), 1) if discovered else None,
        "plannedScopeCount": len((manifest or {}).get("selectedScopes", [])),
        "reviewedScopeCount": len(completed_scopes),
        "pendingScopeCount": len(set((manifest or {}).get("selectedScopes", [])) - completed_scopes),
        "deferredScopeCount": len((manifest or {}).get("deferredScopes", [])),
        "observationScopeCount": len(({industry for row in registry["items"] if row["key"] in discovered_keys for industry in row["match"]["industryIds"]} | set((manifest or {}).get("deferredScopes", []))) - completed_scopes),
        "evaluableFundDirectionCount": sum(bool(row.get("plans")) for row in (formal or {}).get("valueDirections", [])),
        "qualifiedFormalDirectionCount": sum(bool(row.get("plans")) for row in (formal or {}).get("valueDirections", [])),
        "validEventCount": len((formal or {}).get("events", [])),
        "validUpcomingCount": len((formal or {}).get("upcoming", [])),
        "additionalModelCalls": 0, "autoPublishAllowed": formal is not None,
        "autoPublishBlockerCount": 0, "searchExecutionVerified": False,
        "researchDeliveryStatus": "VALIDATED_READ_ONLY" if formal else "UNAVAILABLE",
        "investmentQualificationStatus": "QUALIFIED_CANDIDATES" if any(row.get("plans") for row in (formal or {}).get("valueDirections", [])) else "NO_QUALIFIED_OPPORTUNITIES",
    }
    if manifest:
        from backend.macro.research_assurance import audit_assurance

        assurance = audit_assurance({row["industry"]: row for row in (formal or {}).get("industryCycleReviews", [])}, manifest, checks if isinstance(checks, dict) else {}, stamp, research_id=research_id)
        registry["coverage"].update({key: value for key, value in assurance.items() if key != "completedKinds"})
        registry["coverage"].update(manifest["budget"])
        registry["coverage"]["executionPendingDirectionCount"] = sum(row.get("auditStatus") == "UNAUDITED_SCOPE" for row in (formal or {}).get("valueDirections", []))
    warnings = (formal or {}).get("validationWarnings", [])
    summary = {"version": "research-sanitize-v1", "originalDecision": outcome.decision,
        "strictRevalidated": False, "quarantinedCount": 0,
        "coverage": deepcopy(registry["coverage"]), "changed": bool(warnings),
        "repairRevision": revision, "buildCommit": build_revision(),
        "quarantined": [], "validationWarnings": deepcopy(warnings)}
    return {"originalOutcome": outcome, "formalReport": formal, "registry": registry,
        "repairSummary": summary, "autoPublishAllowed": formal is not None,
        "historicalPublishAllowed": formal is not None, "strictPass": formal is not None,
        "candidateRaw": deepcopy(formal), "safetyBlocked": False,
        "hardBlockers": [], "autoBlockers": [], "quarantined": [],
        "decision": "REPAIRED" if formal else "BLOCKED",
        "retained": {"counts": {key: len((formal or {}).get(key, [])) for key in ("sources", "industryCycleReviews", "valueDirections", "events", "upcoming")}, "directionKeys": [row["key"] for row in (formal or {}).get("valueDirections", [])]}}


def diagnose_research_batch(
    raw,
    packet,
    checks,
    *,
    research_id,
    validated_at,
    rule_version=None,
    _strict_validator=None,
):
    _strict_validator = _strict_validator or diagnose_report
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
    malformed_plan_directions = set()
    try:
        parsed = _parse(raw)
        from backend.macro.value_directions import downgrade_invalid_plan_shapes

        parsed, malformed_plan_directions = downgrade_invalid_plan_shapes(parsed, packet)
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
        or len(directions) > direction_limit(packet)
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
    accepted_directions = {
        row["key"]: row for row in accepted.get("valueDirections", [])
    }
    from backend.macro.industry_cycle import RISK_CHECKS

    structured_reviews = {
        row["industry"]: row for row in accepted.get("industryCycleReviews", [])
    }
    from backend.macro.research_assurance import audit_assurance

    assurance = audit_assurance(
        structured_reviews, manifest, checks, stamp, research_id=research_id
    )
    completed_kinds = assurance["completedKinds"]
    if assurance["executionMatchStatus"] == "EXECUTION_UNMATCHED":
        registry["issues"].append(_issue("EXECUTION_UNMATCHED"))
    if assurance.get("executionReceiptTransportReason"):
        registry["issues"].append(_issue(assurance["executionReceiptTransportReason"]))
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
    retained, removed, changed = [], set(), bool(malformed_plan_directions)
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
        accepted_direction = accepted_directions.get(direction["key"], {})
        if (
            accepted_direction.get("qualificationStatus") == "OBSERVATION_ONLY"
            or direction["key"] in malformed_plan_directions
        ):
            # A strict observation keeps its original risks, sources and news
            # dependencies. Removing its plans is not removing the direction.
            direction.update(
                signal=accepted_direction.get("signal", direction["signal"]),
                reviewIndustryIds=accepted_direction.get("reviewIndustryIds", direction["reviewIndustryIds"]),
                plans=[],
                quantitativeFundCodes=[],
            )
            changed |= direction != raw_direction
            retained.append(direction)
        elif exact and required <= reviews:
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
                audit_completed_scopes=reviews,
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
    # A topic is a read-only identity, not a formal plan. Retain its legitimate
    # reference context during strict news validation, without copying a plan or
    # assigning industry audit rights. Invalid proposals stay in the registry.
    candidate["topicCatalog"] = []
    for proposal in raw_topics:
        try:
            topic = TopicProposal.model_validate(proposal).model_dump()
        except ValidationError:
            continue
        if all(ref in sources for ref in topic["sourceIds"]):
            candidate["topicCatalog"].append(topic)
    from backend.macro.research_event_links import possible_formal_dependencies

    all_formal_keys = [
        row["key"] for row in directions
        if isinstance(row, dict) and isinstance(row.get("key"), str)
    ]
    for component, kind in (("events", "EVENT"), ("upcoming", "UPCOMING")):
        kept = []
        for index, row in enumerate(parsed.get(component, [])):
            if not isinstance(row, dict):
                result["hardBlockers"].append(_block("UNSAFE_INPUT"))
                continue
            refs = possible_formal_dependencies(row, all_formal_keys) & removed
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
                candidate, packet, checks, completed_at=stamp, research_id=research_id
            )
        except ResearchValidationError:
            result["hardBlockers"].append(_block("STRICT_REVALIDATION_FAILED"))
        else:
            for direction in formal.get("valueDirections", []):
                previous = accepted_directions.get(direction["key"], {})
                if (
                    previous.get("qualificationStatus") == "OBSERVATION_ONLY"
                    or direction["key"] in malformed_plan_directions
                ):
                    direction.update(
                        qualificationStatus="OBSERVATION_ONLY",
                        investmentEligible=False,
                        formalEligibilityReasons=sorted(
                            {
                                *direction.get("formalEligibilityReasons", []),
                                *previous.get("formalEligibilityReasons", []),
                                *(["FUND_PLAN_INVALID"] if direction["key"] in malformed_plan_directions else []),
                            }
                        ),
                    )
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
        match = match_topic(proposal, sources=proof, version=topic_match_version(packet))
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
            "unknowns": list(proposal["unknowns"]),
            "followUp": list(proposal["pendingTasks"]),
            "insightCategory": "MULTI_SCOPE" if len(match["industryIds"]) > 1 else "INDUSTRY" if match["industryIds"] else "DYNAMIC",
            "evidenceCoverageScore": round(
                100 * sum(source.get("verification") == "SOURCE_CHECKED" for source in proof)
                / len(proposal["sourceIds"])
            ) if proposal["sourceIds"] else 0,
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

    audit_scopes = set(manifest["selectedScopes"]) if is_targeted_research(packet) else all_scopes
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
        for industry in sorted(audit_scopes - reviews)
        for kind, terms in RISK_CHECKS.items()
        if kind not in completed_kinds.get(industry, set())
    ]
    if is_targeted_research(packet):
        registry["auditTasks"] = [
            {**deepcopy(task),
             "status": "PLANNED_DEFERRED" if task["industryId"].startswith("TOPIC_SCOPE:") else "PENDING_REVIEW",
             "reasonCode": "INDUSTRY_IDENTITY_PENDING" if task["industryId"].startswith("TOPIC_SCOPE:") else "AUDIT_INCOMPLETE"}
            for task in manifest["tasks"]
            if task["kind"] not in completed_kinds.get(task["industryId"], set())
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
        "pendingScopeCount": len(audit_scopes - reviews),
        **({"observationScopeCount": len(all_scopes - audit_scopes)} if is_targeted_research(packet) else {}),
        "deferredScopeCount": len(manifest["deferredScopes"]),
        **{key: value for key, value in assurance.items() if key != "completedKinds"},
        "hasMore": bool(discovered_count and not registry["items"]),
        "additionalModelCalls": 0,
        **manifest["budget"],
    }
    # This is an execution-gap count, not an attribution of unmatched queries.
    registry["coverage"]["executionPendingDirectionCount"] = sum(
        any(
            check.get("status") == "NOT_FOUND"
            and check["kind"] not in completed_kinds.get(industry, set())
            for industry in (
                direction_required.get(row["key"], set())
                | {item for item in (
                    row.get("reviewIndustryIds") if isinstance(row.get("reviewIndustryIds"), list) else []
                ) if isinstance(item, str)}
            )
            for check in structured_reviews.get(industry, {}).get("riskChecks", [])
        )
        for row in directions
        if isinstance(row, dict) and isinstance(row.get("key"), str)
    )
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
    # These describe different stages of coverage. A checked quote can support
    # observation without proving an economic inference, industry scope or trade.
    reasons = Counter()
    if not directions:
        reasons["NO_FORMAL_PROPOSAL"] = 1
    for row in directions:
        if not isinstance(row, dict):
            reasons["FORMAL_VALIDATION_FAILED"] += 1
        elif not row.get("plans"):
            reasons["FUND_PLAN_MISSING"] += 1
        elif row.get("key") not in formal_by_key:
            reasons["FORMAL_VALIDATION_FAILED"] += 1
    for direction in formal_by_key.values():
        for reason in direction.get("formalEligibilityReasons", []):
            if reason == "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED":
                reasons["INDUSTRY_AUDIT_PENDING"] += 1
            elif reason != "OBSERVATION_ONLY":
                reasons["FORMAL_VALIDATION_FAILED"] += 1
    for item in registry["items"]:
        reason = {
            "SOURCE_SCOPE_UNVERIFIED": "SOURCE_EVIDENCE_PENDING",
            "SOURCE_REFERENCE_INVALID": "SOURCE_EVIDENCE_PENDING",
            "INDUSTRY_IDENTITY_PENDING": "INDUSTRY_IDENTITY_PENDING",
            "AUDIT_INCOMPLETE": "INDUSTRY_AUDIT_PENDING",
            "AUDIT_BUDGET_DEFERRED": "INDUSTRY_AUDIT_PENDING",
        }.get(item["reasonCode"])
        if reason:
            reasons[reason] += 1
    if result["hardBlockers"] or result["autoBlockers"]:
        reasons["PUBLICATION_SAFETY_BLOCKED"] = len(result["hardBlockers"]) + len(result["autoBlockers"])
    safe_news = formal or {"events": accepted.get("events", []), "upcoming": accepted.get("upcoming", [])}
    registered_discovery_keys = {row["key"] for row in candidate["topicCatalog"]}
    registry["coverage"].update(
        discoveredTopicCount=len(raw_topics),
        evidenceBackedTopicCount=sum(
            item["status"] != "QUARANTINED"
            and item["key"] in registered_discovery_keys
            and any(sources.get(ref, {}).get("verification") == "SOURCE_CHECKED" for ref in item["sourceIds"])
            for item in registry["items"]
        ),
        evaluableFundDirectionCount=sum(
            isinstance(row, dict) and bool(row.get("plans"))
            and isinstance(row["plans"], list)
            and all(isinstance(plan, dict) and plan_industry_ids(packet, plan) for plan in row["plans"])
            for row in directions
        ),
        qualifiedFormalDirectionCount=sum(
            bool(row.get("plans")) and result["autoPublishAllowed"]
            for row in formal_by_key.values()
        ),
        formalEligibilityReasons=dict(reasons),
        validEventCount=len(safe_news.get("events", [])),
        validUpcomingCount=len(safe_news.get("upcoming", [])),
        unresolvedReferenceCount=sum(
            len(row.get("referenceResolution", {}).get("unresolved", []))
            for component in ("events", "upcoming")
            for row in safe_news.get(component, [])
        ),
    )
    discovered = registry["coverage"]["discoveredTopicCount"]
    checked = registry["coverage"]["evidenceBackedTopicCount"]
    registry["coverage"].update(
        pendingVerificationTopicCount=max(0, discovered - checked),
        evidenceBackedTopicRatioPercent=round(100 * checked / discovered, 1) if discovered else None,
    )
    # Read-only delivery is not the READY result or permission for an operation.
    # A failed fund can coexist with independently checked economic material.
    checked_source_ids = {
        source["id"] for source in accepted.get("sources", [])
        if source.get("verification") == "SOURCE_CHECKED"
    }
    checked_news = any(
        set(row.get("sourceIds", [])) & checked_source_ids
        for component in ("events", "upcoming") for row in safe_news.get(component, [])
    )
    observation_available = bool(
        safe_news.get("events") or safe_news.get("upcoming")
        or any(item["status"] != "QUARANTINED" for item in registry["items"])
    )
    news_errors = any(issue.item_type in {"SOURCE", "EVENT", "UPCOMING"} for issue in original.issues)
    unsafe = any(block["code"] == "UNSAFE_INPUT" for block in result["hardBlockers"])
    registry["coverage"]["researchDeliveryStatus"] = (
        "VALIDATED_READ_ONLY" if not unsafe and not news_errors and (
            checked_news or registry["coverage"]["evidenceBackedTopicCount"] > 0
        ) else "OBSERVATION_READ_ONLY" if observation_available and not unsafe else "UNAVAILABLE"
    )
    registry["coverage"]["investmentQualificationStatus"] = (
        "QUALIFIED_CANDIDATES" if registry["coverage"]["qualifiedFormalDirectionCount"]
        else "BLOCKED" if any(isinstance(row, dict) and row.get("plans") for row in directions)
        else "NO_QUALIFIED_OPPORTUNITIES"
    )
    result["retained"] = {
        "counts": {
            **{component: len((formal or {}).get(component, [])) for component in (
                "sources", "industryCycleReviews", "valueDirections", "events", "upcoming",
            )},
            "fundPlans": sum(len(row.get("plans", [])) for row in formal_by_key.values()),
        },
        "directionKeys": sorted(formal_by_key),
    }
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
    if topic_match_version(packet) == "topic-identity-v2":
        # Resolve only the validated source alias map, after raw-reference risk
        # and coverage checks. Frozen v1 registry identities stay unchanged.
        for item in registry["items"]:
            item["sourceIds"] = list(dict.fromkeys(
                aliases.get(ref, ref) for ref in item["sourceIds"]
            ))
    return result
