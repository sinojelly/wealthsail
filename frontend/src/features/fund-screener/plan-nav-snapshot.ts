import type { ActionableZoneResult } from "../../api/investment";
import type { FundNavHistory } from "../../api/funds";
import { navPercentile } from "./nav-percentile";

export function planNavSnapshot(
  plan: ActionableZoneResult,
  name: string,
): FundNavHistory | undefined {
  const series = plan.series;
  const code = plan.fundCode ?? plan.subjectId;
  if (
    !series?.points.length ||
    series.fundCode !== code ||
    series.priceBasis !== "UNIT_NAV" ||
    !["CNY/份", "元/份", "CNY/unit", "CNY"].includes(series.unit)
  )
    return undefined;
  const points = series.points
    .filter(
      (point) =>
        /^\d{4}-\d{2}-\d{2}$/.test(point.date) &&
        Number.isFinite(Number(point.value)) &&
        Number(point.value) > 0 &&
        (!plan.asOf || point.date <= plan.asOf.slice(0, 10)),
    )
    .map((point) => ({ date: point.date, unitNav: String(point.value) }))
    .sort((a, b) => a.date.localeCompare(b.date));
  if (!points.length) return undefined;
  const values = points.map((point) => Number(point.unitNav));
  return {
    methodVersion: "fund-unit-nav-trend-v1",
    fundCode: code,
    fundName: name,
    period: "2y",
    priceBasis: "UNIT_NAV",
    unit: series.unit,
    state: series.state === "READY" ? "READY" : "PARTIAL",
    asOf: points.at(-1)!.date,
    retrievedAt: series.retrievedAt,
    sourceUri: series.sourceUri,
    points,
    coverage: {
      startDate: points[0].date,
      endDate: points.at(-1)!.date,
      fullHistory: false,
      requestedStartDate: null,
      sampleCount: points.length,
    },
    summary: {
      minimumNav: String(Math.min(...values)),
      maximumNav: String(Math.max(...values)),
      currentNav: points.at(-1)!.unitNav,
      percentilePct: navPercentile(values),
      percentileMethod: "EMPIRICAL_MIDRANK_V1",
    },
    refresh: { state: "IDLE", runId: null, pollAfterMs: null },
  };
}
