"""Canonical scoped research matters, separate from trading qualification."""

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

from backend.macro.direction_models import _trend_usable, _usable
from backend.macro.market_repository import CATALOG, _number, _stamp

VERSION = "research-board-v2"
ENTRY_GATES = {
    "value",
    "trend",
    "macro",
    "product",
    "validation",
    "portfolio",
    "tradability",
}
LABELS = {
    **{code: value[0] for code, value in CATALOG.items()},
    "PMI": "制造业PMI",
    "INDEX_TREND": "当前指数趋势",
    "INDEX_VALUATION": "当前指数估值",
    "INDEX_EARNINGS": "企业盈利证据",
    "USD_FX": "美元/汇率事实",
    "YIELD_CURVE_DURATION": "收益率曲线/久期",
    "CREDIT_SPREAD_DEFAULT": "信用利差/违约事实",
    "FUND_MAPPING": "基金指数/方向归属",
    "LOCAL_ASSET_EVIDENCE": "当地资产基本面/价格/风险",
}


def action(kind, label, *, target=None, href=None):
    return {
        "type": kind,
        "label": label,
        "target": target,
        "href": href,
        "enabled": True,
        "disabledReason": None,
    }


def confirmed_breach(decision, portfolio):
    return (
        decision.get("state") == "POSITION_REVIEW"
        and portfolio.get("state") == "READY"
        and portfolio.get("heldSelected") is True
        and portfolio.get("budgetViolation") is True
        and portfolio.get("budgetState") == "BLOCKED"
    )


def _current_trend(evidence):
    return evidence.get("state") not in {
        "STALE",
        "CONFLICT",
        "UNVERIFIED",
        "NOT_COLLECTED",
        "UNAVAILABLE",
    } and _trend_usable(evidence)


def _source_results(collection):
    output = {}
    for key, row in (collection.get("sourceResults") or {}).items():
        if not isinstance(row, dict):
            continue
        result = {"status": row.get("status", "UNKNOWN")}
        code = row.get("httpStatus")
        if type(code) is int and 100 <= code <= 599:
            result["httpStatus"] = code
        for field in ("reason", "errorClass"):
            value = row.get(field)
            if isinstance(value, str) and re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{0,79}", value
            ):
                result[field] = value
        output[key] = result
    return output


def _verified_fact(item):
    return _usable(item) and (
        item.get("code") in CATALOG or item.get("valueVerification") == "VERIFIED"
    )


def _official_event(event, now):
    published = _stamp(event.get("publishedAt"))
    retrieved = _stamp(event.get("retrievedAt"))
    try:
        uri = urlsplit(event.get("sourceUri") or "")
        trusted = (
            uri.scheme == "https"
            and not uri.username
            and not uri.password
            and uri.port in (None, 443)
            and any(
                uri.hostname == host or (uri.hostname or "").endswith("." + host)
                for host in ("stats.gov.cn", "pbc.gov.cn", "federalreserve.gov")
            )
        )
    except ValueError:
        return False
    return (
        event.get("sourceType") == "OFFICIAL_FACT"
        and trusted
        and published is not None
        and published <= now
        and (
            event.get("retrievedAt") is None
            or retrieved is not None
            and retrieved <= now
        )
    )


def _qualified_entry(decision, portfolio, model):
    gates = decision.get("gates") or []
    validation = decision.get("validation") or {}
    return (
        decision.get("state") == "ENTRY_CANDIDATE"
        and len(gates) == len(ENTRY_GATES)
        and {gate.get("id") for gate in gates} == ENTRY_GATES
        and all(gate.get("state") == "PASS" for gate in gates)
        and validation.get("state") == "PASS"
        and validation.get("sourceMode") == "REAL_PIT"
        and validation.get("pointInTimeVerified") is True
        and portfolio.get("state") == "READY"
        and portfolio.get("heldSelected") is False
        and portfolio.get("budgetState") == "PASS"
        and portfolio.get("budgetViolation") is False
        and model.get("qualificationState") == "READY"
        and model.get("missingRequiredEvidence") == []
        and not model.get("counterFactors")
        and not model.get("failedConditions")
    )


def _blocker_group(item):
    blocker = item.get("blocker") or {}
    code = blocker.get("code", "")
    if blocker.get("state") == "PERMISSION_REQUIRED":
        return "PERMISSION"
    if code == "PERSONAL_PORTFOLIO":
        return "PORTFOLIO"
    if code == "PIT_VALIDATION":
        return "STRATEGY"
    if code in CATALOG or code in {
        "PMI",
        "CPI_YOY",
        "PPI_YOY",
        "M2_YOY",
        "USD_FX",
        "YIELD_CURVE_DURATION",
        "CREDIT_SPREAD_DEFAULT",
    }:
        return "MACRO"
    return "MARKET"


def _groups(items):
    definitions = (
        ("MARKET", "市场与产品证据", "指数、基金产品或归属判断受限。", "RESEARCH_DATA"),
        (
            "MACRO",
            "宏观数据与采集",
            "宏观传导判断需分别核查缺失或失败的来源。",
            "MACRO_WORKER",
        ),
        ("PERMISSION", "来源许可", "相应来源尚不能用于自动研究。", "ADMIN"),
        ("PORTFOLIO", "个人持仓与预算", "个人敞口或风险评估尚不完整。", "PORTFOLIO"),
        (
            "STRATEGY",
            "策略与历史验证",
            "尚不能认证真实时点策略有效性。",
            "RESEARCH_DATA",
        ),
    )
    return [
        {
            "id": key,
            "label": label,
            "summary": f"当前范围有{len(children)}项研究限制，子项状态分别保留。"
            if children
            else "当前范围未列出此类限制，不代表已完成该类判断。",
            "impact": impact
            if children
            else "仅为分类概览，判断资格以实际证据和条件为准。",
            "owner": owner,
            "itemIds": children,
        }
        for key, label, impact, owner in definitions
        for children in [
            [
                item["id"]
                for item in items
                if item.get("blocker") and _blocker_group(item) == key
            ]
        ]
    ]


def build_research_board(
    context,
    series,
    market_evidence,
    direction,
    decision,
    portfolio,
    *,
    model,
    radar,
    scope,
    collection=None,
    can_manage_operations=False,
    now=None,
):
    now = now or datetime.now(timezone.utc)
    collection = collection or {}
    normalized = {
        key: str(scope[key]) if scope.get(key) is not None else None
        for key in (
            "direction_id",
            "index_id",
            "instrument_id",
            "fund_code",
            "fund_market",
        )
    }
    scope_hash = hashlib.sha256(
        json.dumps(normalized, sort_keys=True).encode()
    ).hexdigest()[:20]
    assets = {
        "directionId": normalized["direction_id"],
        "indexId": normalized["index_id"],
        "instrumentId": normalized["instrument_id"],
        "fundCode": normalized["fund_code"],
        "fundMarket": normalized["fund_market"],
    }
    holding = (
        "HELD"
        if portfolio.get("heldSelected") is True
        else "NOT_HELD"
        if portfolio.get("heldSelected") is False
        else "UNKNOWN"
    )
    items = {}
    sources = _source_results(collection)

    def add(
        key,
        category,
        summary,
        impact,
        *,
        tone="GRAY",
        severity="LOW",
        confidence="UNKNOWN",
        evidence=(),
        owner="RESEARCH_SERVICE",
        next_action=None,
        blocker=None,
        index_id=None,
        fact_verification="UNKNOWN",
        condition=None,
    ):
        identity = f"{scope_hash}:{key}"
        item = {
            "id": identity,
            "key": key,
            "category": category,
            "severity": severity,
            "confidence": confidence,
            "factVerification": fact_verification,
            "tone": tone,
            "summary": summary,
            "impact": impact,
            "evidenceIds": sorted(set(evidence)),
            "selectionRelevance": "CURRENT_SELECTION",
            "holdingRelevance": holding,
            "affectedAssets": {**assets, **({"indexId": index_id} if index_id else {})},
            "owner": owner,
            "actionability": "ADMIN"
            if next_action and next_action["type"] == "VIEW_OPERATIONS"
            else "USER"
            if next_action
            and next_action["type"]
            in {
                "VIEW_HOLDINGS",
                "VIEW_POLICY",
                "UPDATE_HOLDINGS",
                "VIEW_CANDIDATES",
                "TRACK",
            }
            else "SYSTEM"
            if owner == "MACRO_WORKER"
            else "READ_ONLY",
            "nextAction": next_action
            or action("VIEW_DATA", "查看数据状态", target="research-data"),
            "firstSeenAt": None,
            "lastChangedAt": None,
            "status": "OPEN",
        }
        if blocker:
            item["blocker"] = blocker
        if condition is not None:
            item["condition"] = condition
        items[identity] = item
        return identity

    if confirmed_breach(decision, portfolio):
        add(
            "personal-budget",
            "RISK",
            "当前持仓触发已确认风险预算。",
            "复核敞口、限额差距和风险政策；当前事项不自动生成交易。",
            tone="RED",
            severity="HIGH",
            confidence="VERIFIED",
            fact_verification="VERIFIED",
            evidence=("portfolio", "confirmed-policy"),
            owner="USER",
            next_action=action(
                "VIEW_HOLDINGS", "查看我的持仓", href="/dashboard?scope=MINE"
            ),
        )

    evidence_by_code = {
        item["code"]: item for item in context.get("items", []) + series
    }
    event_ids = {e["id"] for e in context.get("events", []) if _official_event(e, now)}
    for change in radar.get("changes", []):
        if change.get("kind") in {"GAP"} or change.get("id") in {
            "personal-budget",
            "no-change",
        }:
            continue
        refs = change.get("evidenceIds") or []
        if not refs or not all(
            code in event_ids
            or (
                code in evidence_by_code
                and evidence_by_code[code].get("state") == "READY"
                and _number(evidence_by_code[code].get("value")) is not None
            )
            for code in refs
        ):
            continue
        related = change.get("relatedDirectionIds") or []
        if related and direction.get("id") not in related:
            continue
        foreign_direction = direction.get("market_id", direction.get("marketId")) in {
            "us",
            "gold",
        }
        foreign_codes = {"US_T10_NOMINAL", "US_T10_REAL", "DXY", "USD_CNY"}
        domestic_codes = {
            "CN_T10",
            "DR007",
            "M1_YOY",
            "TSF_STOCK_YOY",
            "CORP_MEDLONG_LOANS_YTD",
        }
        topic = (
            "research-global"
            if (
                change["id"].startswith("event:")
                or any(
                    code in CATALOG and code not in foreign_codes | domestic_codes
                    for code in refs
                )
                or any(code in foreign_codes for code in refs)
                and not foreign_direction
                or any(code in domestic_codes for code in refs)
                and foreign_direction
            )
            else "research-macro"
        )
        risk = change.get("kind") == "RISK"
        important = change.get("priority") in {"ATTENTION", "CRITICAL"}
        add(
            change["id"],
            "RISK" if risk else "CHANGE",
            change["summary"],
            change.get("impact", "核查原始证据及当前条件。"),
            tone="ORANGE" if risk or important else "BLUE",
            severity="HIGH"
            if important and change.get("severity") == "HIGH"
            else "MEDIUM"
            if risk or important
            else "LOW",
            confidence="PARTIAL"
            if risk
            or change.get("kind") == "SUPPORT"
            or change["id"].startswith("event:")
            else "VERIFIED",
            fact_verification="VERIFIED"
            if all(
                code in event_ids or _verified_fact(evidence_by_code.get(code, {}))
                for code in refs
            )
            else "UNKNOWN",
            evidence=refs,
            next_action=action(
                "VIEW_EVIDENCE",
                "查看研究依据",
                target=topic,
            ),
        )

    for evidence in market_evidence:
        if not _current_trend(evidence):
            continue
        positive = (
            _number(evidence["trend"]["distanceMA200"]) > 0
            and _number(evidence["trend"]["momentum63"]) > 0
        )
        add(
            "trend:" + evidence["indexId"],
            "OPPORTUNITY" if positive else "CHANGE",
            evidence.get("label", evidence["indexId"])
            + ("当前符合趋势研究条件。" if positive else "当前尚未满足趋势研究条件。"),
            "仅说明当前MA200与动量条件，继续核查该资产适用的估值、风险、基金质量、策略验证和个人预算。"
            if positive
            else "当前MA200与63期正动量条件未同时满足；前后事实不足时不推断刚刚转弱。",
            tone="GREEN" if positive else "BLUE",
            severity="MEDIUM",
            confidence="VERIFIED",
            fact_verification="VERIFIED",
            condition="PASSED" if positive else "FAILED",
            evidence=(evidence["indexId"],),
            next_action=action(
                "VIEW_CANDIDATES",
                "查看当前指数与基金",
                target="op-research-targets",
            ),
            index_id=evidence["indexId"],
        )

    qualified = _qualified_entry(decision, portfolio, model)
    if qualified:
        add(
            "qualification:entry",
            "OPPORTUNITY",
            "当前已满足规定的完整研究资格。",
            "价值、趋势、宏观、产品、真实PIT、个人预算及交易机制均已核查，仍需人工复核。",
            tone="GREEN",
            severity="MEDIUM",
            confidence="VERIFIED",
            fact_verification="VERIFIED",
            evidence=ENTRY_GATES,
            next_action=action(
                "VIEW_CANDIDATES", "复核当前指数与基金", target="op-research-targets"
            ),
        )

    def add_gap(code, index=None):
        label = LABELS.get(code, code)
        source_key = CATALOG.get(code, (None, None, None, None))[3]
        index_fact = index is not None and code in {"INDEX_TREND", "INDEX_VALUATION"}
        index_collection = (index.get("collection") or {}) if index_fact else {}
        if index_fact:
            source_key = "INDEX_RESEARCH:" + index["indexId"]
            if index_collection:
                sources.update(
                    _source_results(
                        {
                            "sourceResults": {
                                source_key: {
                                    **index_collection,
                                    "status": "SUCCESS"
                                    if index_collection.get("status") == "COMPLETED"
                                    else index_collection.get("status", "UNKNOWN"),
                                }
                            }
                        }
                    )
                )
        source = sources.get(source_key, {})
        row = evidence_by_code.get(code, {})
        state = row.get("state", "NOT_COLLECTED")
        if source.get("status") == "FAILED":
            state = "FAILED"
        elif source.get("status") == "PERMISSION_REQUIRED":
            state = "PERMISSION_REQUIRED"
        if index:
            field = {
                "INDEX_TREND": "trend",
                "INDEX_VALUATION": "valuation",
                "INDEX_EARNINGS": "earnings",
            }.get(code)
            if field:
                state = index.get(field, {}).get("state", "UNKNOWN")
        if source.get("status") == "FAILED":
            state = "FAILED"
        if state == "READY":
            state = "UNVERIFIED"
        owner = (
            "ADMIN"
            if state == "PERMISSION_REQUIRED"
            else "MARKET_WORKER"
            if index_fact
            else "MACRO_WORKER"
            if source_key
            else "RESEARCH_DATA"
        )
        reason = {
            "FAILED": "来源获取失败，保留已有事实；是否恢复以实际采集记录为准。",
            "PERMISSION_REQUIRED": "尚无授权及许可客户端，由管理员核查来源许可；当前不自动采集。",
            "STALE": "已有事实已过期，暂不用于当前条件判断。",
            "CONFLICT": "当前证据存在冲突，需核对来源和口径。",
        }.get(state, "形成此项判断的必要数据或核验条件尚未完整。")
        attempted = (
            _stamp(
                index_collection.get("attemptedAt")
                or index_collection.get("collectedAt")
            )
            if index_fact
            else _stamp((collection.get("sourceAttemptAt") or {}).get(source_key))
        )
        if attempted is not None and attempted > now:
            attempted = None
        if state == "PARTIAL" and index_fact and code == "INDEX_VALUATION":
            reason = "已取得部分指数估值事实，PB等完整估值条件尚未齐备。"
        if attempted is None and index_collection.get("collectionState") != "CLAIMED":
            reason += "未检测到执行记录。"
        blocker = {
            "code": code,
            "state": state,
            "owner": owner,
            "reason": reason,
            "affectedJudgment": "完整价值判断" if code != "INDEX_TREND" else "趋势判断",
            "userFixable": False,
            "lastAttemptAt": attempted,
            "nextCheckAt": index_collection.get("nextCheckAt")
            if index_fact
            else collection.get("nextCheckAt")
            if source_key
            else None,
            "sourceKey": source_key,
        }
        return add(
            "required:" + code + (":" + index["indexId"] if index else ""),
            "SYSTEM" if state in {"FAILED", "STALE", "CONFLICT"} else "DATA_GAP",
            (index.get("label", index["indexId"]) + "：" if index else "")
            + label
            + "研究受限。",
            reason,
            owner=owner,
            evidence=(code, index["indexId"]) if index_fact else (code,),
            blocker=blocker,
            next_action=action("VIEW_DATA", "查看研究限制", target="research-data"),
            index_id=index["indexId"] if index else None,
        )

    required_missing = model.get("missingRequiredEvidence")
    if required_missing is None:
        required_missing = [
            code
            for code in model.get("requiredEvidence", [])
            if LABELS.get(code, code) in model.get("missingEvidence", [])
            or code in model.get("missingEvidence", [])
        ]
    for code in dict.fromkeys(required_missing):
        field = {
            "INDEX_TREND": "trend",
            "INDEX_VALUATION": "valuation",
            "INDEX_EARNINGS": "earnings",
        }.get(code)
        if field and market_evidence:
            for index in market_evidence:
                valid = (
                    _current_trend(index)
                    if field == "trend"
                    else index.get(field, {}).get("state") == "READY"
                )
                if not valid:
                    add_gap(code, index)
        else:
            add_gap(code)
    # Keep independently visible index analysis honest even if another requirement
    # prevented the direction model from reaching this index-specific check.
    for index in market_evidence:
        for code, field in (
            ("INDEX_VALUATION", "valuation"),
            ("INDEX_EARNINGS", "earnings"),
        ):
            if (
                code in model.get("requiredEvidence", [])
                and index.get(field, {}).get("state") != "READY"
            ):
                add_gap(code, index)

    for gate in decision.get("gates", []):
        if gate.get("state") != "UNKNOWN" or gate.get("id") not in {
            "product",
            "tradability",
            "validation",
            "portfolio",
        }:
            continue
        code = {
            "product": "PRODUCT_QUALITY",
            "tradability": "TRADABILITY",
            "validation": "PIT_VALIDATION",
            "portfolio": "PERSONAL_PORTFOLIO",
        }[gate["id"]]
        owner = "PORTFOLIO" if gate["id"] == "portfolio" else "RESEARCH_DATA"
        reason = (
            gate.get("reason") or "完成该项判断所需的来源或已确认配置尚未完整。"
        ) + "未检测到执行记录。"
        add(
            "required:" + code,
            "DATA_GAP",
            gate.get("label", code) + "研究受限。",
            reason,
            owner=owner,
            blocker={
                "code": code,
                "state": "UNKNOWN",
                "owner": owner,
                "reason": reason,
                "affectedJudgment": "个人风险评估"
                if gate["id"] == "portfolio"
                else "完整价值判断",
                "userFixable": gate["id"] == "portfolio",
                "lastAttemptAt": None,
                "nextCheckAt": None,
                "sourceKey": None,
            },
            next_action=action("VIEW_DATA", "查看研究限制", target="research-data"),
        )

    ranked = sorted(
        items.values(),
        key=lambda i: (
            i["tone"] != "RED",
            i["category"] in {"DATA_GAP", "SYSTEM"},
            i["category"] != "RISK",
            i["holdingRelevance"] != "HELD",
            i["category"] != "OPPORTUNITY",
            i["severity"] == "LOW",
            i["id"],
        ),
    )
    personal = [i["id"] for i in ranked if i["key"] == "personal-budget"]
    opportunities = [i["id"] for i in ranked if i["category"] == "OPPORTUNITY"]
    changes = [
        i["id"]
        for i in ranked
        if i["category"] in {"RISK", "CHANGE"}
        and i["key"] != "personal-budget"
        and i["severity"] in {"HIGH", "MEDIUM"}
    ]
    blockers = [i["id"] for i in ranked if i["category"] in {"DATA_GAP", "SYSTEM"}]
    checked = (
        portfolio.get("state") == "READY"
        and portfolio.get("budgetState") == "PASS"
        and portfolio.get("heldSelected") is not None
    )
    known_trends = any(_current_trend(e) for e in market_evidence)
    trend_items = [i for i in ranked if i["key"].startswith("trend:")]
    current_index_summary = trend_items[0]["summary"] if trend_items else None
    primary_opportunity = items[opportunities[0]]["summary"] if opportunities else None
    direction_facts = [
        text
        for text in [
            current_index_summary,
            (model.get("supports") or model.get("counterFactors") or [{}])[0].get(
                "summary"
            ),
        ]
        if text
    ]
    holding_state = "BREACH" if personal else "CHECKED" if checked else "UNKNOWN"
    opportunity_state = (
        "ENTRY_CANDIDATE"
        if qualified
        else "PRIORITY_RESEARCH"
        if opportunities
        else "WAIT_CONDITIONS"
        if known_trends
        else "INSUFFICIENT"
    )
    summaries = [
        {
            "id": "HOLDINGS",
            "label": "我的持仓风险",
            "state": holding_state,
            "summary": "有1项已确认的个人预算风险需要复核。"
            if personal
            else "当前范围已完成个人预算核查，仍需持续观察。"
            if checked
            else "持仓或风险尚未评估，先查看我的持仓与估值覆盖。",
            "tone": "RED" if personal else "BLUE" if checked else "GRAY",
            "itemIds": personal,
            "nextAction": action("VIEW_ISSUES", "查看持仓风险事项", target="HOLDINGS"),
        },
        {
            "id": "OPPORTUNITIES",
            "label": "研究机会",
            "state": opportunity_state,
            "summary": "当前选择已满足完整研究资格，仍需人工复核。"
            if opportunity_state == "ENTRY_CANDIDATE"
            else f"{primary_opportunity}完整资格需继续核查。"
            if opportunities
            else current_index_summary or "已有趋势证据，等待预设条件满足。"
            if known_trends
            else "当前范围尚无充分证据评价研究机会。",
            "tone": "GREEN"
            if opportunities or opportunity_state == "ENTRY_CANDIDATE"
            else "GRAY",
            "itemIds": opportunities,
            "nextAction": action(
                "VIEW_ISSUES", "查看研究机会事项", target="OPPORTUNITIES"
            ),
        },
        {
            "id": "CHANGES",
            "label": "重要变化",
            "state": "OBSERVED" if changes else "NO_CONFIRMED_CHANGE",
            "summary": items[changes[0]]["summary"]
            + (f"另有{len(changes) - 1}项当前条件可查看。" if len(changes) > 1 else "")
            if changes
            else "暂无已确认的重要变化；数据缺口另列处理状态。",
            "tone": "ORANGE"
            if any(i["tone"] == "ORANGE" for i in ranked if i["id"] in changes)
            else "BLUE",
            "itemIds": changes,
            "nextAction": action("VIEW_ISSUES", "查看重要变化", target="CHANGES"),
        },
    ]
    return {
        "methodVersion": VERSION,
        "asOf": now,
        "items": ranked,
        "summaries": summaries,
        "direction": {
            "label": direction.get("label", "当前方向"),
            "researchState": model.get("researchState", opportunity_state),
            "qualificationState": model.get("qualificationState", "INCOMPLETE"),
            "supports": model.get("supports", []),
            "counterFactors": model.get("counterFactors", []),
            "failedConditions": model.get("failedConditions", []),
            "recommendedChecks": model.get(
                "recommendedChecks", model.get("watchFor", [])
            ),
            "itemIds": [
                i["id"]
                for i in ranked
                if i["category"] in {"RISK", "OPPORTUNITY", "CHANGE"}
            ],
            "blockerIds": blockers,
            "relatedIndexIds": sorted(
                {e["indexId"] for e in market_evidence}
                | ({normalized["index_id"]} if normalized["index_id"] else set())
            ),
            "summary": " ".join(direction_facts)
            or "当前证据尚不足以评价完整研究机会。",
            "nextStep": "先查看当前条件和相关指数，未满足规则继续观察；必要缺口另见研究限制。",
            "nextAction": action(
                "VIEW_DIRECTION", "查看方向关联事项", target="current-direction"
            ),
        },
        "dataStatus": {
            "itemIds": blockers,
            "groups": _groups([i for i in ranked if i["id"] in blockers]),
            "adminAction": action(
                "VIEW_OPERATIONS", "管理员查看运行详情", href="/operations"
            )
            if can_manage_operations
            else None,
            "collectedAt": collection.get("collectedAt"),
            "nextCheckAt": collection.get("nextCheckAt"),
            "collectionState": collection.get("collectionState", "NOT_COLLECTED"),
            "sourceResults": sources,
            "methodVersions": [
                VERSION,
                model.get("methodVersion", "opportunity-radar-v2"),
                decision.get("methodVersion", "opportunity-decision-v1"),
            ],
        },
    }
