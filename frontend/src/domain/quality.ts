import type { QualityCode } from "../api/generated/types";

export const qualityLabels: Record<QualityCode, string> = {
  FRESH: "新鲜",
  ESTIMATED: "估值",
  STALE: "过期",
  MISSING: "缺失",
};

export const qualityDescriptions: Record<QualityCode, string> = {
  FRESH: "数据仍在约定时效窗口内",
  ESTIMATED: "基于最近披露与行情估算，并非基金最终净值",
  STALE: "数据已超出时效窗口，使用前需要复核",
  MISSING: "关键数据尚未获取，本结论不得作为事实使用",
};
