"""One bounded, evidence-preserving quarantine followed by the public validator.

This is deterministic repair of a failed proposal, never another model turn.
The original acceptance receipt and all sources/reviews remain immutable.
"""

from copy import deepcopy
import hashlib
import json
import re

from pydantic import ValidationError

from backend.macro.daily_research import (
    ResearchValidationError,
    _Report,
    _now,
    _previous_industry_states,
    _safe_narrative,
    _url,
    validate_report,
)
from backend.macro.industry_contract import plan_industry_ids
from backend.macro.industry_cycle import industry_risk_policy
from backend.macro.research_acceptance import (
    _parse,
    build_revision,
    rejected_outcome,
    validate_report_outcome,
    validator_revision,
)
from backend.macro.value_directions import _macro_codes

REPAIR_VERSION = "research-repair-v1"
MAX_REPAIR_BYTES = 4 * 1024 * 1024
REPAIR_MESSAGES = {
    "WATCH_REVIEW_UNLINKED": "无计划观察方向缺少有效行业审核关联，已整体隔离。",
    "DEPENDENT_EVENT_QUARANTINED": "事件明确关联已隔离方向，已连同证据整体隔离。",
    "DEPENDENT_UPCOMING_QUARANTINED": "日历项明确关联已隔离方向，已连同证据整体隔离。",
    "HEADER_PROVENANCE_MISSING": "总纲缺少方向依赖记录，禁止自动发布。",
    "HEADER_DEPENDENCY_QUARANTINED": "总纲涉及已隔离方向，需人工复核原文。",
    "HEADER_REFERENCE_UNKNOWN": "总纲引用未知方向，不能可靠修复。",
    "DEPENDENT_IMPORTANCE_UNPROVEN": "关联事实的重要性未证明为低，禁止自动发布。",
    "DEPENDENT_NEGATIVE_UNPROVEN": "关联事实的非负面性质未获明确证明，禁止自动发布。",
    "HIGH_IMPORTANCE_DEPENDENCY": "高重要性关联事实已完整保留于必读附件，禁止自动发布。",
    "NARRATIVE_RISK_REVIEW_REQUIRED": "风险叙述已完整保留于必读附件，禁止自动发布，历史只读审阅须人工确认。",
    "NEGATIVE_RISK_QUARANTINE": "负面事实或行业风险不得通过修复删除。",
    "HISTORICAL_RISK_QUARANTINE": "关联方向仍有历史风险观察，不得隔离绕过。",
    "PLAN_REVIEW_REQUIRED": "有计划的方向必须保留并完成精确行业审核。",
    "QUARANTINE_BUDGET_EXCEEDED": "待隔离方向超过单次修复预算。",
    "NO_SAFE_REPAIR": "未找到符合固定边界的安全修复。",
    "UNSAFE_INPUT": "原始结构或来源核验材料不完整，禁止修复发布。",
    "STRICT_REVALIDATION_FAILED": "修复候选未通过完整严格复验。",
    "RETAINED_PROTECTION_CHANGED": "保留计划或风险保护发生减轻，禁止发布。",
    "REPAIR_OUTPUT_LIMIT": "修复材料超过存储预算，禁止发布。",
}


def repair_summary(result):
    """Closed, small audit metadata; private candidate/prose never enters INITIAL."""
    summary = {
        name: result.get(name)
        for name in (
            "decision",
            "strictPass",
            "autoPublishAllowed",
            "historicalPublishAllowed",
            "safetyBlocked",
            "repairVersion",
            "revision",
            "buildCommit",
        )
    }
    summary["quarantinedCount"] = len(result.get("quarantined", []))
    summary.update(
        version=result.get("repairVersion"),
        originalDecision=(result.get("originalOutcome") or {}).get("decision"),
        strictRevalidated=result.get("strictPass") is True,
        repairRevision=result.get("revision"),
        counts=deepcopy((result.get("retained") or {}).get("counts") or {}),
    )
    summary["quarantined"] = [
        {
            "itemType": row["itemType"],
            "itemIndex": row["itemIndex"],
            "ruleId": row["ruleId"],
            "businessKey": row["businessKey"],
            "reason": REPAIR_MESSAGES[row["ruleId"]],
        }
        for row in result.get("quarantined", [])
    ]
    for name in ("autoBlockers", "hardBlockers"):
        summary[name] = [
            {"code": row["code"], "reason": REPAIR_MESSAGES[row["code"]]}
            for row in result.get(name, [])
        ]
    summary["excerpted"] = True
    return summary


# Narrative matches veto automatic publication, never establish an industry fact.
_NEGATIVE = re.compile(
    r"融资.{0,12}(?:降温|下降|萎缩|断代|中断)|欠薪|裁员|倒闭|破产|砍单|"
    r"产能过剩|价格战|量产.{0,8}(?:延期|推迟)|商业化.{0,8}(?:延期|推迟)|"
    r"系统性风险|金融危机|违约|亏损|去泡沫|financing.{0,20}(?:cool|declin)|"
    r"layoff|bankrupt|order cuts?|overcapacity|price war|production delay",
    re.IGNORECASE,
)
_BENIGN = re.compile(
    r"公开资料(?:已)?发布|例行(?:资料|数据|报告)发布|公开(?:数据|报告)已发布|"
    r"例行会议(?:已召开|已举行)|routine (?:publication|meeting)",
    re.IGNORECASE,
)


def _clearly_nonnegative(row):
    # Unknown extra clauses are not made benign by one matching fragment.
    if any(row.get(name) for name in ("scenarios", "unknowns", "channels", "watchFor")):
        return False
    for name in ("title", "summary"):
        text = row.get(name)
        if text is None:
            continue
        clauses = [
            part.strip() for part in re.split(r"[。！？!?;；\n]+", text) if part.strip()
        ]
        if not clauses or any(not _BENIGN.fullmatch(part) for part in clauses):
            return False
    return True


def _block(code, *, path="$", item_type="REPORT", item_index=None):
    return {
        "code": code,
        "path": path,
        "itemType": item_type,
        "itemIndex": item_index,
        "reason": REPAIR_MESSAGES[code],
    }


def _text(row):
    return " ".join(
        str(row.get(name) or "")
        for name in ("title", "summary", "thesis", "watchFor", "risks", "unknowns")
    )


def _has_cycle_risk(row):
    cycle = row.get("industryCycle") or {}
    return bool(
        industry_risk_policy(cycle)["leftDiscountScale"]
        or any(check.get("status") == "FOUND" for check in cycle.get("riskChecks", []))
    )


def _annex(row, index, component, code, sources, *, dependencies=()):
    item_type = {
        "valueDirections": "VALUE_DIRECTION",
        "events": "EVENT",
        "upcoming": "UPCOMING",
    }[component]
    source_ids = list(row.get("sourceIds") or [])
    fields = ("id", "title", "url", "quote", "publishedDate", "verification")
    cycle = deepcopy(row.get("industryCycle"))
    if isinstance(cycle, dict):
        # Describe structural gaps only, never adjudicate new industry facts.
        # The exact original cycle stays in originalItem and candidate evidence.
        gaps = list(cycle.get("evidenceGaps") or [])
        if code == "WATCH_REVIEW_UNLINKED":
            gaps.append("缺少有效的同行业审核关联，不能作为正式价值方向。")
        unverified = sum(
            check.get("status") == "UNVERIFIED" for check in cycle.get("riskChecks", [])
        )
        if unverified:
            gaps.append(
                f"五类反方核查中{unverified}项为UNVERIFIED，不能据此认定无行业风险。"
            )
        cycle["evidenceGaps"] = gaps
    return {
        "itemType": item_type,
        "itemIndex": index,
        "path": f"{component}[{index}]",
        "businessKey": row.get("key")
        or row.get("id")
        or hashlib.sha256(
            json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:24],
        "ruleId": code,
        "reason": REPAIR_MESSAGES[code],
        "sourceIds": source_ids,
        "dependencies": deepcopy(list(dependencies)),
        "title": row.get("title", ""),
        "summary": row.get("summary", row.get("thesis", "")),
        "importance": row.get("importance"),
        "mandatory": True,
        **{
            name: deepcopy(row.get(name) or [])
            for name in (
                "risks",
                "watchFor",
                "scenarios",
                "unknowns",
                "channels",
                "catalysts",
            )
        },
        **({"industryCycle": cycle} if isinstance(cycle, dict) else {}),
        "sourceEvidence": [
            {
                name: deepcopy(sources[source_id][name])
                for name in fields
                if name in sources[source_id]
            }
            for source_id in source_ids
            if source_id in sources
        ],
        "originalItem": deepcopy(row),
    }


def _protection_unchanged(accepted, formal, candidate):
    old = {row["key"]: row for row in accepted.get("valueDirections", [])}
    new = {row["key"]: row for row in formal.get("valueDirections", [])}
    expected = {row["key"]: row for row in candidate["valueDirections"]}
    if set(new) != set(expected):
        return False
    for key, proposal in expected.items():
        if len(new[key].get("plans", [])) != len(proposal.get("plans", [])):
            return False
        if key not in old:
            continue
        before, after = (
            old[key].get("industryCycle") or {},
            new[key].get("industryCycle") or {},
        )
        for field in (
            "riskFlags",
            "confirmedRiskFlags",
            "suspectedRiskFlags",
            "persistedRiskFlags",
        ):
            if not set(before.get(field) or []).issubset(set(after.get(field) or [])):
                return False
        if (
            industry_risk_policy(after)["leftDiscountScale"]
            < industry_risk_policy(before)["leftDiscountScale"]
        ):
            return False
    return True


def repair_report(raw, packet, source_checks, *, research_id, validated_at):
    """Return a private bounded receipt; callers publish only the explicit gate."""
    if (packet.get("researchDiscovery") or {}).get(
        "version"
    ) == "research-discovery-v2":
        # New and offline historical research use exactly the same deterministic
        # core. A V1 frozen packet never receives V2 coverage semantics.
        from backend.macro.research_batch import evaluate_research_batch

        batch = evaluate_research_batch(
            raw,
            packet,
            source_checks,
            research_id=research_id,
            validated_at=validated_at,
        )
        formal = batch.get("strictFormalReport")
        original = batch["originalOutcome"].to_dict()
        hard = batch.get("hardBlockers", [])
        strict = batch.get("strictPass") is True and formal is not None
        return {
            "decision": "REPAIRED" if strict else "BLOCKED",
            "strictPass": strict,
            "autoPublishAllowed": strict and batch["autoPublishAllowed"],
            "historicalPublishAllowed": strict
            and batch.get("historicalPublishAllowed", False)
            and not hard,
            "safetyBlocked": batch.get("safetyBlocked", bool(hard)),
            "formalReport": formal,
            "candidateRaw": batch.get("candidateRaw"),
            "originalOutcome": original,
            "topicRegistry": batch["registry"],
            "quarantined": batch.get("quarantined", []),
            "retained": batch.get("retained", {}),
            "hardBlockers": hard,
            "autoBlockers": batch.get("autoBlockers", []),
            "blockers": hard + batch.get("autoBlockers", []),
            "repairVersion": "research-batch-v2",
            "revision": batch["registry"]["revision"],
            "buildCommit": batch["registry"].get("buildCommit"),
            "validatedAt": _now(validated_at).isoformat(),
        }
    stamp = _now(validated_at)
    result = {
        "decision": "BLOCKED",
        "strictPass": False,
        "autoPublishAllowed": False,
        "historicalPublishAllowed": False,
        "safetyBlocked": True,
        "formalReport": None,
        "candidateRaw": None,
        "quarantined": [],
        "retained": {},
        "autoBlockers": [],
        "hardBlockers": [],
        "blockers": [],
        "repairVersion": REPAIR_VERSION,
        "revision": validator_revision(),
        "buildCommit": build_revision(),
        "validatedAt": stamp.isoformat(),
    }

    def finish():
        result["blockers"] = result["hardBlockers"] + result["autoBlockers"]
        result["safetyBlocked"] = bool(result["hardBlockers"])
        result["historicalPublishAllowed"] = (
            result["strictPass"] and not result["hardBlockers"]
        )
        result["autoPublishAllowed"] = (
            result["historicalPublishAllowed"] and not result["autoBlockers"]
        )
        if (
            len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode())
            > MAX_REPAIR_BYTES
        ):
            result.update(
                decision="BLOCKED",
                strictPass=False,
                autoPublishAllowed=False,
                historicalPublishAllowed=False,
                safetyBlocked=True,
                formalReport=None,
                candidateRaw=None,
                quarantined=[],
                retained={},
            )
            result["hardBlockers"] = [_block("REPAIR_OUTPUT_LIMIT")]
            result["blockers"] = result["hardBlockers"] + result["autoBlockers"]
        return result

    try:
        parsed = _parse(raw)
        schema_input = parsed
        if "themeActionableZones" not in packet:
            schema_input = {
                "macroCycle": "宏观周期证据待核查。",
                "actionablePlans": [],
                **schema_input,
            }
        if "discoveryUniverse" not in packet:
            schema_input = {"valueDirections": [], **schema_input}
        _Report.model_validate(schema_input)
        _safe_narrative(schema_input)
        if not isinstance(source_checks, dict):
            raise ValueError("source verification missing")
    except (ValueError, TypeError, RecursionError, ValidationError):
        result["originalOutcome"] = rejected_outcome(
            research_id, stamp, rule_id="ACCEPTANCE_ENVELOPE_INVALID"
        ).to_dict()
        result["hardBlockers"].append(_block("UNSAFE_INPUT"))
        return finish()

    original = validate_report_outcome(
        parsed, packet, source_checks, research_id=research_id, completed_at=stamp
    )
    result["originalOutcome"] = original.to_dict()
    if original.formal_payload is not None:
        result.update(
            decision="NO_CHANGE", strictPass=True, formalReport=original.formal_payload
        )
        return finish()
    if original.decision != "PARTIAL":
        # Unsafe envelopes/sources and reports without independently accepted
        # observations are never candidates for a direction-only correction.
        result["hardBlockers"].append(_block("UNSAFE_INPUT"))
        return finish()
    accepted = original.accepted
    reviews = {row["industry"] for row in accepted.get("industryCycleReviews", [])}
    directions = parsed.get("valueDirections", [])
    keys = [row["key"] for row in directions]
    if len(keys) != len(set(keys)):
        result["hardBlockers"].append(_block("UNSAFE_INPUT"))
        return finish()
    candidates = []
    for index, row in enumerate(directions):
        if row.get("plans"):
            required = {
                industry
                for plan in row["plans"]
                for industry in plan_industry_ids(packet, plan)
            }
            if (
                required - reviews
                or not set(row.get("reviewIndustryIds") or []) & reviews
            ):
                result["hardBlockers"].append(
                    _block(
                        "PLAN_REVIEW_REQUIRED",
                        path=f"valueDirections[{index}]",
                        item_type="VALUE_DIRECTION",
                        item_index=index,
                    )
                )
        elif (
            row["signal"] == "WATCH"
            and not row.get("quantitativeFundCodes")
            and not (set(row.get("reviewIndustryIds") or []) & reviews)
        ):
            candidates.append(index)
    if len(candidates) > 2:
        result["hardBlockers"].append(_block("QUARANTINE_BUDGET_EXCEEDED"))
    if not candidates:
        result["hardBlockers"].append(_block("NO_SAFE_REPAIR"))
    history = _previous_industry_states(packet, stamp)
    source_ids = {row["id"] for row in parsed["sources"]}
    macros = set(_macro_codes(packet, stamp))
    for index in candidates:
        row = directions[index]
        if (
            set(row.get("sourceIds") or []) - source_ids
            or set(row.get("macroCodes") or []) - macros
        ):
            result["hardBlockers"].append(
                _block(
                    "NO_SAFE_REPAIR",
                    path=f"valueDirections[{index}]",
                    item_type="VALUE_DIRECTION",
                    item_index=index,
                )
            )
        if _has_cycle_risk(row):
            result["hardBlockers"].append(
                _block(
                    "NEGATIVE_RISK_QUARANTINE",
                    path=f"valueDirections[{index}]",
                    item_type="VALUE_DIRECTION",
                    item_index=index,
                )
            )
        if row.get("risks") or _NEGATIVE.search(_text(row)):
            result["autoBlockers"].append(
                _block(
                    "NARRATIVE_RISK_REVIEW_REQUIRED",
                    path=f"valueDirections[{index}]",
                    item_type="VALUE_DIRECTION",
                    item_index=index,
                )
            )
        if ("key", row["key"]) in history:
            result["hardBlockers"].append(
                _block(
                    "HISTORICAL_RISK_QUARANTINE",
                    path=f"valueDirections[{index}]",
                    item_type="VALUE_DIRECTION",
                    item_index=index,
                )
            )
    if (
        not candidates
        or len(candidates) > 2
        or any(row["code"] == "NO_SAFE_REPAIR" for row in result["hardBlockers"])
    ):
        return finish()

    removed = {directions[index]["key"] for index in candidates}
    all_keys = set(keys) | {row.get("id") for row in packet.get("directions", [])}
    narrative = parsed.get("narrativeDirectionIds")
    if narrative is None:
        result["autoBlockers"].append(_block("HEADER_PROVENANCE_MISSING"))
    else:
        for name, refs in narrative.items():
            if set(refs) - all_keys:
                result["hardBlockers"].append(
                    _block(
                        "HEADER_REFERENCE_UNKNOWN", path=f"narrativeDirectionIds.{name}"
                    )
                )
            if removed & set(refs):
                result["autoBlockers"].append(
                    _block("HEADER_DEPENDENCY_QUARANTINED", path=name)
                )
    headers = json.dumps(
        {name: parsed.get(name) for name in ("summary", "macroCycle", "coverage")},
        ensure_ascii=False,
    )
    if any(
        directions[index][name] in headers
        for index in candidates
        for name in ("key", "title")
    ):
        result["autoBlockers"].append(_block("HEADER_DEPENDENCY_QUARANTINED"))
    verified_urls = {row["url"]: row for row in accepted.get("sources", [])}
    sources = {}
    for row in parsed["sources"]:
        try:
            uri = _url(row["url"])
        except ValueError:
            continue  # The unchanged invalid source still fails full strict validation.
        if uri in verified_urls:
            sources[row["id"]] = {**verified_urls[uri], "id": row["id"]}
    candidate = deepcopy(parsed)
    candidate["valueDirections"] = [
        row for index, row in enumerate(directions) if index not in candidates
    ]
    result["quarantined"] = [
        _annex(
            directions[index],
            index,
            "valueDirections",
            "WATCH_REVIEW_UNLINKED",
            sources,
        )
        for index in candidates
    ]
    for component, item_type in (("events", "EVENT"), ("upcoming", "UPCOMING")):
        retained = []
        for index, row in enumerate(parsed[component]):
            refs = set(row.get("affectedDirectionIds") or []) & removed
            if not refs:
                retained.append(deepcopy(row))
                continue
            path = f"{component}[{index}]"
            importance = row.get("importance")
            if importance == "HIGH":
                result["autoBlockers"].append(
                    _block(
                        "HIGH_IMPORTANCE_DEPENDENCY",
                        path=path,
                        item_type=item_type,
                        item_index=index,
                    )
                )
            evidence_text = " ".join(
                sources[source_id].get("quote", "")
                for source_id in row.get("sourceIds", [])
                if source_id in sources
            )
            if _NEGATIVE.search(_text(row) + " " + evidence_text):
                result["autoBlockers"].append(
                    _block(
                        "NARRATIVE_RISK_REVIEW_REQUIRED",
                        path=path,
                        item_type=item_type,
                        item_index=index,
                    )
                )
            if importance != "LOW":
                result["autoBlockers"].append(
                    _block(
                        "DEPENDENT_IMPORTANCE_UNPROVEN",
                        path=path,
                        item_type=item_type,
                        item_index=index,
                    )
                )
            if not _clearly_nonnegative(row):
                result["autoBlockers"].append(
                    _block(
                        "DEPENDENT_NEGATIVE_UNPROVEN",
                        path=path,
                        item_type=item_type,
                        item_index=index,
                    )
                )
            result["quarantined"].append(
                _annex(
                    row,
                    index,
                    component,
                    f"DEPENDENT_{item_type}_QUARANTINED",
                    sources,
                    dependencies=[
                        {"itemType": "VALUE_DIRECTION", "businessKey": key}
                        for key in sorted(refs)
                    ],
                )
            )
        candidate[component] = retained
    result["candidateRaw"] = candidate
    try:
        formal = validate_report(candidate, packet, source_checks, completed_at=stamp)
    except ResearchValidationError:
        result["hardBlockers"].append(_block("STRICT_REVALIDATION_FAILED"))
        return finish()
    if not _protection_unchanged(accepted, formal, candidate):
        result["hardBlockers"].append(_block("RETAINED_PROTECTION_CHANGED"))
        return finish()
    result.update(
        decision="BLOCKED" if result["hardBlockers"] else "REPAIRED",
        strictPass=True,
        candidateRaw=candidate,
        formalReport=formal,
    )
    result["retained"] = {
        "counts": {
            "fundPlans": sum(
                len(row.get("plans", [])) for row in candidate["valueDirections"]
            )
            + len(candidate.get("actionablePlans", [])),
            **{
                name: len(candidate.get(name, []))
                for name in (
                    "sources",
                    "industryCycleReviews",
                    "valueDirections",
                    "events",
                    "upcoming",
                )
            },
        },
        "directionKeys": [row["key"] for row in candidate["valueDirections"]],
        "planIdentities": [
            {
                name: plan.get(name)
                for name in ("subjectKind", "subjectId", "fundCode", "track")
            }
            for row in candidate["valueDirections"]
            for plan in row.get("plans", [])
        ],
    }
    return finish()
