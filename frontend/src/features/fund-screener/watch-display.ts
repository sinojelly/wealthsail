import type { StudyPriceWatch } from "../../api/study-api";

export const watchStateLabels = {
  ENTRY_ZONE: "🎯已入买入击球区",
  HOLDING: "📈持仓观察中",
  STOP_LOSS: "⚠️触及止损",
  TAKE_PROFIT: "🎉触及止盈",
  OBSERVING: "区间观察中",
  UNKNOWN: "行情待更新",
};
export const currentFundEstimate = (watch: StudyPriceWatch) =>
  watch.currentEstimate === undefined
    ? watch.estimate1430
    : watch.currentEstimate;
