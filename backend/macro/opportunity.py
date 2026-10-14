"""Prioritized decision questions: observed changes, mechanisms, gates and holdings."""

from datetime import datetime, timezone
from backend.macro.direction_models import direction_model, profile, VERSION, _usable
from backend.macro.market_repository import _number


def _change(
    identity, priority, kind, summary, impact, evidence, directions, time_scale
):
    return {
        "id": identity,
        "priority": priority,
        "kind": kind,
        "summary": summary,
        "impact": impact,
        "evidenceIds": evidence,
        "relatedDirectionIds": directions,
        "timeScale": time_scale,
    }


def build_radar(
    context,
    series,
    market_evidence,
    direction,
    decision,
    portfolio,
    *,
    now=None,
    model=None,
):
    now = now or datetime.now(timezone.utc)
    model = (
        model
        if model is not None
        else direction_model(direction, context, series, market_evidence)
    )
    required, _, market_label = profile(direction)
    direction_id = direction.get("id", "")
    changes = []
    for item in series:
        code = item["code"]
        if code not in required or not _usable(item):
            continue
        delta = _number(item.get("changes", {}).get("change5Samples"))
        if (
            code in {"CN_T10", "US_T10_NOMINAL", "US_T10_REAL", "DR007"}
            and delta is not None
            and abs(delta) >= 20
            and item.get("changes", {}).get("changeUnit") == "bp"
        ):
            direction_text = (
                "黄金机会成本"
                if code == "US_T10_REAL" and direction_id.startswith("gold:")
                else "折现与融资成本"
                if not direction_id.startswith("bond:")
                else "债券价格与久期敏感度"
            )
            kind = "RISK" if delta > 0 else "SUPPORT"
            priority = "CRITICAL" if abs(delta) >= 40 else "ATTENTION"
            changes.append(
                _change(
                    "rate:" + code,
                    priority,
                    kind,
                    f"{item.get('label', code)}近5个观察样本{delta:+.2f}基点。",
                    f"{market_label}需复核{direction_text}；利率变化的原因与其他资产证据可能产生相反作用。",
                    [code],
                    [direction_id],
                    "每日发布后",
                )
            )
        elif code in {"M1_YOY", "TSF_STOCK_YOY"}:
            delta = _number(item.get("changes", {}).get("periodChange"))
            if (
                delta is not None
                and delta != 0
                and item.get("changes", {}).get("changeUnit") == "pp"
            ):
                changes.append(
                    _change(
                        "credit:" + code,
                        "ATTENTION",
                        "SUPPORT" if delta > 0 else "RISK",
                        f"{item['label']}较前月{delta:+.2f}个百分点。",
                        f"{market_label}核查信用传导、需求与融资结构，增速变化不直接等于盈利变化。",
                        [code],
                        [direction_id],
                        "月度公布后",
                    )
                )
    for index, change in enumerate(context.get("research", {}).get("changes", [])):
        code = change["code"]
        item = next((i for i in context.get("items", []) if i["code"] == code), None)
        if code not in required or not item or not _usable(item):
            continue
        crossing = "跨越" in change["summary"]
        changes.append(
            _change(
                f"macro:{code}:{index}",
                "ATTENTION" if crossing else "INFO",
                "OBSERVATION",
                change["summary"],
                f"{market_label}需要后续经营、估值与风险证据确认，月度观察不单独决定盘中行动。",
                [code],
                [direction_id],
                "月度公布后",
            )
        )
    for event in context.get("events", []):
        if direction_id in event.get("directionIds", []) and event.get("publishedAt"):
            changes.append(
                _change(
                    "event:" + event["id"],
                    "INFO",
                    "OBSERVATION",
                    event["title"],
                    "官方事件已发生，需核对原文、传导渠道及价格反应。",
                    [event["id"]],
                    [direction_id],
                    "事件发布后",
                )
            )
    if model["missingEvidence"]:
        changes.append(
            _change(
                "required-evidence",
                "CRITICAL",
                "GAP",
                "当前方向仍有关键证据缺口。",
                f"{market_label}尚待核查："
                + "、".join(model["missingEvidence"][:4])
                + "；不能据此认证建仓条件。",
                [],
                [direction_id],
                "当前研究",
            )
        )
    if decision.get("state") == "POSITION_REVIEW":
        changes.append(
            _change(
                "personal-budget",
                "CRITICAL",
                "RISK",
                "已确认个人风险条件需要复核。",
                decision.get("summary", "持仓预算条件超限。"),
                ["portfolio"],
                [direction_id],
                "持仓/预算变化",
            )
        )
    rank = {"CRITICAL": 3, "ATTENTION": 2, "INFO": 1}
    kind_rank = {"RISK": 4, "SUPPORT": 3, "GAP": 2, "OBSERVATION": 1}
    changes.sort(
        key=lambda c: (
            c["id"] == "personal-budget",
            rank[c["priority"]],
            kind_rank[c["kind"]],
        ),
        reverse=True,
    )
    headline = (
        changes[0]
        if changes
        else _change(
            "no-change",
            "INFO",
            "OBSERVATION",
            "暂无已确认的重要条件变化。",
            "继续核查当前方向的指数、基金和个人风险证据。",
            [],
            [direction_id],
            "当前研究",
        )
    )
    impact_summary = (
        model["counterFactors"]
        or model["supports"]
        or [{"summary": headline["impact"]}]
    )[0]["summary"]
    questions = [
        {
            "id": "CHANGE",
            "label": "发生了什么重要变化",
            "state": headline["kind"],
            "summary": headline["summary"],
            "critical": False,
        },
        {
            "id": "IMPACT",
            "label": "对当前方向有什么影响",
            "state": model["state"],
            "summary": headline["impact"]
            if headline["priority"] == "CRITICAL"
            else impact_summary,
            "critical": headline["priority"] == "CRITICAL",
        },
        {
            "id": "QUALIFICATION",
            "label": "当前研究条件是否满足",
            "state": decision.get("state", "WAIT"),
            "summary": decision.get("summary", "尚未完成指数/基金和策略条件核验。"),
            "critical": False,
        },
        {
            "id": "PERSONAL",
            "label": "结合持仓下一步看什么",
            "state": portfolio.get("state", "UNKNOWN"),
            "summary": portfolio.get("summary", "个人持仓未核查，不能推断仓位动作。"),
            "critical": decision.get("state") == "POSITION_REVIEW",
        },
    ]
    rates = [i for i in series if i["code"] in required]
    section_definitions = [
        (
            "MACRO",
            "宏观周期",
            [i["code"] for i in context.get("items", []) if i["code"] in required],
            "增长/价格观察须结合盈利与需求。",
            [],
        ),
        (
            "INTEREST_CREDIT",
            "利率与信用",
            [i["code"] for i in rates if i.get("state") == "READY"],
            "利率变化看基点；信用看口径和融资结构。",
            [i["label"] for i in rates if i.get("state") != "READY"],
        ),
        (
            "MARKET",
            "指数与市场",
            [e["indexId"] for e in market_evidence if e.get("state") == "READY"],
            "估值、长期趋势、波动和回撤分别核查。",
            list(
                dict.fromkeys(
                    m for e in market_evidence for m in e.get("missingEvidence", [])
                )
            )
            or (["当前指数市场证据"] if not market_evidence else []),
        ),
        (
            "GLOBAL",
            "全球事件",
            [
                e["id"]
                for e in context.get("events", [])
                if direction_id in e.get("directionIds", [])
            ],
            "官方事件与盘前反应只作传导线索。",
            ["美元/汇率与能源冲击", "消息预期与价格反应"],
        ),
    ]
    sections = [
        {
            "id": identity,
            "label": label,
            "state": "PARTIAL" if refs else "UNKNOWN",
            "summary": summary,
            "criticalImpact": headline["impact"]
            if headline["priority"] == "CRITICAL"
            and (
                (
                    identity == "INTEREST_CREDIT"
                    and headline["id"].startswith(("rate:", "credit:", "required"))
                )
                or identity == "MARKET"
                and headline["id"] == "personal-budget"
            )
            else None,
            "missingEvidence": missing,
            "evidenceIds": refs,
        }
        for identity, label, refs, summary, missing in section_definitions
    ]
    return {
        "methodVersion": VERSION,
        "asOf": now,
        "headline": headline,
        "questions": questions,
        "changes": changes,
        "sections": sections,
    }
