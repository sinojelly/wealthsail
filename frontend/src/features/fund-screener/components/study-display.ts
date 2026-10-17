export const studyStates: Record<string, string> = {
  OBSERVING: "观察中",
  STRENGTHENED: "假设加强",
  WEAKENED: "假设减弱",
  TRIGGERED: "预设条件触发",
  INVALIDATED: "预设失效条件触发",
  ENDED: "观察已结束",
  TRUE: "条件成立",
  FALSE: "条件不成立",
  UNKNOWN: "无法核验",
  NOT_DUE: "观察期已结束",
  MANUAL_REVIEW_REQUIRED: "需人工复盘",
  READY: "证据可核对",
};
export const studyActions = {
  WATCH: "观察",
  PLAN_BUY: "计划买入",
  PLAN_SELL: "计划卖出",
  HOLD: "继续持有",
  IGNORE: "暂不采取行动",
};
export const studyVerdicts = {
  SUPPORTED: "得到支持",
  MIXED: "部分支持",
  REFUTED: "被证伪",
  INCONCLUSIVE: "无法判断",
};
export const studyErrors = {
  FACT: "事实",
  TRANSMISSION: "传导",
  EXPECTATIONS: "预期",
  INSTRUMENT: "工具",
  TIMING: "时机",
  RISK: "风险",
  DISCIPLINE: "纪律",
};
export const studyCatalog = [
  ["PMI", "制造业PMI", "指数点"],
  ["CPI_YOY", "CPI同比", "%"],
  ["PPI_YOY", "PPI同比", "%"],
  ["M2_YOY", "M2同比", "%"],
  ["US_T10_NOMINAL", "美国10年名义利率", "%"],
  ["US_T10_REAL", "美国10年实际利率", "%"],
  ["DCOILBRENTEU", "布伦特现货", "USD/barrel"],
  ["GASDESW", "美国零售柴油", "USD/gallon"],
  ["INDEX_MA200_GAP", "指数偏离MA200", "%"],
  ["INDEX_MOMENTUM63", "指数63期动量", "%"],
  ["INDEX_PE", "指数滚动PE", "倍"],
] as const;
export const studyMetricLabel = (code: string) =>
  studyCatalog.find(([id]) => id === code)?.[1] ?? "其他待核查指标";
export const studySubjectLabel = (code?: string | null) =>
  code
    ? ((
        { hs300: "沪深300", a500: "中证A500", zz500: "中证500" } as Record<
          string,
          string
        >
      )[code] ?? "关联标的待核查")
    : "";
export const studyOperatorLabel = (code: string) =>
  (
    ({ GT: "大于", GTE: "不低于", LT: "小于", LTE: "不高于" }) as Record<
      string,
      string
    >
  )[code] ?? "比较方式待核查";
export const studyEffectLabel = (code: string) =>
  (
    ({
      STRENGTHEN: "研究依据增强",
      WEAKEN: "研究依据减弱",
      TRIGGER: "预设条件触发",
      INVALIDATE: "预设失效条件触发",
    }) as Record<string, string>
  )[code] ?? "条件影响待核查";
export const studyConditionLabel = (condition: StudyCondition) =>
  [
    studyMetricLabel(condition.metric),
    studySubjectLabel(condition.subjectKey),
    studyOperatorLabel(condition.operator),
    condition.threshold,
    condition.unit,
  ]
    .filter((part) => part != null && part !== "")
    .join(" ") + ` · ${studyEffectLabel(condition.effect)}`;
import type { StudyCondition } from "../../../api/study-api";
