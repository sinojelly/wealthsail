"""Read-only decision context with explicit market selection and actor scope."""

from backend.dependencies import now_shanghai
from backend.common.timing import trace_stage
from backend.errors import NotFoundError, ValidationError_
from backend.investment import repository
from backend.investment.decision import decision_context
from backend.investment.decision_repository import resolve_instrument
from backend.investment.service import (
    analysis,
    market_context,
    actionable_plan_for_analysis,
)
from backend.macro import market_repository
from backend.macro.direction_models import direction_model
from backend.macro.daily_repository import read_daily_research
from backend.config import get_settings


def _complete_trend_window(result, points):
    window = result.get("trendWindow") or {}
    if result.get("trendQuality") != "READY" or result.get("state") not in {
        "CONFLICT",
        "PARTIAL",
        "INSUFFICIENT",
        "READY",
        "WEAK",
        "MIXED",
        "STRONG",
        "NEUTRAL",
    }:
        return False
    count = window.get("sampleCount")
    start, end = (
        market_repository._day(window.get("start")),
        market_repository._day(window.get("end")),
    )
    if (
        type(count) is not int
        or count < 200
        or start is None
        or end is None
        or start > end
        or end != market_repository._day(result.get("asOf"))
        or not points
        or market_repository._day(points[-1].get("date")) != end
    ):
        return False
    selected = [
        point
        for point in points
        if (day := market_repository._day(point.get("date"))) is not None
        and start <= day <= end
    ]
    days = [market_repository._day(point["date"]) for point in selected]
    return (
        len(days) == count
        and days == sorted(set(days))
        and days[0] == start
        and days[-1] == end
        and all(
            market_repository._number(point.get("close")) is not None
            and market_repository._number(point["close"]) > 0
            for point in selected
        )
    )


def market_evidence(index, result, *, now=None):
    number = market_repository._number
    indicators = result.get("indicators") or {}
    points = result.get("points") or []
    ma, momentum = number(indicators.get("ma200")), number(indicators.get("momentum63"))
    close = number(points[-1].get("close")) if points else None
    usable = (
        result.get("state") in {"STRONG", "MIXED", "NEUTRAL", "WEAK", "READY"}
        and result.get("dataQuality", "READY") == "READY"
    )
    trend_usable = usable or _complete_trend_window(result, points)
    trend_ready = (
        trend_usable
        and ma is not None
        and ma > 0
        and momentum is not None
        and close is not None
        and close > 0
    )
    valuation = result.get("valuation") or {"state": "UNKNOWN"}
    value_ready = valuation.get("state") == "READY" and all(
        (valuation.get(metric) or {}).get("state") == "READY"
        and (valuation.get(metric) or {}).get("points")
        for metric in ("pe", "pb")
    )
    if not usable or not value_ready:
        pe = valuation.get("pe") or {}
        partial_pe = (
            valuation.get("state") == "PARTIAL"
            and pe.get("state") in {"READY", "INSUFFICIENT"}
            and bool(pe.get("points"))
            and all(
                number(point.get("value")) is not None and number(point["value"]) > 0
                for point in pe["points"]
            )
        )
        valuation = {
            **valuation,
            "state": ("PARTIAL" if partial_pe else "UNKNOWN")
            if trend_usable
            else result.get("state", "UNKNOWN"),
        }
    volatility = number(indicators.get("volatility20")) if trend_usable else None
    if volatility is not None and volatility < 0:
        volatility = None
    maximum, drawdown = None, None
    for point in points if usable else []:
        value = number(point.get("close"))
        if value is None or value <= 0:
            maximum, drawdown = None, None
            break
        maximum = max(maximum or value, value)
        drawdown = min(drawdown or 0, value / maximum - 1)
    missing = ["企业盈利证据"]
    if not trend_ready:
        missing.append("当前指数趋势")
    if valuation.get("state") != "READY":
        missing.append("当前指数估值")
    risk_ready = volatility is not None and drawdown is not None
    if not risk_ready:
        missing.append("指数波动与回撤")
    return {
        "indexId": index["id"],
        "label": index.get("name", index["id"]),
        "state": "READY" if trend_ready else result.get("state", "NOT_COLLECTED"),
        "asOf": result.get("asOf"),
        "sourceUri": result.get("source"),
        "retrievedAt": result.get("retrievedAt"),
        "publishedAt": result.get("publishedAt"),
        "timeBasis": result.get("timeBasis"),
        "collection": result.get("indexCollection"),
        "historyQuality": result.get("dataQuality", "UNKNOWN"),
        "trendWindow": result.get("trendWindow"),
        "valuation": valuation,
        "trend": {
            "state": "READY" if trend_ready else "UNKNOWN",
            "ma200": ma if trend_ready else None,
            "momentum63": momentum if trend_ready else None,
            "distanceMA200": close / ma - 1 if trend_ready else None,
        },
        "risk": {
            "state": "READY"
            if risk_ready
            else "PARTIAL"
            if volatility is not None
            else "UNKNOWN",
            "volatility20": volatility,
            "maxDrawdown": drawdown,
            "summary": "历史收盘价格回撤，未包含分红和投资者现金流。",
        },
        "earnings": {"state": "UNKNOWN", "summary": "尚无经核实的企业盈利证据。"},
        "missingEvidence": missing,
        "actionableZones": actionable_plan_for_analysis(
            result, "index", index["id"], label=index.get("name"), now=now
        ),
    }


async def get_context(
    conn,
    tenant_id,
    user_id,
    *,
    direction_id=None,
    index_id=None,
    instrument_id=None,
    fund_code=None,
    fund_market=None,
    months=36,
    now=None,
    can_manage_operations=False,
    theme_code=None,
):
    now = now or now_shanghai()
    direction = {
        "id": "other:unconfirmed",
        "market_id": "other",
        "label": "资产归属待核查",
    }
    if direction_id:
        row = await conn.fetchrow(
            "SELECT * FROM reference.research_directions WHERE id=$1", direction_id
        )
        if row is None:
            raise NotFoundError("研究方向不存在")
        direction = dict(row)
    selected = []
    if direction_id:
        selected = [dict(row) for row in await repository.indices(conn, direction_id)]
    if index_id:
        row = await conn.fetchrow(
            "SELECT * FROM reference.research_indices WHERE id=$1", index_id
        )
        if row is None:
            raise NotFoundError("研究指数不存在")
        if direction_id and index_id not in {i["id"] for i in selected}:
            raise ValidationError_("指数不属于所选研究方向")
        selected = [dict(row)]
    if instrument_id and fund_code:
        same_identity = await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM reference.research_fund_catalog
            WHERE instrument_id::text=$1 AND fund_code=$2 AND ($3::text IS NULL OR market=$3))""",
            str(instrument_id),
            fund_code,
            fund_market,
        )
        if same_identity is not True:
            raise ValidationError_("基金代码、市场与标的身份不一致")
    identity = await resolve_instrument(conn, instrument_id, fund_code, fund_market)
    selection_state = "READY" if not (instrument_id or fund_code) else "UNKNOWN"
    evaluated_index_id = index_id
    if identity and (direction_id or index_id):
        mappings = await conn.fetch(
            """SELECT DISTINCT f.index_id,i.name,
            ARRAY(SELECT d.direction_id FROM reference.direction_index_links d WHERE d.index_id=f.index_id) AS direction_ids
            FROM reference.fund_index_links f JOIN reference.research_indices i ON i.id=f.index_id
            WHERE f.instrument_id::text=$1 AND f.verified_at<=$2 AND i.verified_at<=$2
            AND f.effective_from<=($2 AT TIME ZONE 'Asia/Shanghai')::date
            AND (f.effective_to IS NULL OR f.effective_to>=($2 AT TIME ZONE 'Asia/Shanghai')::date)""",
            str(identity),
            now,
        )
        if len(mappings) == 1:
            mapping = mappings[0]
            if (
                index_id
                and mapping["index_id"] != index_id
                or direction_id
                and direction_id not in mapping["direction_ids"]
            ):
                raise ValidationError_("所选基金与研究方向/指数的已核查映射不一致")
            selection_state = "READY"
            evaluated_index_id = mapping["index_id"]
            selected = [
                next(
                    (i for i in selected if i["id"] == evaluated_index_id),
                    {
                        "id": evaluated_index_id,
                        "name": mapping.get("name", evaluated_index_id),
                    },
                )
            ]
    # A fund code alone cannot establish a market or a verified index mapping.
    with trace_stage("context.market_context"):
        base = await market_context(conn, months, now=now)
    with trace_stage("context.market_series"):
        permissions = await market_repository.read_permissions(conn)
        collected = await market_repository.read_collection(conn, now)
        series = market_repository.series_context(
            await market_repository.read_series(conn, now),
            now,
            permissions=permissions,
            source_results=collected.get("sourceResults", {}),
        )
    from backend.macro.alpha_repository import read_alpha_public, merge_alpha_series
    from backend.investment.theme_graph import read_themes, cached_nav_analysis

    with trace_stage("context.alpha_public"):
        alpha_public = await read_alpha_public(conn, now)
        series = merge_alpha_series(series, alpha_public, now)
    with trace_stage("context.themes"):
        themes = await read_themes(conn, now, theme_code)
    evidence = []
    for index in selected[:5]:
        with trace_stage("context.index_analysis"):
            result = await analysis(conn, "index", index["id"])
        retrieved = market_repository._stamp(result.get("retrievedAt"))
        if (
            retrieved
            and retrieved > now
            or any(
                (
                    market_repository._day(point.get("date")) is None
                    or market_repository._day(point.get("date")) > now.date()
                )
                for point in result.get("points", [])
            )
            or (
                result.get("asOf")
                and (
                    market_repository._day(result["asOf"]) is None
                    or market_repository._day(result["asOf"]) > now.date()
                )
            )
        ):
            result = {"state": "NOT_COLLECTED"}
        evidence.append(market_evidence(index, result, now=now))
    model = direction_model(direction, base, series, evidence)
    if selection_state == "UNKNOWN":
        model["state"] = "PARTIAL"
        model["qualificationState"] = "INCOMPLETE"
        model.setdefault("requiredEvidence", []).append("FUND_MAPPING")
        model.setdefault("missingRequiredEvidence", []).insert(0, "FUND_MAPPING")
        model["missingEvidence"].insert(0, "基金指数/方向归属尚待唯一核查")
    with trace_stage("context.personal"):
        personal = await decision_context(
            conn,
            tenant_id,
            user_id,
            direction_id=direction_id,
            index_id=index_id,
            evaluated_index_id=evaluated_index_id,
            instrument_id=instrument_id,
            fund_code=fund_code,
            fund_market=fund_market,
            market_evidence=evidence,
            direction_model=model,
            now=now,
        )
    with trace_stage("context.daily_research"):
        research_settings = get_settings()
        daily = await read_daily_research(
            conn,
            now,
            enabled=research_settings.daily_research_enabled,
            start_window_minutes=research_settings.daily_research_start_window_minutes,
            schedule_mode=getattr(research_settings, "daily_research_schedule_mode", "DAILY"),
            schedule_time=getattr(research_settings, "daily_research_schedule_time", "07:00"),
            histories=alpha_public.get("fundHistories"),
        )
    from backend.investment.nav_change import report_with_nav_changes

    with trace_stage("context.nav_projection"):
        daily["report"] = report_with_nav_changes(
            daily.get("report"), alpha_public.get("fundHistories"), now
        )
    if fund_code and (fund_market == "FUND_CN" or identity is not None):
        from backend.investment.decision import evaluate

        with trace_stage("context.selected_fund"):
            nav = await cached_nav_analysis(
                conn,
                fund_code,
                now,
                subject_kind="fund" if identity else "nav",
                subject_id=str(identity) if identity else fund_code,
            )
        if nav:
            portfolio = personal.get("portfolio") or {}
            personal["decision"] = evaluate(
                held=portfolio.get("heldSelected"),
                portfolio=portfolio,
                budget_violation=portfolio.get("budgetViolation") is True,
                direction_model=model,
                actionable_zones=nav["actionableZones"],
            )
    if theme_code and (personal.get("decision") or {}).get("actionableZones"):
        personal["decision"]["actionableZones"]["themeCode"] = theme_code
    plans = [row["actionableZones"] for row in evidence]
    selected_plan = (personal.get("decision") or {}).get("actionableZones")
    if selected_plan and not any(
        plan["subjectKind"] == selected_plan.get("subjectKind")
        and plan["subjectId"] == selected_plan.get("subjectId")
        for plan in plans
    ):
        plans.append(selected_plan)
    from backend.investment.sell_risk import sell_risk_alerts

    with trace_stage("context.sell_risk"):
        sell_alerts = await sell_risk_alerts(conn, tenant_id, user_id, now, daily=daily)
    return {
        **base,
        **personal,
        "direction": {
            "id": direction["id"],
            "marketId": direction["market_id"],
            "label": direction["label"],
        },
        "series": series,
        "marketEvidence": evidence,
        "directionModel": model,
        "marketCollection": collected,
        "selectionState": selection_state,
        "dailyResearch": daily,
        "sellRiskAlerts": sell_alerts,
        "actionableZones": plans,
        "alphaPublic": {
            key: value
            for key, value in alpha_public.items()
            if key not in {"fundHistories"}
        },
        "themes": themes,
    }
