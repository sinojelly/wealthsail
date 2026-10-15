import type {
  OpportunityContext,
  ResearchAction,
  ResearchIssue,
  ResearchTone,
} from "../../../api/investment";

const labels: Record<string, string> = {
  UNKNOWN: "尚未评估",
  INSUFFICIENT: "证据不足",
  INCOMPLETE: "证据不完整",
  READY: "条件齐备",
  BLOCKED: "条件未满足",
  PARTIAL: "部分覆盖",
  PRIORITY_RESEARCH: "值得优先研究",
  WAIT_CONDITIONS: "等待条件",
  RISK_OBSERVATION: "风险观察",
  POSITION_REVIEW: "持仓风险复核",
  WAIT: "等待完整条件",
  ENTRY_CANDIDATE: "建仓研究候选",
  HOLD_REVIEW: "持仓观察",
  VERIFIED: "证据已核验",
  NOT_COLLECTED: "尚未采集",
  PERMISSION_REQUIRED: "需要来源授权",
  STALE: "旧期证据",
  CONFLICT: "证据冲突",
  UNAVAILABLE: "证据不可用",
  SUCCESS: "采集完成",
  COMPLETED: "处理完成",
  FAILED: "采集失败",
  CANCELLED: "处理已取消",
  CLAIMED: "已登记执行租约",
  RUNNING: "已登记执行租约",
  SKIPPED: "本次跳过",
  BREACH: "已确认预算超限",
  CHECKED: "已完成预算核查",
  OBSERVED: "事实观察",
  NO_CONFIRMED_CHANGE: "暂无已确认变化",
  HELD: "已持有",
  NOT_HELD: "未持有",
  UNVERIFIED: "待核验",
  FINISHED: "最近处理已结束",
};
export const stateLabel = (state: string) => labels[state] ?? "待核查";
export const categoryLabels: Record<ResearchIssue["category"], string> = {
  RISK: "风险复核",
  OPPORTUNITY: "研究线索",
  CHANGE: "重要变化",
  DATA_GAP: "研究受限",
  SYSTEM: "数据状态",
};
export const ownerLabels: Record<string, string> = {
  MACRO_WORKER: "宏观采集任务",
  MARKET_WORKER: "市场采集任务",
  WORKER: "后台采集任务",
  RESEARCH: "研究服务",
  SYSTEM: "系统",
  ADMIN: "管理员",
  USER: "用户",
  DATA_GOVERNANCE: "数据核验服务",
  PORTFOLIO: "资产服务",
  INVESTMENT_RESEARCH: "研究服务",
  RESEARCH_SERVICE: "研究服务",
  RESEARCH_DATA: "研究数据服务",
};
export const sourceLabels: Record<string, string> = {
  PBOC: "中国人民银行",
  US_TREASURY: "美国财政部",
  TREASURY_NOMINAL: "美国财政部名义利率",
  TREASURY_REAL: "美国财政部实际利率",
  NBS: "国家统计局",
  PRE_MARKET: "已存盘前事实",
  CHINABOND: "中债来源",
  CFETS: "外汇交易中心来源",
  UST_NOMINAL: "美国财政部名义利率",
  UST_REAL: "美国财政部实际利率",
  PBOC_M1: "中国人民银行货币供应",
  PBOC_TSF: "中国人民银行社会融资",
  PBOC_LOANS: "中国人民银行贷款数据",
};
export const dataIssue = (issue: ResearchIssue) =>
  ["DATA_GAP", "SYSTEM"].includes(issue.category);
export const evidenceLabels: Record<string, string> = {
  INDEX_TREND: "当前指数趋势",
  INDEX_VALUATION: "当前指数估值",
  INDEX_EARNINGS: "企业盈利证据",
  portfolio: "个人组合证据",
  "confirmed-policy": "已确认风险政策",
  PMI: "制造业PMI",
  CN_T10: "中国10年期国债收益率",
  DR007: "银行间资金价格",
  M1_YOY: "M1同比",
  TSF_STOCK_YOY: "社融存量同比",
  US_T10_NOMINAL: "美国10年期名义国债收益率",
  US_T10_REAL: "美国10年期实际利率",
  CORP_MEDLONG_LOANS_YTD: "企业中长期贷款累计新增",
  USD_FX: "美元及汇率事实",
  YIELD_CURVE_DURATION: "收益率曲线及久期",
  CREDIT_SPREAD_DEFAULT: "信用利差及违约事实",
  FUND_MAPPING: "基金归属",
  LOCAL_ASSET_EVIDENCE: "当地资产基本面与风险",
};
export function confirmedRisk(issue: ResearchIssue, data: OpportunityContext) {
  return (
    issue.category === "RISK" &&
    issue.severity === "HIGH" &&
    issue.confidence === "VERIFIED" &&
    issue.factVerification !== "UNKNOWN" &&
    issue.holdingRelevance === "HELD" &&
    data.decision?.state === "POSITION_REVIEW" &&
    data.portfolio?.state === "READY" &&
    data.portfolio.heldSelected === true &&
    data.portfolio.budgetViolation === true &&
    data.portfolio.budgetState === "BLOCKED"
  );
}
export function issueTone(
  issue: ResearchIssue,
  data: OpportunityContext,
): ResearchTone {
  if (dataIssue(issue)) return "GRAY";
  if (issue.tone === "RED")
    return confirmedRisk(issue, data) ? "RED" : "ORANGE";
  if (
    issue.tone === "GREEN" &&
    (issue.category !== "OPPORTUNITY" || issue.confidence !== "VERIFIED")
  )
    return "GRAY";
  return issue.tone;
}
export const toneClass = (tone: ResearchTone) =>
  `op-tone-${tone.toLowerCase()}${tone === "RED" ? " op-critical-impact" : ""}`;
export const localRoutes: Partial<Record<ResearchAction["type"], string>> = {
  VIEW_HOLDINGS: "/dashboard?scope=MINE",
  VIEW_POLICY: "/risk-policy",
  UPDATE_HOLDINGS: "/onboarding",
  VIEW_OPERATIONS: "/operations",
};
export const topicTargets = [
  "research-macro",
  "research-market",
  "research-global",
  "research-strategy",
];
