"""Publish parsed research, repairing local defects without deleting its insights.

Model prose is retained as model reasoning. Source verification and server NAV
coordinates keep their own truth status; neither is invented to make a report
pass. A malformed item can never reject its healthy siblings.
"""

from copy import deepcopy
import hashlib
import json
import math
import re


def _rows(value):
    return (
        value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    )


def _text(value, default=""):
    return (value.strip() or default) if isinstance(value, str) else default


def _texts(value):
    if isinstance(value, str):
        value = [value]
    return list(
        dict.fromkeys(
            item.strip()
            for item in _rows(value)
            if isinstance(item, str) and item.strip()
        )
    )


def _warn(warnings, path, code, message):
    warnings.append({"path": path, "code": code, "message": message})


_QUESTION_TOPICS = tuple(map(re.compile, (
    r"航运|海运|船舶|商船|通航|航道|海峡|停航|运输|战争险",
    r"油气|原油|石油|油价|产油|减产|产量|能源供应",
    r"黄金|金价|贵金属",
    r"加息|降息|美联储|实际利率|政策利率",
    r"卫星|火箭|星链|(?i:starship)|航天",
)))
_QUESTION_NOISE = re.compile(
    r"报道|风险|尚未|待确认|仍需|有待|是否|影响|最新|目前|变化|调查|历史|线索|研究|市场"
)
_QUESTION_SUBJECTS = re.compile(r"英国|俄罗斯|乌克兰|俄乌|对俄|法国|加沙|欧盟")


def _event_questions(title, proposals):
    """Keep short, event-specific clauses out of a digest's other stories."""
    anchors = set()
    for word in re.findall(r"[\u3400-\u9fff]{3,}|[a-z]{3,}\d*", _QUESTION_NOISE.sub(" ", title), re.I):
        anchors.update(
            [word.lower()] if word.isascii() else
            [word[index:index + 3] for index in range(len(word) - 2)]
        )
    shared_topics = [topic for topic in _QUESTION_TOPICS if topic.search(title)]
    result = []
    for proposal in proposals:
        for clause in re.split(r"[；;。\n]+", proposal):
            clause = clause.strip()
            if not clause or clause in result or any(
                subject not in title for subject in _QUESTION_SUBJECTS.findall(clause)
            ):
                continue
            text = _QUESTION_NOISE.sub(" ", clause).lower()
            if any(topic.search(text) for topic in shared_topics) or any(
                anchor in text for anchor in anchors
            ):
                result.append(clause)
                if len(result) == 2:
                    return result
    return result


def _json_safe(value, warnings, path="$"):
    """Repair JSON text/number encodings before publication or JSONB storage."""
    if isinstance(value, str):
        fixed = re.sub(r"[\ud800-\udfff]", "\ufffd", value.replace("\x00", ""))
        if fixed != value:
            _warn(
                warnings,
                path,
                "TEXT_ENCODING_REPAIRED",
                "文本中的无效字符已修正，完整原始输出仍保留于审计。",
            )
        return fixed
    if isinstance(value, float) and not math.isfinite(value):
        _warn(
            warnings,
            path,
            "NON_FINITE_NUMBER_CLEARED",
            "非有限数值已清空，不冒充真实观测。",
        )
        return None
    if isinstance(value, dict):
        return {
            (
                _json_safe(key, warnings, path) if isinstance(key, str) else str(key)
            ): _json_safe(
                child,
                warnings,
                path
                + "."
                + re.sub(r"[\ud800-\udfff]", "\ufffd", str(key).replace("\x00", "")),
            )
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _json_safe(child, warnings, f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    return value


def parse_report(raw):
    """Reject an absent/unparseable result, accepting every JSON body shape."""
    from backend.macro.daily_research import ResearchValidationError

    if raw is None:
        raise ResearchValidationError(
            "SCHEMA_VALIDATION_FAILED", "model returned no research body"
        )
    duplicates = []

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        parsed = (
            json.loads(raw, object_pairs_hook=object_pairs)
            if isinstance(raw, str)
            else deepcopy(raw)
        )
    except (ValueError, TypeError, RecursionError) as exc:
        raise ResearchValidationError(
            "SCHEMA_VALIDATION_FAILED", "invalid research JSON"
        ) from exc
    if parsed is None:
        return {"summary": "模型返回空 JSON；请查看执行诊断。", "_bodyShape": "null"}
    if isinstance(parsed, dict):
        if duplicates:
            parsed["_bodyDuplicateFields"] = list(dict.fromkeys(duplicates))
        return parsed
    if isinstance(parsed, list):
        return {"topicCatalog": parsed, "_bodyShape": "array"}
    return {"summary": str(parsed), "_bodyShape": "scalar"}


def normalize_industries(values, warnings, path):
    """Global industry identity is independent of the per-run search budget."""
    from backend.macro.research_topics import global_industry_identity

    result, original, mappings = [], _texts(values), []
    for value in original:
        resolved, basis = global_industry_identity(value)
        if resolved not in result:
            result.append(resolved)
        if resolved != value:
            mappings.append({"original": value, "industryId": resolved, "basis": basis})
            _warn(
                warnings,
                path,
                "INDUSTRY_NORMALIZED",
                f"行业标签 {value} 按 {resolved} 展示；保留原始行业洞察。",
            )
    return result, original, mappings


def _references(value, aliases, warnings, path):
    valid, invalid = [], []
    for ref in _texts(value):
        target = aliases.get(ref)
        if target is None:
            invalid.append(ref)
        elif target not in valid:
            valid.append(target)
    if invalid:
        _warn(
            warnings,
            path,
            "SOURCE_REFERENCE_REMOVED",
            "已移除无法对应来源的引用，保留原文观点。",
        )
    return valid


def _date(value, now, warnings, path, *, upcoming=False, timestamp=False):
    from backend.macro.daily_research import _day, _stamp, _TZ

    if not value:
        return None
    parsed = _stamp(value) if timestamp else _day(value)
    if parsed is None or (timestamp and parsed.tzinfo is None):
        _warn(
            warnings, path, "DATE_CLEARED", "日期无法识别，保留事项并标记日期待确认。"
        )
        return None
    if not upcoming and (
        parsed > now if timestamp else parsed > now.astimezone(_TZ).date()
    ):
        _warn(warnings, path, "DATE_CLEARED", "来源日期晚于研究时间，日期待确认。")
        return None
    return parsed.isoformat()


def _cycle(raw, *, aliases, sources, now, industries, warnings, path):
    from backend.macro.industry_cycle import (
        normalize_industry_cycle,
        IndustryEvidence,
        RISK_CHECKS,
        industry_risk_policy,
    )

    baseline = normalize_industry_cycle(None)
    if not isinstance(raw, dict):
        return baseline
    proposal = {
        key: deepcopy(raw[key])
        for key in ("stage", "evidenceSummary", "sourceIds", "evidence", "riskChecks")
        if key in raw
    }
    proposal.setdefault("stage", "UNKNOWN")
    proposal.setdefault("evidenceSummary", "基于已有资讯研判。")
    proposal["sourceIds"] = [
        ref for ref in _texts(proposal.get("sourceIds")) if ref in aliases
    ]
    for field in ("evidence", "riskChecks"):
        proposal[field] = [
            deepcopy(row) for row in _rows(proposal.get(field)) if isinstance(row, dict)
        ]
        for row in proposal[field]:
            row["sourceIds"] = [
                ref for ref in _texts(row.get("sourceIds")) if ref in aliases
            ]
    original_evidence = deepcopy(proposal["evidence"])
    evidence = []
    for index, row in enumerate(proposal["evidence"]):
        try:
            evidence.append(
                IndustryEvidence.model_validate(
                    {
                        key: value
                        for key, value in row.items()
                        if key in IndustryEvidence.model_fields
                    }
                ).model_dump()
            )
        except ValueError:
            _warn(
                warnings,
                f"{path}.evidence[{index}]",
                "EVIDENCE_RETAINED_AS_TEXT",
                "单条证据结构不完整，保留原文研判而不冒充已核事实。",
            )
    proposal["evidence"] = evidence
    by_kind = {}
    for row in proposal["riskChecks"]:
        kind = _text(row.get("kind"))
        if kind not in RISK_CHECKS:
            _warn(
                warnings,
                path + ".riskChecks",
                "RISK_CHECK_RETAINED_AS_TEXT",
                "未识别风险分类已保留在原始研判中。",
            )
            continue
        fixed = {
            "kind": kind,
            "status": _text(row.get("status"))
            if _text(row.get("status")) in {"FOUND", "NOT_FOUND", "UNVERIFIED"}
            else "UNVERIFIED",
            "query": _text(
                row.get("query"), " ".join(industries) + " " + RISK_CHECKS[kind]
            )[:300],
            "summary": _text(
                row.get("summary"), "基于已有资讯研判，未执行额外反方搜索。"
            )[:300],
            "sourceIds": row["sourceIds"],
        }
        previous = by_kind.get(kind)
        if previous is None or fixed["status"] == "FOUND":
            # A repair path cannot create a second rejection boundary. Keep
            # larger reference sets for the fallback risk observation too.
            by_kind[kind] = fixed
    if set(by_kind) != set(RISK_CHECKS):
        _warn(
            warnings,
            path + ".riskChecks",
            "RISK_CHECK_INCOMPLETE",
            "未提供的反方核查已标记为未核查，观点和基金参考价位全部保留。",
        )
    for kind, label in RISK_CHECKS.items():
        if kind not in by_kind:
            by_kind[kind] = {
                "kind": kind,
                "status": "UNVERIFIED",
                "query": " ".join(industries) + " " + label,
                "summary": "本轮未完成此类额外反方搜索，基于已有资讯研判。",
                "sourceIds": [],
            }
    proposal["riskChecks"] = list(by_kind.values())
    try:
        normalized = normalize_industry_cycle(
            proposal,
            source_aliases=aliases,
            sources=sources,
            now=now,
            expected_industries=industries,
            require_checks=False,
        )
        normalized["declaredStage"] = _text(raw.get("stage"), "UNKNOWN")
        normalized["declaredEvidence"] = deepcopy(original_evidence)
        normalized["declaredRiskChecks"] = deepcopy(_rows(raw.get("riskChecks")))
        return normalized
    except (ValueError, TypeError, KeyError):
        # Keep risk prose and declared evidence rather than replacing a failed
        # check with invented NOT_FOUND conclusions or rejecting the direction.
        _warn(
            warnings,
            path,
            "INDUSTRY_AUDIT_INCOMPLETE",
            "行业审核结构未完整对应；保留原始风险与观点。",
        )
        baseline.update(
            evidenceSummary=_text(
                raw.get("evidenceSummary"), baseline["evidenceSummary"]
            ),
            declaredStage=_text(raw.get("stage"), "UNKNOWN"),
            declaredEvidence=deepcopy(proposal["evidence"]),
            declaredRiskChecks=deepcopy(proposal["riskChecks"]),
            sourceIds=_references(
                raw.get("sourceIds"), aliases, warnings, path + ".sourceIds"
            ),
        )
        baseline["declaredEvidence"] = original_evidence
        baseline["riskChecks"] = [
            {
                **row,
                "sourceIds": [
                    aliases[ref] for ref in row["sourceIds"] if ref in aliases
                ],
                "verification": "UNVERIFIED",
                "strength": "SUSPECTED" if row["status"] == "FOUND" else "NONE",
            }
            for row in proposal["riskChecks"]
        ]
        baseline["suspectedRiskFlags"] = [
            row["kind"] for row in proposal["riskChecks"] if row["status"] == "FOUND"
        ]
        baseline["riskFlags"] = list(baseline["suspectedRiskFlags"])
        baseline.update(industry_risk_policy(baseline))
        return baseline


def sanitize_fund_plans(
    proposals, packet, *, direction=None, warnings=None, path="plans"
):
    """Keep selected funds; all price fields come exclusively from server data."""
    warnings = warnings if warnings is not None else []
    anchors = [
        row
        for field in (
            "discoveryActionableZones",
            "themeActionableZones",
            "actionableZones",
        )
        for row in _rows(packet.get(field))
        if isinstance(row, dict)
    ]
    result, seen = [], set()
    for index, proposal in enumerate(_rows(proposals)):
        at = f"{path}[{index}]"
        if not isinstance(proposal, dict):
            _warn(
                warnings,
                at,
                "ITEM_SKIPPED",
                "单条基金计划无法识别，其他基金和研究内容已保留。",
            )
            continue
        kind = _text(proposal.get("subjectKind"))
        subject = _text(proposal.get("subjectId")) or _text(proposal.get("fundCode"))
        code = _text(proposal.get("fundCode")) or (
            subject if re.fullmatch(r"\d{6}", subject) else ""
        )
        explicit_code = _text(proposal.get("fundCode"))
        explicit_code = (
            explicit_code if re.fullmatch(r"[0-9]{6}", explicit_code) else None
        )
        if not kind:
            kind = "nav" if code else "fund"
        if not subject:
            _warn(
                warnings, at, "ITEM_SKIPPED", "单条基金缺少可识别代码，其他计划已保留。"
            )
            continue
        candidates = [
            row
            for row in anchors
            if (row.get("subjectKind"), str(row.get("subjectId"))) == (kind, subject)
        ]
        conflict = bool(
            explicit_code
            and candidates
            and not any(
                row.get("fundCode", row.get("subjectId")) == explicit_code
                for row in candidates
            )
        )
        if explicit_code:
            candidates = [
                row
                for row in anchors
                if row.get("fundCode") == explicit_code
                or row.get("subjectKind") == "nav"
                and row.get("subjectId") == explicit_code
            ]
            if conflict or kind == "nav" and subject != explicit_code:
                _warn(
                    warnings,
                    at,
                    "FUND_IDENTITY_RECONCILED",
                    "基金代码与标的标识不一致；保留明确基金代码并重新匹配，未借用其他基金的价格。",
                )
            kind, subject, code = "nav", explicit_code, explicit_code
        elif not candidates and code:
            candidates = [
                row
                for row in anchors
                if row.get("fundCode") == code
                or row.get("subjectKind") == "nav"
                and row.get("subjectId") == code
            ]
        anchor = candidates[0] if candidates else None
        plan = (
            deepcopy(anchor)
            if anchor
            else {
                "subjectKind": kind,
                "subjectId": subject,
                "fundCode": code or None,
                "market": "FUND_CN" if code else None,
                "currentPrice": None,
                "tracks": [],
                "priceState": "UNAVAILABLE",
                "priceBasis": "UNIT_NAV" if code else None,
            }
        )
        tracks = [row for row in _rows(plan.get("tracks")) if isinstance(row, dict)]
        wanted = _text(proposal.get("selectedTrack")) or _text(proposal.get("track"))
        track = next(
            (row for row in tracks if row.get("track") == wanted), None
        ) or next(iter(tracks), None)
        selected = (
            track.get("track")
            if track
            else wanted
            if wanted in {"LEFT_VALUE", "RIGHT_TREND"}
            else "LEFT_VALUE"
        )
        identity = (plan.get("subjectKind"), plan.get("subjectId"), selected)
        if identity in seen:
            continue
        seen.add(identity)
        plan.update(
            track=selected,
            selectedTrack=selected,
            priceRevisionStatus=_text(proposal.get("priceRevisionStatus"))
            if _text(proposal.get("priceRevisionStatus"))
            in {"INITIAL", "MAINTAINED", "UPGRADED", "DOWNGRADED", "TAKE_PROFIT_ALERT"}
            else "INITIAL",
            priceRevisionBasis="PRICE",
            priceRevisionSourceIds=[],
            priceRevisionNote=_text(
                proposal.get("priceRevisionNote"), "参考价位由服务端真实行情计算。"
            ),
            calibrationRuleKeys=[],
            calibrationNote=_text(proposal.get("calibrationNote")),
            orderPriceAvailable=False,
        )
        if direction:
            plan.update(
                valueDirectionKey=direction["key"],
                valueDirectionTitle=direction["title"],
                auditStatus=direction.get("auditStatus", "UNAUDITED_SCOPE"),
            )
        if anchor is None:
            _warn(
                warnings,
                at,
                "FUND_PRICE_PENDING",
                "基金身份和观点已保留，等待真实历史净值计算参考价位。",
            )
        elif any(
            field in proposal
            for field in (
                "currentPrice",
                "entryZoneMin",
                "entryZoneMax",
                "stopLossPrice",
                "takeProfitT1",
                "takeProfitT2",
                "tracks",
            )
        ):
            _warn(
                warnings,
                at,
                "FUND_PRICE_RECOMPUTED",
                "模型价位已由服务端真实行情计算结果覆盖。",
            )
        result.append(plan)
    return result


def sanitize_value_directions(
    proposals,
    packet,
    now,
    aliases,
    *,
    sources=None,
    industry_reviews=None,
    historical_cycles=None,
    audit_completed_scopes=None,
    execution_scopes=None,
    warnings=None,
):
    from backend.macro.industry_cycle import (
        industry_audit_status,
        merge_industry_cycles,
    )
    from backend.macro.industry_contract import plan_industry_ids
    from backend.macro.daily_research import _persist_review_cycle

    warnings = warnings if warnings is not None else []
    completed = set(audit_completed_scopes or [])
    reviews = {row["industry"]: row for row in industry_reviews or []}
    macro_rows = {
        row.get("code"): row
        for row in _rows(packet.get("macros"))
        if isinstance(row, dict) and isinstance(row.get("code"), str)
    }
    result, keys = [], set()
    for index, raw in enumerate(_rows(proposals)):
        at = f"valueDirections[{index}]"
        if not isinstance(raw, dict):
            _warn(warnings, at, "ITEM_SKIPPED", "单条方向无法识别，其他方向已保留。")
            continue
        title = _text(raw.get("title")) or _text(raw.get("thesis"))[:100]
        if not title:
            _warn(
                warnings, at, "ITEM_SKIPPED", "单条方向没有可用观点，其他方向已保留。"
            )
            continue
        key = _text(raw.get("key"))
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) or key in keys:
            key = (
                "direction-"
                + hashlib.sha256(f"{index}:{title}".encode()).hexdigest()[:16]
            )
        keys.add(key)
        declared = list(
            dict.fromkeys(
                code
                for field in (
                    "reviewIndustryIds",
                    "relatedIndustryIds",
                    "industryCodes",
                )
                for code in _texts(raw.get(field))
            )
        )
        industries, original, mappings = normalize_industries(
            declared, warnings, at + ".reviewIndustryIds"
        )
        plan_scopes = set()
        for plan in _rows(raw.get("plans", raw.get("fundPlans"))):
            if isinstance(plan, dict):
                try:
                    plan_scopes.update(plan_industry_ids(packet, plan))
                except (ValueError, TypeError, KeyError):
                    _warn(
                        warnings,
                        at + ".plans",
                        "FUND_INDUSTRY_PENDING",
                        "单只基金行业归属待匹配，保留基金计划。",
                    )
        expected_industries = list(dict.fromkeys([*industries, *sorted(plan_scopes)]))
        industries = expected_industries
        audit = industry_audit_status(industries, completed)
        direction = {
            "key": key,
            "title": title,
            "thesis": _text(raw.get("thesis"), title),
            "signal": _text(raw.get("signal"))
            if _text(raw.get("signal"))
            in {"LEFT_VALUE", "RIGHT_TREND", "TAKE_PROFIT", "WATCH", "AVOID"}
            else "WATCH",
            "sourceIds": _references(
                raw.get("sourceIds"), aliases, warnings, at + ".sourceIds"
            ),
            "macroCodes": _texts(raw.get("macroCodes")),
            "quantitativeFundCodes": _texts(raw.get("quantitativeFundCodes")),
            "watchFor": _texts(raw.get("watchFor")),
            "reviewIndustryIds": industries,
            "auditStatus": audit,
            "auditNote": "基于已有资讯研判"
            if audit == "UNAUDITED_SCOPE"
            else "已完成行业反方核查",
            "qualificationStatus": "FORMAL"
            if audit == "AUDITED"
            else "OBSERVATION_ONLY",
            "investmentEligible": False,
            "formalEligibilityReasons": []
            if audit == "AUDITED"
            else ["DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED"],
        }
        if mappings:
            direction.update(
                originalReviewIndustryIds=original,
                originalIndustryIds=original,
                industryMappings=mappings,
            )
        direction["industryCycle"] = _cycle(
            raw.get("industryCycle"),
            aliases=aliases,
            sources=sources or {},
            now=now,
            industries=expected_industries or [title],
            warnings=warnings,
            path=at + ".industryCycle",
        )
        for industry in industries:
            direction["industryCycle"] = merge_industry_cycles(
                direction["industryCycle"], reviews.get(industry)
            )
        for identity in [("key", key), *(("industry", code) for code in industries)]:
            if historical_cycles and identity in historical_cycles:
                direction["industryCycle"] = _persist_review_cycle(
                    direction["industryCycle"], historical_cycles[identity], now
                )
        direction["industryCycle"]["auditStatus"] = audit
        direction["industryCycle"]["searchExecutionVerified"] = bool(
            industries
        ) and set(industries) <= set(execution_scopes or [])
        direction["sourceIds"] = sorted(
            set(direction["sourceIds"])
            | set(direction["industryCycle"].get("sourceIds", []))
        )
        invalid_macros = [
            code for code in direction["macroCodes"] if code not in macro_rows
        ]
        if invalid_macros:
            direction["macroCodes"] = [
                code for code in direction["macroCodes"] if code in macro_rows
            ]
            _warn(
                warnings,
                at + ".macroCodes",
                "MACRO_REFERENCE_REMOVED",
                "未知宏观数据引用已移除，保留方向的文字研判。",
            )
        for field in ("catalysts", "risks"):
            direction[field] = []
            for note_index, note in enumerate(_rows(raw.get(field))):
                if isinstance(note, str):
                    note = {"summary": note}
                if isinstance(note, dict) and _text(note.get("summary")):
                    direction[field].append(
                        {
                            "summary": note["summary"],
                            "sourceIds": _references(
                                note.get("sourceIds"),
                                aliases,
                                warnings,
                                f"{at}.{field}[{note_index}].sourceIds",
                            ),
                        }
                    )
        direction["plans"] = sanitize_fund_plans(
            raw.get("plans", raw.get("fundPlans")),
            packet,
            direction=direction,
            warnings=warnings,
            path=at + ".plans",
        )
        unadjusted_plans = deepcopy(direction["plans"])
        for plan in direction["plans"]:
            own_industries = sorted(plan_industry_ids(packet, plan))
            from backend.macro.industry_cycle import normalize_plan_industry_cycle

            try:
                own = normalize_plan_industry_cycle(
                    raw.get("industryCycle"),
                    expected_industries=own_industries,
                    source_aliases=aliases,
                    sources=sources or {},
                    now=now,
                )
            except (ValueError, TypeError, KeyError):
                own = _cycle(
                    raw.get("industryCycle"),
                    aliases=aliases,
                    sources=sources or {},
                    now=now,
                    industries=own_industries or [title],
                    warnings=warnings,
                    path=at + ".plans.industryCycle",
                )
            if (
                not own_industries
                and not industry_reviews
                and not (packet.get("industryCycleResearch") or {}).get("required")
            ):
                own = deepcopy(direction["industryCycle"])
            for industry in own_industries:
                own = merge_industry_cycles(own, reviews.get(industry))
                if historical_cycles and ("industry", industry) in historical_cycles:
                    own = _persist_review_cycle(
                        own, historical_cycles[("industry", industry)], now
                    )
            plan["industryCycle"] = own
            own["searchExecutionVerified"] = bool(own_industries) and set(
                own_industries
            ) <= set(execution_scopes or [])
            if own.get("industryRiskWarning"):
                plan["industryRiskWarning"] = own["industryRiskWarning"]
            # The normal deterministic overlay needs unrounded server anchors.
            # Older snapshots keep their visible reference band until a fresh
            # NAV calculation supplies that base; the risk note remains shown.
            if isinstance(
                (plan.get("industryAdjustmentBase") or {}).get("leftTrack"), dict
            ):
                from backend.indicators.actionable_zones import apply_industry_cycle

                plan.update(apply_industry_cycle(plan, own))
            elif own.get("leftDiscountScale", 0) > 0:
                plan["industryRiskAdjustment"] = {
                    "state": "INSUFFICIENT",
                    "riskLevel": own.get("riskLevel"),
                    "entryShiftScale": own.get("leftDiscountScale"),
                    "referenceBandPreserved": True,
                }
            from backend.macro.price_revision import (
                index_previous_plans,
                previous_plan,
                normalize_price_revision,
            )
            from backend.macro.alpha_research import _program_plan

            previous = index_previous_plans(
                [
                    *packet.get("previousActionablePlans", []),
                    *(
                        old_plan
                        for old_direction in packet.get("previousValueDirections", [])
                        for old_plan in old_direction.get("plans", [])
                    ),
                ],
                packet.get("discoveryActionableZones", []),
            )
            old = previous_plan(previous, plan, plan["selectedTrack"])
            try:
                _, _, track = _program_plan(
                    plan, plan["selectedTrack"], now, require_theme=False
                )
                old_track = (
                    _program_plan(old, plan["selectedTrack"], now, require_theme=False)[
                        2
                    ]
                    if old
                    else None
                )
                normalize_price_revision(plan, plan, track, old, old_track)
            except (ValueError, TypeError, KeyError):
                plan["priceRevisionStatus"] = "MAINTAINED" if old else "INITIAL"
        direction["investmentEligible"] = (
            bool(direction["plans"]) and direction["signal"] != "AVOID"
        )
        direction["macroEvidence"] = [
            deepcopy(row)
            for row in _rows(packet.get("macros"))
            if isinstance(row, dict) and row.get("code") in direction["macroCodes"]
        ]
        direction["quantitativeEvidence"] = []
        from backend.macro.value_directions import direction_quantitative_evidence

        scan = (
            packet.get("quantitativeScan")
            if isinstance(packet.get("quantitativeScan"), dict)
            else {}
        )
        valid_quantitative_codes = []
        for code in direction["quantitativeFundCodes"]:
            try:
                evidence = direction_quantitative_evidence(
                    scan.get("items"), [code], now, plans=unadjusted_plans
                )
            except (ValueError, TypeError, KeyError):
                _warn(
                    warnings,
                    at + ".quantitativeFundCodes",
                    "QUANTITATIVE_REFERENCE_REMOVED",
                    "量化引用未对应真实扫描记录，保留文字判断与基金计划。",
                )
                continue
            valid_quantitative_codes.append(code)
            direction["quantitativeEvidence"].extend(evidence)
        direction["quantitativeFundCodes"] = valid_quantitative_codes
        result.append(direction)
    return result


def sanitize_report(raw, packet, source_checks, *, completed_at, research_id=None):
    from backend.macro import daily_research as core
    from backend.macro.research_mentions import (
        extract_mentioned_funds,
        safe_mentioned_funds,
    )

    parsed = parse_report(raw)
    now = core._now(completed_at)
    packet = packet if isinstance(packet, dict) else {}
    checks = source_checks if isinstance(source_checks, dict) else {}
    warnings = []
    parsed = _json_safe(parsed, warnings)
    if parsed.get("_bodyShape"):
        _warn(
            warnings,
            "$",
            "BODY_NORMALIZED",
            "JSON 主体已转换为研究内容，原始输出仍保留于审计。",
        )
    if parsed.get("_bodyDuplicateFields"):
        _warn(
            warnings,
            "$",
            "DUPLICATE_JSON_FIELDS",
            "重复 JSON 字段已按最后一个完整值解析，完整原始内容仍保留于审计。",
        )
    report = {
        "summary": _text(
            parsed.get("summary"), "本次研究内容已保存，详见各方向和主题。"
        ),
        "macroCycle": _text(parsed.get("macroCycle"), "宏观研判详见本次研究。"),
        "methodVersion": core.VERSION,
        "asOf": now.isoformat(),
        "executionStatus": "COMPLETED",
        "sources": [],
        "events": [],
        "upcoming": [],
        "coverage": [],
        "energy": [],
        "validationWarnings": warnings,
    }
    aliases, sources = {}, {}
    for index, raw_source in enumerate(_rows(parsed.get("sources"))):
        at = f"sources[{index}]"
        if not isinstance(raw_source, dict):
            _warn(
                warnings, at, "ITEM_SKIPPED", "单条来源结构无法识别，正文观点仍保留。"
            )
            continue
        ref = _text(raw_source.get("id"), f"source-{index}")
        try:
            url = core._url(raw_source.get("url"))
        except (ValueError, TypeError):
            _warn(
                warnings,
                at,
                "SOURCE_URL_REMOVED",
                "来源网址无效，正文观点保留为模型研判。",
            )
            continue
        identity = core._identity("source", url)
        if ref in aliases and aliases[ref] != identity:
            _warn(
                warnings,
                at,
                "SOURCE_ID_RENAMED",
                "来源标识重复，保留首条引用与其余来源正文。",
            )
        else:
            aliases[ref] = identity
        aliases[identity] = identity
        check = checks.get(ref) if isinstance(checks.get(ref), dict) else {}
        valid_check = (
            check.get("verification") == "SOURCE_CHECKED"
            and check.get("quoteMatched") is True
            and check.get("url") == url
            and core._stamp(check.get("retrievedAt")) is not None
            and core._stamp(check.get("retrievedAt")) <= now
            and core._visible(check, now)
            and re.fullmatch(r"[a-fA-F0-9]{64}", str(check.get("contentHash") or ""))
            is not None
        )
        row = {
            "id": identity,
            "url": url,
            "title": _text(raw_source.get("title"), "研究来源"),
            "quote": _text(raw_source.get("quote")),
            "publishedDate": _date(
                raw_source.get("publishedDate"), now, warnings, at + ".publishedDate"
            ),
            "publishedAt": _date(
                check.get("publishedAt")
                if valid_check
                else raw_source.get("publishedAt"),
                now,
                warnings,
                at + ".publishedAt",
                timestamp=True,
            ),
            "retrievedAt": _date(
                check.get("retrievedAt"),
                now,
                warnings,
                at + ".retrievedAt",
                timestamp=True,
            ),
            "verification": "SOURCE_CHECKED" if valid_check else "UNVERIFIED",
            "contentHash": check.get("contentHash") if valid_check else None,
        }
        sources[identity] = row
    report["sources"] = list(sources.values())
    from backend.macro.cycle_insights import sanitize_cycle_insights

    cycle = sanitize_cycle_insights(
        parsed.get("cycleInsights"), aliases=aliases, now=now, warnings=warnings
    )
    if cycle:
        report["cycleInsights"] = cycle
    reviews = []
    for index, review in enumerate(_rows(parsed.get("industryCycleReviews"))):
        if not isinstance(review, dict):
            _warn(
                warnings,
                f"industryCycleReviews[{index}]",
                "ITEM_SKIPPED",
                "单条行业审核损坏，其他内容已保留。",
            )
            continue
        industries, original, mappings = normalize_industries(
            [review.get("industry")],
            warnings,
            f"industryCycleReviews[{index}].industry",
        )
        if not industries:
            continue
        cycle = _cycle(
            {
                "stage": "UNKNOWN",
                "evidenceSummary": "基于已有资讯研判。",
                "evidence": [],
                **review,
            },
            aliases=aliases,
            sources=sources,
            now=now,
            industries=industries,
            warnings=warnings,
            path=f"industryCycleReviews[{index}]",
        )
        cycle.update(industry=industries[0], auditStatus="UNAUDITED_SCOPE")
        if mappings:
            cycle.update(originalIndustryIds=original, industryMappings=mappings)
        reviews.append(cycle)
    completed, execution_scopes = set(), set()
    try:
        from backend.macro.research_topics import frozen_manifest
        from backend.macro.research_assurance import audit_assurance, verified_tasks
        from backend.macro.industry_cycle import RISK_CHECKS

        manifest = frozen_manifest(packet)
        if manifest:
            assurance = audit_assurance(
                {row["industry"]: row for row in reviews},
                manifest,
                checks,
                now,
                research_id=research_id,
            )
            completed = {
                scope
                for scope, kinds in assurance["completedKinds"].items()
                if kinds == set(RISK_CHECKS)
            }
            executed = verified_tasks(checks, manifest, now, research_id=research_id)
            execution_scopes = {
                scope
                for scope in manifest["selectedScopes"]
                if all(
                    task["taskId"] in executed
                    for task in manifest["tasks"]
                    if task["industryId"] == scope
                )
            }
            # Existing articles can support an insight without proving that
            # this run executed its extra five-kind counter-evidence searches.
            completed.intersection_update(execution_scopes)
    except (ValueError, TypeError, KeyError):
        _warn(
            warnings,
            "industryCycleReviews",
            "AUDIT_METADATA_UNAVAILABLE",
            "行业核查元数据未完整对应，研究和基金价位仍正常展示。",
        )
    for review in reviews:
        review["searchExecutionVerified"] = review["industry"] in execution_scopes
        review["auditStatus"] = (
            "AUDITED" if review["industry"] in completed else "UNAUDITED_SCOPE"
        )
    report["industryCycleReviews"] = reviews
    current_reviews = deepcopy(reviews)
    historical_cycles = core._previous_industry_states(packet, now)
    for review in reviews:
        review.update(
            core._persist_review_cycle(
                review, historical_cycles.get(("industry", review["industry"])), now
            )
        )
    report["valueDirections"] = sanitize_value_directions(
        parsed.get("valueDirections"),
        packet,
        now,
        aliases,
        sources=sources,
        industry_reviews=reviews,
        historical_cycles=historical_cycles,
        audit_completed_scopes=completed,
        execution_scopes=execution_scopes,
        warnings=warnings,
    )
    from backend.macro.industry_cycle import earnings_recovery_verified

    recovered_industries = {
        direction["reviewIndustryIds"][0]
        for direction in report["valueDirections"]
        if len(direction.get("reviewIndustryIds", [])) == 1
        and earnings_recovery_verified(direction.get("industryCycle", {}))
    }
    report["industryCycleReviews"] = [
        core._persist_review_cycle(
            review,
            historical_cycles.get(("industry", review["industry"])),
            now,
            earnings_recovered=review["industry"] in recovered_industries,
        )
        for review in current_reviews
    ]
    for field in ("events", "upcoming"):
        for index, raw_item in enumerate(_rows(parsed.get(field))):
            at = f"{field}[{index}]"
            if isinstance(raw_item, str):
                raw_item = {"title": raw_item, "summary": raw_item}
            if not isinstance(raw_item, dict) or not (
                _text(raw_item.get("title")) or _text(raw_item.get("summary"))
            ):
                _warn(
                    warnings, at, "ITEM_SKIPPED", "单条事件无法识别，其他事项已保留。"
                )
                continue
            title = _text(raw_item.get("title"), _text(raw_item.get("summary"))[:200])
            row = {
                "id": core._identity(field, index, title),
                "title": title,
                "summary": _text(raw_item.get("summary"), title),
                "phase": _text(raw_item.get("phase"))
                if _text(raw_item.get("phase"))
                in {"PLANNED", "ANNOUNCED", "IMPLEMENTED", "REPORTED", "OPINION"}
                else "PLANNED"
                if field == "upcoming"
                else "REPORTED",
                "importance": _text(raw_item.get("importance"))
                if _text(raw_item.get("importance")) in {"LOW", "MEDIUM", "HIGH"}
                else "MEDIUM",
                "sourceIds": _references(
                    raw_item.get("sourceIds"), aliases, warnings, at + ".sourceIds"
                ),
                "factVerification": "UNVERIFIED",
                "reasoningType": "MODEL_CONDITIONAL",
                "marketReaction": "UNVERIFIED",
            }
            for name in (
                "channels",
                "watchFor",
                "unknowns",
                "affectedDirectionIds",
                "relatedTopicKeys",
                "relatedFormalDirectionKeys",
                "relatedMacroConcepts",
                "candidateConceptLabels",
            ):
                row[name] = _texts(raw_item.get(name))
            row["unknowns"] = list(dict.fromkeys(
                question for key in ("unknowns", "openQuestions", "stillUnknown")
                for question in _texts(raw_item.get(key))
            ))
            row["scenarios"] = [
                {
                    key: _text(item.get(key))
                    for key in ("condition", "impact", "counter")
                }
                for item in _rows(raw_item.get("scenarios"))
                if isinstance(item, dict)
            ]
            if field == "upcoming":
                row["scheduledDate"] = _date(
                    raw_item.get("scheduledDate"),
                    now,
                    warnings,
                    at + ".scheduledDate",
                    upcoming=True,
                )
                row["scheduledAt"] = _date(
                    raw_item.get("scheduledAt"),
                    now,
                    warnings,
                    at + ".scheduledAt",
                    upcoming=True,
                    timestamp=True,
                )
                if (
                    row["scheduledAt"]
                    and row["scheduledDate"]
                    and core._stamp(row["scheduledAt"])
                    .astimezone(core._TZ)
                    .date()
                    .isoformat()
                    != row["scheduledDate"]
                ):
                    row["scheduledDate"] = (
                        core._stamp(row["scheduledAt"])
                        .astimezone(core._TZ)
                        .date()
                        .isoformat()
                    )
                    _warn(
                        warnings,
                        at,
                        "DATE_RECONCILED",
                        "计划日期与明确时间冲突，已按明确时间修正。",
                    )
            row["id"] = core._identity(
                "event" if field == "events" else "planned",
                "economic-observation-v2",
                " ".join(row["title"].split()),
                *(
                    [" ".join(row["summary"].split()), row["phase"]]
                    if field == "events"
                    else [
                        row["scheduledAt"],
                        row["scheduledDate"],
                        sorted(row["watchFor"]),
                    ]
                ),
            )
            report[field].append(row)
        merged = {}
        for row in report[field]:
            identity = row["id"]
            if identity in merged:
                combined = core._merge_economic_observation(merged[identity], row)
                if combined is not None:
                    merged[identity] = combined
                    continue
                row["id"] = core._identity(field, identity, row)
            merged[row["id"]] = row
        report[field] = list(merged.values())
    from backend.macro.event_metadata import event_metadata

    for field in ("events", "upcoming"):
        for row in report[field]:
            # Keep source fidelity for the audit and identity merge above.
            row["displayQuestions"] = _event_questions(row["title"], row["unknowns"])
            row.update(
                event_metadata(
                    row,
                    [sources[ref] for ref in row["sourceIds"] if ref in sources],
                    now,
                )
            )
    for raw_coverage in _rows(parsed.get("coverage")):
        if isinstance(raw_coverage, dict):
            report["coverage"].append(
                {
                    "topic": _text(raw_coverage.get("topic"), "GLOBAL_EQUITIES"),
                    "status": _text(raw_coverage.get("status"))
                    if _text(raw_coverage.get("status"))
                    in {"FOUND", "NOT_FOUND", "UNVERIFIED"}
                    else "UNVERIFIED",
                    "summary": _text(raw_coverage.get("summary"), "详见研究方向。"),
                }
            )
    selected_energy = set(_texts(parsed.get("energy")))
    report["energy"] = [
        deepcopy(row)
        for row in _rows(packet.get("energy"))
        if isinstance(row, dict) and row.get("code") in selected_energy
    ]
    topics = parsed.get("topicCatalog", parsed.get("researchTopics"))
    report["topicCatalog"] = []
    topic_keys = set()
    for index, raw_topic in enumerate(_rows(topics)):
        if isinstance(raw_topic, str):
            raw_topic = {"title": raw_topic, "thesis": raw_topic}
        if not isinstance(raw_topic, dict) or not (
            _text(raw_topic.get("title")) or _text(raw_topic.get("thesis"))
        ):
            _warn(
                warnings,
                f"topicCatalog[{index}]",
                "ITEM_SKIPPED",
                "单条主题无法识别，其他主题已保留。",
            )
            continue
        title = _text(raw_topic.get("title"), _text(raw_topic.get("thesis"))[:100])
        key = _text(raw_topic.get("key"))
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) or key in topic_keys:
            key = (
                "topic-" + hashlib.sha256(f"{index}:{title}".encode()).hexdigest()[:16]
            )
        topic_keys.add(key)
        industries, original, mappings = normalize_industries(
            raw_topic.get("industryClaims"),
            warnings,
            f"topicCatalog[{index}].industryClaims",
        )
        topic = {
            "key": key,
            "title": title,
            "thesis": _text(raw_topic.get("thesis"), title),
            "industryClaims": industries,
            "originalIndustryIds": original,
            "industryMappings": mappings,
            "relatedIndustryIds": normalize_industries(
                raw_topic.get("relatedIndustryIds"),
                warnings,
                f"topicCatalog[{index}].relatedIndustryIds",
            )[0],
            "sourceIds": _references(
                raw_topic.get("sourceIds"),
                aliases,
                warnings,
                f"topicCatalog[{index}].sourceIds",
            ),
            "unknowns": _texts(raw_topic.get("unknowns")),
            "pendingTasks": _texts(raw_topic.get("pendingTasks")),
            "auditStatus": "AUDITED"
            if industries and set(industries) <= completed
            else "UNAUDITED_SCOPE",
        }
        topic["plans"] = sanitize_fund_plans(
            raw_topic.get("plans", raw_topic.get("fundPlans")),
            packet,
            direction={"key": key, "title": title, "auditStatus": topic["auditStatus"]},
            warnings=warnings,
            path=f"topicCatalog[{index}].plans",
        )
        report["topicCatalog"].append(topic)
    report["researchTopics"] = deepcopy(report["topicCatalog"])
    report["actionablePlans"] = [
        deepcopy(plan)
        for direction in report["valueDirections"]
        for plan in direction["plans"]
    ] + sanitize_fund_plans(
        parsed.get("actionablePlans"), packet, warnings=warnings, path="actionablePlans"
    )
    if isinstance(packet.get("quantitativeScan"), dict):
        report["quantitativeScan"] = deepcopy(packet["quantitativeScan"])
    try:
        from backend.integrations.daily_sources import industry_cycle_research_tasks

        input_at = core._stamp(packet.get("asOf")) or now
        tasks = industry_cycle_research_tasks(
            core.apply_entry_guards(packet, packet, input_at), input_at
        )
        partial = (
            bool(tasks["omittedCandidateCount"])
            or tasks.get("allocationVersion")
            in {"industry-allocation-v2", "industry-allocation-v3"}
            and bool(
                tasks["omittedRiskCandidateCount"] or tasks.get("excludedScopeCount", 0)
            )
        )
        report["industryResearchCoverage"] = {
            "maximumCandidates": tasks["maximumCandidates"],
            "candidateCount": len(tasks["candidates"]),
            "omittedCandidateCount": tasks["omittedCandidateCount"],
            "omittedRiskCandidateCount": tasks["omittedRiskCandidateCount"],
            "omittedIndustries": deepcopy(tasks["omittedIndustries"]),
            "omittedPlanIndustryIds": deepcopy(tasks["omittedPlanIndustryIds"]),
            "status": "PARTIAL" if partial else "COMPLETE",
        }
    except (ValueError, TypeError, KeyError):
        _warn(
            warnings,
            "industryResearchCoverage",
            "AUDIT_METADATA_UNAVAILABLE",
            "本轮行业核查覆盖元数据不完整，研究内容仍全部保留。",
        )
    structured_mentions = []
    for raw_mention in _rows(parsed.get("mentionedFunds")):
        if not isinstance(raw_mention, dict):
            continue
        code = _text(raw_mention.get("fundCode"))
        if not re.fullmatch(r"[0-9]{6}", code):
            continue
        structured_mentions.append(
            {
                "fundCode": code,
                "subjectKind": "nav",
                "subjectId": code,
                "market": "FUND_CN",
                "label": _text(raw_mention.get("label"))
                or _text(raw_mention.get("name"), code),
                "qualificationStatus": "OBSERVATION_ONLY",
                "investmentEligible": False,
                "identityVerification": "MODEL_REFERENCE",
                "directionKeys": _texts(raw_mention.get("directionKeys")),
                "topicKeys": _texts(raw_mention.get("topicKeys")),
                "mentionLocations": ["mentionedFunds"],
            }
        )
    mentions = [
        *structured_mentions,
        *safe_mentioned_funds(parsed.get("mentionedFunds")),
        *extract_mentioned_funds(report, packet),
    ]
    merged_mentions = {}
    for mention in mentions:
        key = mention["fundCode"]
        old = merged_mentions.get(key, {})
        row = {**old, **mention}
        for field in ("directionKeys", "topicKeys", "mentionLocations"):
            row[field] = list(
                dict.fromkeys([*_texts(old.get(field)), *_texts(mention.get(field))])
            )
        merged_mentions[key] = row
    report["mentionedFunds"] = list(merged_mentions.values())
    from backend.macro.research_event_links import normalize_event_links
    from backend.macro.research_topics import TopicProposal

    link_catalog = []
    original_topics = _rows(topics)
    for index, topic in enumerate(report["topicCatalog"]):
        projected = {key: deepcopy(topic[key]) for key in TopicProposal.model_fields}
        if index < len(original_topics) and isinstance(original_topics[index], dict):
            original_key = original_topics[index].get("key")
            if isinstance(original_key, str) and re.fullmatch(
                r"[a-z][a-z0-9_-]{0,63}", original_key
            ):
                projected["key"] = original_key
        link_catalog.append(projected)

    for field in ("events", "upcoming"):
        report[field] = [
            normalize_event_links(
                row,
                packet=packet,
                formal_keys=[
                    direction["key"] for direction in report["valueDirections"]
                ],
                source_aliases=aliases,
                topic_catalog=link_catalog,
            )
            for row in report[field]
        ]
    report = core.apply_entry_guards(report, packet, now)
    from backend.investment.opportunity_scoring import score_plan

    for direction in report["valueDirections"]:
        direction["plans"] = [
            score_plan(plan, packet, now) for plan in direction["plans"]
        ]
    if isinstance(report.get("quantitativeScan"), dict):
        from backend.macro.industry_contract import plan_industry_ids
        from backend.macro.industry_cycle import (
            merge_industry_cycles,
            normalize_industry_cycle,
        )
        from backend.indicators.actionable_zones import apply_industry_cycle

        reviews = {row["industry"]: row for row in report["industryCycleReviews"]}
        for index, item in enumerate(_rows(report["quantitativeScan"].get("items"))):
            if not isinstance(item, dict):
                continue
            own = normalize_industry_cycle(None)
            for industry in sorted(plan_industry_ids(packet, item)):
                own = merge_industry_cycles(own, reviews.get(industry))
            item["industryCycle"] = own
            if isinstance(
                (item.get("industryAdjustmentBase") or {}).get("leftTrack"), dict
            ):
                item = apply_industry_cycle(item, own)
            report["quantitativeScan"]["items"][index] = score_plan(item, packet, now)
    report["actionablePlans"] = [
        deepcopy(plan)
        for direction in report["valueDirections"]
        for plan in direction["plans"]
    ] + [
        score_plan(plan, packet, now)
        for plan in report["actionablePlans"]
        if not plan.get("valueDirectionKey")
    ]
    report = _json_safe(report, warnings)
    report["validationWarnings"] = deepcopy(warnings)
    return report
