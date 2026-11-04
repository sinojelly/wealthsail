"""Bounded, collectable acceptance; retained observations never become a report.

Only the global candidate-completeness gate is separated from component checks.
All evidence, fund identity, history, price and entry guards use the strict core.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re

from pydantic import ValidationError

from backend.macro import daily_research as strict
from backend.macro.alpha_research import _program_plan
from backend.macro.industry_contract import plan_industry_ids
from backend.macro.industry_cycle import IndustryCycleReview, RISK_CHECKS
from backend.macro.research_diagnostics import RULE_IDS, sanitize_missing_industry_codes
from backend.macro.value_directions import ValueDirectionProposal, _macro_codes

VALIDATOR_VERSION = "research-acceptance-v1"
_ROOT = Path(__file__).resolve().parents[2]
# Hash the deployed backend tree, including lazy dependencies such as candidate
# generation and diagnostics, so a newly imported rule cannot silently escape.
_REVISION_FILES = tuple(sorted((_ROOT / "backend").rglob("*.py"))) + (
    _ROOT / "requirements.txt",
)


@lru_cache(maxsize=1)
def validator_revision():
    """Fingerprint the shipped rule sources without requiring a Git checkout."""
    digest = hashlib.sha256()
    try:
        for path in _REVISION_FILES:
            source = path.read_bytes().replace(b"\r\n", b"\n")
            try:
                identity = path.relative_to(_ROOT).as_posix()
            except ValueError:
                identity = path.name
            digest.update(identity.encode("utf-8") + b"\0")
            digest.update(len(source).to_bytes(8, "big") + source)
    except OSError:
        # Missing build metadata must never veto a strictly valid report.
        return None
    return digest.hexdigest()


def build_revision():
    """Optional provenance supplied by the image build, never run Git at runtime."""
    value = os.environ.get("WEALTHSAIL_BUILD_COMMIT", "")
    return (
        value.lower()
        if re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", value)
        else None
    )


MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_ISSUES = 1000
MAX_RECEIPT_BYTES = 768 * 1024
COMPONENTS = {
    "sources": ("SOURCE", strict._Source, 24),
    "industryCycleReviews": ("INDUSTRY_REVIEW", IndustryCycleReview, 64),
    "valueDirections": ("VALUE_DIRECTION", ValueDirectionProposal, 8),
    "events": ("EVENT", strict._Event, 12),
    "upcoming": ("UPCOMING", strict._Upcoming, 12),
}
ISSUE_MESSAGES = {
    **{rule: "该项未通过业务校验，已隔离。" for rule in RULE_IDS},
    "ACCEPTANCE_PARSE_FAILED": "报告无法可靠解析，禁止发布。",
    "ACCEPTANCE_ENVELOPE_INVALID": "报告结构或大小不符合验收边界。",
    "ACCEPTANCE_SCHEMA_INVALID": "该项字段不符合数据结构要求。",
    "ACCEPTANCE_UNSAFE_SOURCE": "来源地址不符合安全边界，禁止发布。",
    "ACCEPTANCE_UNSAFE_CONTENT": "报告包含不受支持的内容，禁止发布。",
    "ACCEPTANCE_AMBIGUOUS_IDENTITY": "业务标识存在冲突，不能选择性采信。",
    "ACCEPTANCE_SOURCE_DEPENDENCY_INVALID": "引用的来源不存在或未通过验收。",
    "ACCEPTANCE_DIRECTION_DEPENDENCY_INVALID": "引用的方向不存在或已被隔离。",
    "ACCEPTANCE_SOURCE_DATE_INVALID": "来源发布日期无效或晚于验证时刻。",
    "ACCEPTANCE_CALENDAR_DATE_INVALID": "日历日期无效、冲突或已过期。",
    "ACCEPTANCE_FUND_PLAN_INVALID": "基金计划不符合服务器身份或价格保护要求。",
    "ACCEPTANCE_ITEM_INVALID": "该项未通过独立验收，已隔离。",
    "ACCEPTANCE_DEPENDENCY_BLOCKED": "所依赖的条目未通过验收，已阻止使用。",
    "ACCEPTANCE_SECOND_PASS_FAILED": "引用与风险保护复核未通过，禁止发布。",
    "ACCEPTANCE_SYSTEM_FAILURE": "验收未正常完成，禁止发布。",
    "ACCEPTANCE_SOURCE_VERIFICATION_FAILED": "来源核验未完成，不能使用空结果替代。",
    "ACCEPTANCE_ISSUE_LIMIT": "问题数量超过验收预算，不能声称清单完整。",
    "ACCEPTANCE_RECEIPT_LIMIT": "问题清单超过存储预算，需查看冻结材料，不能声称清单完整。",
    "ACCEPTANCE_OVERVIEW_SOURCE_UNRESOLVED": "宏观总纲缺少逐句引用，相关全局来源未通过验收，已隔离。",
}
ISSUE_MESSAGES.update(
    {
        "DIRECTION_PLAN_SECTOR_UNREVIEWED": "基金所属行业未完成核查，相关计划已隔离。",
        "DIRECTION_DECLARED_INDUSTRY_UNKNOWN": "方向声明的行业没有有效审核记录。",
        "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED": "行业核查缺少执行凭证或有效原文支持，相关计划待审核。",
        "DIRECTION_MACRO_REFERENCE_UNKNOWN": "方向引用的宏观事实无效。",
        "INDUSTRY_REVIEW_COVERAGE_MISSING": "本轮必查行业的核查覆盖不完整。",
    }
)


@dataclass(frozen=True)
class ValidationIssue:
    research_id: str
    item_type: str
    item_index: int | None
    path: str
    business_key: str
    rule_id: str
    reason_code: str = "DIRECTION_VALIDATION_FAILED"
    missing_industry_codes: tuple = ()
    disposition: str = "QUARANTINED"
    dependencies: tuple = ()

    def to_dict(self):
        identity = json.dumps(
            [
                self.research_id,
                self.item_type,
                self.item_index,
                self.path,
                self.business_key,
                self.rule_id,
                self.missing_industry_codes,
            ],
            ensure_ascii=False,
        )
        return {
            "issueId": hashlib.sha256(identity.encode()).hexdigest()[:32],
            "researchId": self.research_id,
            "itemType": self.item_type,
            "itemIndex": self.item_index,
            "path": self.path,
            "businessKey": self.business_key,
            "ruleId": self.rule_id,
            "reasonCode": self.reason_code,
            "missingIndustryCodes": list(self.missing_industry_codes),
            "disposition": self.disposition,
            "dependencies": deepcopy(list(self.dependencies)),
            "message": ISSUE_MESSAGES.get(
                self.rule_id, ISSUE_MESSAGES["ACCEPTANCE_ITEM_INVALID"]
            ),
        }


@dataclass(frozen=True)
class ValidationOutcome:
    research_id: str
    validated_at: datetime
    decision: str
    issues: tuple
    accepted: dict
    counts: dict
    formal_payload: dict | None = field(default=None, repr=False)
    first_error: strict.ResearchValidationError | None = field(default=None, repr=False)
    issue_list_complete: bool = True
    accepted_content_complete: bool = True
    revision: str | None = field(default_factory=validator_revision)
    build_commit: str | None = field(default_factory=build_revision)

    def to_dict(self):
        return {
            "researchId": self.research_id,
            "validatorVersion": VALIDATOR_VERSION,
            "validatorRevision": self.revision,
            "buildCommit": self.build_commit,
            "revisionStatus": "AVAILABLE" if self.revision else "UNAVAILABLE",
            "validatedAt": self.validated_at.isoformat(),
            "decision": self.decision,
            "formalPublishable": self.formal_payload is not None,
            "actionable": False,
            "issues": [row.to_dict() for row in self.issues],
            "issueCount": len(self.issues),
            "issueListComplete": self.issue_list_complete,
            "acceptedContentComplete": self.accepted_content_complete,
            "accepted": deepcopy(self.accepted),
            "counts": deepcopy(self.counts),
        }


def _empty():
    return {key: [] for key in COMPONENTS}


def _bounded_outcome(outcome):
    """Bound a receipt without vetoing the original formal publication.

    Original text stays in its artifact. Large prose lists may be excerpted
    explicitly; no risk check or risk conclusion is changed to pass validation.
    """

    def size(value):
        return len(json.dumps(value.to_dict(), ensure_ascii=False).encode("utf-8"))

    if size(outcome) <= MAX_RECEIPT_BYTES:
        return outcome
    accepted = deepcopy(outcome.accepted)
    for component in ("events", "upcoming", "valueDirections"):
        for row in accepted.get(component, []):
            for name in ("watchFor", "unknowns", "channels"):
                values = row.get(name)
                if isinstance(values, list):
                    row[name] = [
                        value[:240] + "…（节选）"
                        if isinstance(value, str) and len(value) > 240
                        else value
                        for value in values[:16]
                    ]
    bounded = replace(outcome, accepted=accepted, accepted_content_complete=False)
    if size(bounded) <= MAX_RECEIPT_BYTES:
        return bounded
    bounded = replace(bounded, accepted=_empty())
    if size(bounded) <= MAX_RECEIPT_BYTES:
        return bounded
    issue = ValidationIssue(
        outcome.research_id, "REPORT", None, "$", "report", "ACCEPTANCE_RECEIPT_LIMIT"
    )
    return replace(
        bounded,
        decision="COMPLETE" if outcome.formal_payload is not None else "REJECTED",
        issues=(issue,),
        issue_list_complete=False,
    )


def rejected_outcome(
    research_id,
    completed_at,
    *,
    reason_code="UNEXPECTED_ERROR",
    rule_id="ACCEPTANCE_SYSTEM_FAILURE",
    item_type="REPORT",
):
    return ValidationOutcome(
        str(research_id),
        strict._now(completed_at),
        "REJECTED",
        (
            ValidationIssue(
                str(research_id), item_type, None, "$", "report", rule_id, reason_code
            ),
        ),
        _empty(),
        {},
        first_error=strict.ResearchValidationError(reason_code),
        issue_list_complete=False,
    )


def _parse(raw):
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
            raise ValueError("output budget")
        raw = json.loads(raw, object_pairs_hook=strict._unique_object)
    if not isinstance(raw, dict):
        raise ValueError("object required")
    if (
        len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode())
        > MAX_RAW_BYTES
    ):
        raise ValueError("output budget")

    def structure(value, depth=0):
        if depth > 32:
            raise ValueError("structure depth budget")
        if isinstance(value, dict):
            if any(not isinstance(key, str) or len(key) > 100 for key in value):
                raise ValueError("field name budget")
            for nested in value.values():
                structure(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value:
                structure(nested, depth + 1)

    structure(raw)
    return deepcopy(raw)


def sources_for_verification(raw, *, now):
    """Never send malformed/untrusted source addresses to a network verifier."""
    try:
        parsed = _parse(raw)
    except (ValueError, TypeError, RecursionError):
        return []
    rows = parsed.get("sources", [])
    if not isinstance(rows, list) or len(rows) > 24:
        return []
    result = []
    for row in rows:
        try:
            value = strict._Source.model_validate(row).model_dump()
            strict._url(value["url"])
            strict._validate_publication(value, now)
        except (ValidationError, ValueError):
            continue
        result.append(value)
    return result


def _projection(report):
    """A closed observation DTO, deliberately excluding plans and trade origins."""
    output = _empty()
    fields = {
        "sources": ("id", "title", "url", "quote", "publishedDate", "publishedAt", "retrievedAt", "verification"),
        "industryCycleReviews": (
            "industry",
            "stage",
            "label",
            "riskLevel",
            "riskFlags",
            "confirmedRiskFlags",
            "suspectedRiskFlags",
            "persistedRiskFlags",
            "evidenceGaps",
            "sourceIds",
            "evidenceSummary",
            "riskChecks",
            "searchExecutionVerified",
        ),
        "valueDirections": (
            "key",
            "title",
            "thesis",
            "sourceIds",
            "reviewIndustryIds",
            "watchFor",
            "catalysts",
            "risks",
            "industryCycle",
        ),
        "events": (
            "id",
            "title",
            "summary",
            "phase",
            "importance",
            "sourceIds",
            "affectedDirectionIds",
            "relatedTopicKeys",
            "relatedFormalDirectionKeys",
            "relatedMacroConcepts",
            "candidateConceptLabels",
            "referenceResolution",
            "channels",
            "scenarios",
            "watchFor",
            "unknowns",
            "factVerification",
        ),
        "upcoming": (
            "id",
            "title",
            "phase",
            "importance",
            "scheduledDate",
            "scheduledAt",
            "sourceIds",
            "affectedDirectionIds",
            "relatedTopicKeys",
            "relatedFormalDirectionKeys",
            "relatedMacroConcepts",
            "candidateConceptLabels",
            "referenceResolution",
            "watchFor",
        ),
    }
    for key, names in fields.items():
        output[key] = [
            {name: deepcopy(row[name]) for name in names if name in row}
            for row in report.get(key, [])
        ]
    # Frozen input already retains historical source bodies. The observation
    # receipt keeps their risk conclusions and clocks without copying those
    # potentially large bodies into every direction again.
    cycle_fields = (
        "stage",
        "label",
        "riskLevel",
        "riskFlags",
        "confirmedRiskFlags",
        "suspectedRiskFlags",
        "persistedRiskFlags",
        "confirmedRiskAt",
        "leftDiscountScale",
        "opportunityPenalty",
        "industryRiskWarning",
        "evidenceSummary",
        "sourceIds",
        "riskChecks",
        "evidence",
        "evidenceGaps",
        "researchStatus",
        "auditStructureStatus",
        "searchExecutionVerified",
        "earningsVerified",
        "earningsIndustryScopeVerified",
        "earningsEntity",
        "earningsReportPeriod",
        "earningsEvidenceScope",
        "forwardPlansOnly",
    )
    for row in output["valueDirections"]:
        cycle = row.get("industryCycle")
        if isinstance(cycle, dict):
            row["industryCycle"] = {
                key: cycle[key] for key in cycle_fields if key in cycle
            }
    output["macroOverview"] = _overview(
        report, [row["id"] for row in output["sources"]]
    )
    return output


def _overview(raw, source_ids):
    """Retain schema-valid model prose as inference, never verified facts.

    The legacy header has no per-claim references. Its global source dependency
    must therefore be intact before a partial report can retain this prose.
    """
    if not source_ids:
        return {}
    output = {"verification": "MODEL_UNVERIFIED", "sourceIds": list(source_ids)}
    for name in ("summary", "macroCycle"):
        value = raw.get(name)
        if isinstance(value, str) and 1 <= len(value) <= 1000:
            output[name] = value
    values = raw.get("coverage")
    if isinstance(values, list) and len(values) <= 6:
        output["coverage"] = []
        for row in values:
            try:
                output["coverage"].append(
                    strict._Coverage.model_validate(row).model_dump()
                )
            except ValidationError:
                continue
    return (
        output
        if any(output.get(name) for name in ("summary", "macroCycle", "coverage"))
        else {}
    )


def _overview_counts(raw, overview):
    def count(value):
        coverage = value.get("coverage")
        return sum(name in value for name in ("summary", "macroCycle")) + (
            len(coverage) if isinstance(coverage, list) else int("coverage" in value)
        )

    total, accepted = count(raw), count(overview)
    return {
        "inputCount": total,
        "acceptedCount": accepted,
        "quarantinedCount": total - accepted,
    }


def validate_report_outcome(
    raw, packet, source_checks, *, research_id, completed_at, _strict_validator=None
):
    """Collect expected data failures; unexpected internal exceptions propagate.

    Formal publication always requires the original, unpruned strict report.
    Component probes only separate candidate coverage from individual validity.
    """
    now = strict._now(completed_at)
    try:
        parsed = _parse(raw)
    except (ValueError, TypeError, RecursionError):
        return rejected_outcome(
            research_id,
            now,
            reason_code="SCHEMA_VALIDATION_FAILED",
            rule_id="ACCEPTANCE_PARSE_FAILED",
        )
    try:
        strict._safe_narrative(parsed)
    except strict.ResearchValidationError:
        return rejected_outcome(
            research_id,
            now,
            rule_id="ACCEPTANCE_UNSAFE_CONTENT",
            reason_code="DIRECTION_VALIDATION_FAILED",
        )
    raw_sources = parsed.get("sources")
    for row in raw_sources if isinstance(raw_sources, list) else []:
        if isinstance(row, dict) and isinstance(row.get("url"), str):
            try:
                strict._url(row["url"])
            except ValueError:
                return rejected_outcome(
                    research_id,
                    now,
                    reason_code="SOURCE_REFERENCE_INVALID",
                    rule_id="ACCEPTANCE_UNSAFE_SOURCE",
                )
    validator = _strict_validator or strict.validate_report
    # Legacy injected validators predate the identity keyword. V2 must always
    # receive caller context; its expected run never comes from the receipt.
    identity_context = (
        {"research_id": research_id}
        if _strict_validator is None or strict.is_v2(packet)
        else {}
    )
    try:
        formal = validator(
            raw, packet, source_checks, completed_at=now, **identity_context
        )
    except strict.ResearchValidationError as error:
        first_error = error
    else:
        counts = {
            key: {
                "inputCount": len(formal.get(key, [])),
                "acceptedCount": len(formal.get(key, [])),
                "quarantinedCount": 0,
            }
            for key in COMPONENTS
        }
        plans = sum(
            len(row.get("plans", [])) for row in formal.get("valueDirections", [])
        )
        counts["fundPlans"] = {
            "inputCount": plans,
            "acceptedCount": plans,
            "quarantinedCount": 0,
        }
        counts["macroOverview"] = _overview_counts(
            formal, _overview(formal, [row["id"] for row in formal["sources"]])
        )
        return _bounded_outcome(
            ValidationOutcome(
                str(research_id),
                now,
                "COMPLETE",
                (),
                _projection(formal),
                counts,
                formal,
            )
        )

    if set(parsed) - set(strict._Report.model_fields) or any(
        not isinstance(parsed.get(key, []), list) or len(parsed.get(key, [])) > limit
        for key, (_, _, limit) in COMPONENTS.items()
    ):
        return rejected_outcome(
            research_id,
            now,
            reason_code="SCHEMA_VALIDATION_FAILED",
            rule_id="ACCEPTANCE_ENVELOPE_INVALID",
        )
    if (packet.get("industryCycleResearch") or {}).get("required") and len(
        parsed.get("industryCycleReviews", [])
    ) > 10:
        return rejected_outcome(
            research_id, now, rule_id="INDUSTRY_REVIEW_BUDGET_EXCEEDED"
        )

    issues, seen = [], set()

    def add(
        kind,
        index,
        path,
        row,
        rule,
        *,
        error=None,
        missing=None,
        deps=(),
        disposition="QUARANTINED",
    ):
        business = (
            str(row.get("key") or row.get("industry") or row.get("id") or "")[:100]
            if isinstance(row, dict)
            else ""
        )
        if kind == "FUND_PLAN" and isinstance(row, dict) and row.get("subjectKind"):
            business = f"{row['subjectKind']}:{row.get('subjectId', '')}:{row.get('track', '')}"[
                :100
            ]
        business = (
            business
            or hashlib.sha256(
                json.dumps(row, ensure_ascii=False, default=str).encode()
            ).hexdigest()[:24]
        )
        codes = tuple(sanitize_missing_industry_codes(missing) or ())
        key = kind, index, path, rule, codes
        if key not in seen:
            seen.add(key)
            issues.append(
                ValidationIssue(
                    str(research_id),
                    kind,
                    index,
                    path,
                    business,
                    rule,
                    error.reason_code if error else "DIRECTION_VALIDATION_FAILED",
                    codes,
                    disposition,
                    tuple(deps),
                )
            )

    def expected(kind, index, path, row, error):
        rule = error.rule_id or {
            "SOURCE_DATE_INVALID": "ACCEPTANCE_SOURCE_DATE_INVALID",
            "SOURCE_REFERENCE_INVALID": "ACCEPTANCE_SOURCE_DEPENDENCY_INVALID",
            "SCHEDULE_DATE_INVALID": "ACCEPTANCE_CALENDAR_DATE_INVALID",
            "FUND_PLAN_INVALID": "ACCEPTANCE_FUND_PLAN_INVALID",
            "SCHEMA_VALIDATION_FAILED": "ACCEPTANCE_SCHEMA_INVALID",
        }.get(error.reason_code, "ACCEPTANCE_ITEM_INVALID")
        add(
            kind,
            index,
            path,
            row,
            rule,
            error=error,
            missing=error.missing_industry_codes,
        )

    base = {
        "summary": "独立条目验收，非正式报告。",
        "macroCycle": "仅用于内部验收。",
        "coverage": [],
        "energy": [],
        "actionablePlans": [],
        "topicCatalog": [row for row in parsed.get("topicCatalog", []) if isinstance(row, dict)]
        if isinstance(parsed.get("topicCatalog", []), list) else [],
        **_empty(),
    }
    # Header failures remain explicit; neutral probe metadata cannot publish.
    header = {
        **base,
        **{key: value for key, value in parsed.items() if key not in COMPONENTS},
    }
    try:
        strict._Report.model_validate(header)
    except ValidationError as error:
        for detail in error.errors():
            add(
                "REPORT",
                None,
                ".".join(map(str, detail["loc"])),
                {},
                "ACCEPTANCE_SCHEMA_INVALID",
            )
    if parsed.get("actionablePlans"):
        add("REPORT", None, "actionablePlans", {}, "ACCEPTANCE_FUND_PLAN_INVALID")

    def probe(**parts):
        return strict._validate_report_core(
            {**base, **parts},
            packet,
            source_checks,
            completed_at=now,
            research_id=research_id,
            _require_candidate_coverage=False,
        )

    # This verifies the frozen server inputs separately from model data.
    probe()
    accepted_raw = _empty()
    source_ids, review_ids, valid_keys = set(), set(), set()
    shaped = {}
    for component, (kind, model, _) in COMPONENTS.items():
        values = []
        for index, row in enumerate(parsed.get(component, [])):
            try:
                valid = model.model_validate(row).model_dump()
            except ValidationError as error:
                for detail in error.errors():
                    suffix = ".".join(map(str, detail["loc"]))
                    add(
                        kind,
                        index,
                        f"{component}[{index}].{suffix}",
                        row,
                        "ACCEPTANCE_SCHEMA_INVALID",
                    )
                continue
            values.append((index, valid))
        shaped[component] = values

    source_names = Counter(row["id"] for _, row in shaped["sources"])
    if any(count > 1 for count in source_names.values()):
        return rejected_outcome(
            research_id, now, rule_id="ACCEPTANCE_AMBIGUOUS_IDENTITY"
        )
    for index, row in shaped["sources"]:
        try:
            strict._url(row["url"])
        except ValueError:
            return rejected_outcome(
                research_id,
                now,
                reason_code="SOURCE_REFERENCE_INVALID",
                rule_id="ACCEPTANCE_UNSAFE_SOURCE",
            )
        try:
            probe(sources=[row])
        except strict.ResearchValidationError as error:
            expected("SOURCE", index, f"sources[{index}]", row, error)
        else:
            accepted_raw["sources"].append(row)
            source_ids.add(row["id"])

    def source_dependencies(row):
        refs = set()

        def walk(value):
            if isinstance(value, dict):
                refs.update(value.get("sourceIds", []))
                for nested in value.values():
                    walk(nested)
            elif isinstance(value, list):
                for nested in value:
                    walk(nested)

        walk(row)
        return tuple(
            {"itemType": "SOURCE", "businessKey": ref}
            for ref in sorted(refs - source_ids)
        )

    def cycle_problems(kind, index, path, row):
        cycle = row if kind == "INDUSTRY_REVIEW" else row.get("industryCycle")
        if not isinstance(cycle, dict):
            return
        checks = cycle["riskChecks"]
        kinds = Counter(check["kind"] for check in checks)
        for risk_kind in sorted(set(RISK_CHECKS) - set(kinds)):
            add(
                kind,
                index,
                path + ".riskChecks." + risk_kind,
                row,
                "INDUSTRY_RISK_CHECK_MISSING",
            )
        for risk_kind, count in kinds.items():
            if count > 1:
                add(
                    kind,
                    index,
                    path + ".riskChecks." + risk_kind,
                    row,
                    "INDUSTRY_RISK_CHECK_DUPLICATE",
                )
        for check_index, check in enumerate(checks):
            if check["status"] == "FOUND" and not check["sourceIds"]:
                add(
                    kind,
                    index,
                    f"{path}.riskChecks[{check_index}]",
                    row,
                    "INDUSTRY_RISK_SOURCE_MISSING",
                )
        own_refs = set(cycle["sourceIds"])
        for evidence_field in ("riskChecks", "evidence"):
            for position, evidence in enumerate(cycle.get(evidence_field, [])):
                if set(evidence["sourceIds"]) - own_refs:
                    add(
                        kind,
                        index,
                        f"{path}.{evidence_field}[{position}].sourceIds",
                        row,
                        "INDUSTRY_CYCLE_SOURCE_INVALID",
                    )
        if kind == "VALUE_DIRECTION" and own_refs - set(row["sourceIds"]):
            add(
                kind,
                index,
                path + ".industryCycle.sourceIds",
                row,
                "INDUSTRY_CYCLE_SOURCE_INVALID",
            )

    for component in ("industryCycleReviews", "valueDirections"):
        kind = COMPONENTS[component][0]
        identity = "industry" if component == "industryCycleReviews" else "key"
        duplicates = Counter(row[identity] for _, row in shaped[component])
        for index, row in shaped[component]:
            path = f"{component}[{index}]"
            before = len(issues)
            if duplicates[row[identity]] > 1:
                add(kind, index, path, row, "ACCEPTANCE_AMBIGUOUS_IDENTITY")
            cycle_problems(kind, index, path, row)
            deps = source_dependencies(row)
            if deps:
                add(
                    kind,
                    index,
                    path + ".sourceIds",
                    row,
                    "ACCEPTANCE_SOURCE_DEPENDENCY_INVALID",
                    deps=deps,
                )
            if component == "valueDirections":
                missing = set(row["reviewIndustryIds"]) - review_ids
                if missing:
                    add(
                        kind,
                        index,
                        path + ".reviewIndustryIds",
                        row,
                        "DIRECTION_DECLARED_INDUSTRY_UNKNOWN",
                        deps=tuple(
                            {"itemType": "INDUSTRY_REVIEW", "businessKey": ref}
                            for ref in sorted(missing)
                        ),
                    )
                for code in sorted(
                    set(row["macroCodes"]) - set(_macro_codes(packet, now))
                ):
                    add(
                        kind,
                        index,
                        path + ".macroCodes",
                        row,
                        "DIRECTION_MACRO_REFERENCE_UNKNOWN",
                        deps=({"itemType": "MACRO", "businessKey": code},),
                    )
                for plan_index, plan in enumerate(row["plans"]):
                    plan_path = f"{path}.plans[{plan_index}]"
                    anchor = next(
                        (
                            item
                            for item in packet.get("discoveryActionableZones", [])
                            if (item.get("subjectKind"), item.get("subjectId"))
                            == (plan["subjectKind"], plan["subjectId"])
                        ),
                        None,
                    )
                    invalid_plan = anchor is None
                    if anchor is not None:
                        try:
                            day, _, _ = _program_plan(
                                anchor, plan["track"], now, require_theme=False
                            )
                        except ValueError:
                            invalid_plan = True
                        else:
                            invalid_plan = (
                                now.astimezone(strict._TZ).date() - day
                            ).days > 10 or anchor.get("historyQuality") in {
                                "STALE",
                                "CONFLICT",
                                "UNVERIFIED",
                                "UNAVAILABLE",
                                "INSUFFICIENT",
                            }
                    required_sectors = plan_industry_ids(packet, plan)
                    if (
                        plan["subjectKind"] in {"fund", "nav"}
                        and (packet.get("industryCycleResearch") or {}).get("required")
                        and not required_sectors
                    ):
                        invalid_plan = True
                    if invalid_plan:
                        add(
                            "FUND_PLAN",
                            index,
                            plan_path,
                            plan,
                            "ACCEPTANCE_FUND_PLAN_INVALID",
                            error=strict.ResearchValidationError("FUND_PLAN_INVALID"),
                        )
                    rules = {
                        item.get("key"): item
                        for item in packet.get("strategyMemoryRules", [])
                        if isinstance(item, dict)
                    }
                    allowed_rules = {
                        f"DISCOVERY:{row['key']}",
                        (anchor or {}).get("themeCode"),
                    }
                    if any(
                        key not in rules
                        or rules[key].get("themeCode") not in allowed_rules
                        for key in plan["calibrationRuleKeys"]
                    ):
                        add(
                            "FUND_PLAN",
                            index,
                            plan_path + ".calibrationRuleKeys",
                            plan,
                            "DIRECTION_CALIBRATION_RULE_UNKNOWN",
                        )
                    missing_sectors = required_sectors - review_ids
                    if missing_sectors:
                        links = tuple(
                            {"itemType": "INDUSTRY_REVIEW", "businessKey": sector}
                            for sector in sorted(missing_sectors)
                        )
                        add(
                            "FUND_PLAN",
                            index,
                            plan_path,
                            plan,
                            "DIRECTION_PLAN_SECTOR_UNREVIEWED",
                            missing=missing_sectors,
                            deps=links,
                        )
                        add(
                            kind,
                            index,
                            path,
                            row,
                            "DIRECTION_PLAN_SECTOR_UNREVIEWED",
                            missing=missing_sectors,
                            deps=links,
                            disposition="BLOCKED",
                        )
                plan_errors = [
                    issue for issue in issues[before:] if issue.item_type == "FUND_PLAN"
                ]
                if plan_errors and not any(
                    issue.item_type == kind for issue in issues[before:]
                ):
                    add(
                        kind,
                        index,
                        path,
                        row,
                        "ACCEPTANCE_DEPENDENCY_BLOCKED",
                        deps=tuple(
                            {"itemType": "FUND_PLAN", "businessKey": issue.business_key}
                            for issue in plan_errors
                        ),
                        disposition="BLOCKED",
                    )
            if len(issues) > before:
                continue
            try:
                probe(
                    **{**accepted_raw, component: [row], "events": [], "upcoming": []}
                )
            except strict.ResearchValidationError as error:
                expected(kind, index, path, row, error)
            else:
                accepted_raw[component].append(row)
                (review_ids if component == "industryCycleReviews" else valid_keys).add(
                    row[identity]
                )

    known_directions = {row["id"] for row in packet.get("directions", [])} | valid_keys
    for component in ("events", "upcoming"):
        kind = COMPONENTS[component][0]
        for index, row in shaped[component]:
            path = f"{component}[{index}]"
            before = len(issues)
            deps = source_dependencies(row)
            if deps:
                add(
                    kind,
                    index,
                    path + ".sourceIds",
                    row,
                    "ACCEPTANCE_SOURCE_DEPENDENCY_INVALID",
                    deps=deps,
                )
            if strict.is_v2(packet):
                from backend.macro.research_event_links import resolved_formal_dependencies

                missing = resolved_formal_dependencies(
                    row, [direction["key"] for _, direction in shaped["valueDirections"]]
                ) - valid_keys
            else:
                missing = set(row["affectedDirectionIds"]) - known_directions
            if missing:
                add(
                    kind,
                    index,
                    path + ".affectedDirectionIds",
                    row,
                    "ACCEPTANCE_DIRECTION_DEPENDENCY_INVALID",
                    deps=tuple(
                        {"itemType": "VALUE_DIRECTION", "businessKey": ref}
                        for ref in sorted(missing)
                    ),
                    disposition="BLOCKED",
                )
            # Independently collect calendar errors even with a broken reference.
            if component == "upcoming":
                stamp = (
                    strict._stamp(row["scheduledAt"]) if row["scheduledAt"] else None
                )
                day = (
                    strict._day(row["scheduledDate"]) if row["scheduledDate"] else None
                )
                if (
                    row["scheduledAt"]
                    and (stamp is None or stamp <= now)
                    or row["scheduledDate"]
                    and (
                        day is None
                        or str(day) != row["scheduledDate"]
                        or day < now.date()
                    )
                    or stamp
                    and day
                    and stamp.astimezone(strict._TZ).date() != day
                ):
                    add(kind, index, path, row, "ACCEPTANCE_CALENDAR_DATE_INVALID")
            if len(issues) > before:
                continue
            try:
                probe(**{**accepted_raw, component: [row]})
            except strict.ResearchValidationError as error:
                expected(kind, index, path, row, error)
            else:
                accepted_raw[component].append(row)

    from backend.integrations.daily_sources import industry_cycle_research_tasks

    if (packet.get("industryCycleResearch") or {}).get("required"):
        input_at = strict._now(packet["asOf"])
        tasks = industry_cycle_research_tasks(
            strict.apply_entry_guards(packet, packet, input_at), input_at
        )
        for sector in sorted(
            {row["industry"] for row in tasks["candidates"]} - review_ids
        ):
            add(
                "REPORT",
                None,
                "industryCycleReviews",
                {"industry": sector},
                "INDUSTRY_REVIEW_COVERAGE_MISSING",
                missing=[sector],
                deps=({"itemType": "INDUSTRY_REVIEW", "businessKey": sector},),
                disposition="BLOCKED",
            )
    if len(issues) > MAX_ISSUES:
        rejected = rejected_outcome(research_id, now, rule_id="ACCEPTANCE_ISSUE_LIMIT")
        return ValidationOutcome(
            rejected.research_id,
            now,
            "REJECTED",
            rejected.issues,
            _empty(),
            {},
            first_error=first_error,
            issue_list_complete=False,
        )
    try:
        joint = probe(**accepted_raw)
    except strict.ResearchValidationError as error:
        expected("REPORT", None, "$", {}, error)
        add(
            "REPORT",
            None,
            "$",
            {},
            "ACCEPTANCE_SECOND_PASS_FAILED",
            disposition="BLOCKED",
        )
        return ValidationOutcome(
            str(research_id),
            now,
            "REJECTED",
            tuple(issues),
            _empty(),
            {},
            first_error=first_error,
        )
    counts = {
        key: {
            "inputCount": len(parsed.get(key, [])),
            "acceptedCount": len(accepted_raw[key]),
            "quarantinedCount": len(parsed.get(key, [])) - len(accepted_raw[key]),
        }
        for key in COMPONENTS
    }
    input_plans = sum(
        len(row.get("plans", []))
        for row in parsed.get("valueDirections", [])
        if isinstance(row, dict) and isinstance(row.get("plans"), list)
    )
    accepted_plans = sum(len(row["plans"]) for row in accepted_raw["valueDirections"])
    counts["fundPlans"] = {
        "inputCount": input_plans,
        "acceptedCount": accepted_plans,
        "quarantinedCount": input_plans - accepted_plans,
    }
    observations = _projection(joint)
    # Never expose neutral probe placeholders as the model's macro research.
    intact = len(source_ids) == len(parsed.get("sources", [])) and bool(source_ids)
    observations["macroOverview"] = (
        _overview(parsed, [row["id"] for row in joint["sources"]]) if intact else {}
    )
    macro_fields = sum(name in parsed for name in ("summary", "macroCycle", "coverage"))
    if not intact and macro_fields:
        add(
            "REPORT",
            None,
            "macroOverview",
            {},
            "ACCEPTANCE_OVERVIEW_SOURCE_UNRESOLVED",
            disposition="BLOCKED",
            deps=tuple(
                {"itemType": "SOURCE", "businessKey": row.get("id", "unknown")}
                for row in parsed.get("sources", [])
                if isinstance(row, dict) and row.get("id") not in source_ids
            ),
        )
    counts["macroOverview"] = _overview_counts(parsed, observations["macroOverview"])
    decision = "PARTIAL" if any(observations.values()) else "EMPTY"
    return _bounded_outcome(
        ValidationOutcome(
            str(research_id),
            now,
            decision,
            tuple(issues),
            observations,
            counts,
            first_error=first_error,
        )
    )
