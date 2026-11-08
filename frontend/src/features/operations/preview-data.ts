import type { FundNavHistory } from "../../api/funds";
import type { PreviewFund } from "../../api/research-preview";
import { navPercentile } from "../fund-screener/nav-percentile";

const reasonLabels: Record<string, string> = {
  FROZEN_NAV_HISTORY_UNAVAILABLE: "冻结材料未保存净值历史",
  FROZEN_HISTORY_UNAVAILABLE: "冻结材料未保存净值历史",
  FROZEN_ANCHOR_UNAVAILABLE: "冻结材料未保存价格依据",
  FROZEN_PRICE_ANCHOR_MISSING: "冻结材料未保存价格依据",
  FROZEN_NAV_SERIES_UNAVAILABLE: "冻结材料未保存净值历史",
};
export function previewMissingReason(reason: string) {
  return (
    reasonLabels[reason] ??
    (reason.includes("HISTORY") || reason.includes("SERIES")
      ? "冻结净值历史未通过完整性核验"
      : reason.includes("ANCHOR")
        ? "原研究价格依据不完整"
        : "原研究证据待核验")
  );
}

export function frozenNavHistory(
  fund: PreviewFund,
  validatedAt: string,
): FundNavHistory | null {
  if (!fund.fundCode || fund.analysis?.seriesKind !== "UNIT_NAV") return null;
  const cutoff = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
  }).format(new Date(validatedAt));
  const points = fund.analysis.points
    .filter(
      (point) =>
        /^\d{4}-\d{2}-\d{2}$/.test(point.date) &&
        point.date <= cutoff &&
        Number.isFinite(point.close) &&
        point.close > 0,
    )
    .map((point) => ({ date: point.date, unitNav: String(point.close) }));
  if (!points.length) return null;
  const values = points.map((point) => Number(point.unitNav));
  return {
    methodVersion: "fund-unit-nav-trend-v1",
    fundCode: fund.fundCode,
    fundName: fund.label,
    period: "all",
    priceBasis: "UNIT_NAV",
    unit: fund.anchor?.unit ?? "CNY/份",
    state: "READY",
    asOf: points.at(-1)!.date,
    retrievedAt: null,
    sourceUri: fund.anchor?.sourceUri ?? fund.analysis.source ?? null,
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
