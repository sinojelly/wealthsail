import type { QualityCode } from "../../api/types";

export interface EvidenceRecord {
  id: string;
  type: string;
  title: string;
  content: string;
  source: string;
  sourceUrl?: string;
  asOf: string;
  claimType: string;
  sourceSnapshotId?: string;
  quality: QualityCode;
  hash?: string;
}

export type ReportLike = {
  session: string;
  variant?: string | null;
  title: string;
  subtitle: string;
};

const REPORT_VARIANT_META: Record<string, { title: string; subtitle: string }> = {
  PRE_MARKET_MACRO: { title: "盘前简报·宏观", subtitle: "隔夜市场与今日全球重点" },
  PRE_MARKET_PERSONALIZED: {
    title: "盘前简报·个性化",
    subtitle: "持仓、关注标的与今日操作提醒",
  },
};

/** Canonical session labels shown even before any report exists for a slot. */
export const REPORT_SESSION_META: Record<
  "PRE_MARKET" | "INTRADAY" | "FUND_1430" | "POST_MARKET",
  { title: string; subtitle: string }
> = {
  PRE_MARKET: { title: "盘前简报", subtitle: "隔夜市场、今日事件与持仓风险" },
  INTRADAY: { title: "盘中跟踪", subtitle: "持仓异动与风险预算触点" },
  FUND_1430: { title: "基金 2:30 报告", subtitle: "场外基金估值、覆盖率与截止时间" },
  POST_MARKET: { title: "盘后交互复盘", subtitle: "计划—决策—执行闭环与证据回看" },
};

/** Fixed display order for the four report slots. */
export const REPORT_SESSION_ORDER = [
  "PRE_MARKET",
  "INTRADAY",
  "FUND_1430",
  "POST_MARKET",
] as const;

/** Split pre-market variants always render their canonical labels. */
export function reportDisplayMeta(report: ReportLike): {
  title: string;
  subtitle: string;
} {
  const meta = report.variant ? REPORT_VARIANT_META[report.variant] : undefined;
  return meta ?? { title: report.title, subtitle: report.subtitle };
}
