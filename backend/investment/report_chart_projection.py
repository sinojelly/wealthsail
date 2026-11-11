"""Copy-on-write chart compaction for the opt-in opportunity response only."""

_PLAN_ARRAYS = (
    "actionablePlans", "actionableZones", "themeActionableZones",
    "discoveryActionableZones",
)


def _compact_plan(plan, *, mention=False):
    if not isinstance(plan, dict):
        return plan
    nested = plan.get("actionableZones")
    outer_series = plan.get("series")
    if not (
        isinstance(nested, dict)
        and isinstance(outer_series, dict)
        and isinstance(outer_series.get("points"), list)
        and outer_series == nested.get("series")
    ):
        # The same fund can have distinct observation clocks or curves. Neither
        # fund identity nor equal latest prices prove those charts duplicate.
        return plan
    if mention:
        # Mention cards receive actionableZones; other cards receive the plan.
        return {key: value for key, value in plan.items() if key != "series"}
    return {
        **plan,
        "actionableZones": {
            key: value for key, value in nested.items() if key != "series"
        },
    }


def _map_rows(container, field, transform):
    rows = container.get(field)
    if not isinstance(rows, list):
        return container
    updated = [transform(row) for row in rows]
    if all(old is new for old, new in zip(rows, updated)):
        return container
    return {**container, field: updated}


def _compact_insight(insight):
    if not isinstance(insight, dict):
        return insight
    for field in ("plans", "fundPlans", "quantitativeEvidence"):
        insight = _map_rows(insight, field, _compact_plan)
    return insight


def _compact_report(report):
    for field in _PLAN_ARRAYS:
        report = _map_rows(report, field, _compact_plan)
    for field in ("valueDirections", "researchTopics", "topicCatalog"):
        report = _map_rows(report, field, _compact_insight)
    report = _map_rows(
        report, "mentionedFunds", lambda plan: _compact_plan(plan, mention=True)
    )
    scan = report.get("quantitativeScan")
    if isinstance(scan, dict):
        updated = _map_rows(scan, "items", _compact_plan)
        if updated is not scan:
            report = {**report, "quantitativeScan": updated}
    return report


def compact_context_charts(context):
    """Keep every consumer's full chart, prices and insight in an API copy.

    Only identical, unused series duplicates are omitted. Retained series and
    metadata are shared for serialization; no stored or cached object is edited.
    """
    if not isinstance(context, dict):
        return context
    daily = context.get("dailyResearch")
    report = daily.get("report") if isinstance(daily, dict) else None
    if not isinstance(report, dict):
        return context
    updated = _compact_report(report)
    if updated is report:
        return context
    return {**context, "dailyResearch": {**daily, "report": updated}}
