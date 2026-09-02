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

/** Split pre-market variants always render their canonical labels. */
export function reportDisplayMeta(report: ReportLike): {
  title: string;
  subtitle: string;
} {
  const meta = report.variant ? REPORT_VARIANT_META[report.variant] : undefined;
  return meta ?? { title: report.title, subtitle: report.subtitle };
}
