import type { QualityCode } from "../../api/generated/types";

export type ReportSession =
  "PRE_MARKET" | "INTRADAY" | "FUND_1430" | "POST_MARKET";

export interface EvidenceRecord {
  id: string;
  type: string;
  title: string;
  content: string;
  source: string;
  sourceUrl: string;
  asOf: string;
  publishedAt: string;
  retrievedAt: string;
  retrievalMethod: string;
  schemaVersion: string;
  modelRunId?: string;
  quality: QualityCode;
  hash: string;
}

export interface ReportRecord {
  id: string;
  session: ReportSession;
  title: string;
  subtitle: string;
  generatedAt: string;
  status: "已发布" | "待复核";
}

export const reports: ReportRecord[] = [
  {
    id: "R-PRE-20260823",
    session: "PRE_MARKET",
    title: "盘前简报",
    subtitle: "隔夜市场、今日事件与持仓风险",
    generatedAt: "08:10",
    status: "已发布",
  },
  {
    id: "R-INTRA-20260823",
    session: "INTRADAY",
    title: "盘中跟踪",
    subtitle: "持仓异动与风险预算触点",
    generatedAt: "11:28",
    status: "已发布",
  },
  {
    id: "R-FUND-20260823",
    session: "FUND_1430",
    title: "基金 2:30 报告",
    subtitle: "场外基金估值、覆盖率与截止时间",
    generatedAt: "14:30",
    status: "已发布",
  },
  {
    id: "R-POST-20260823",
    session: "POST_MARKET",
    title: "盘后交互复盘",
    subtitle: "计划—决策—执行闭环与证据回看",
    generatedAt: "16:35",
    status: "已发布",
  },
];

export const evidenceRecords: Record<string, EvidenceRecord> = {
  "E-PRE-001": {
    id: "E-PRE-001",
    type: "市场行情",
    title: "美股主要指数收盘",
    content: "标普500 +0.42%，纳斯达克 +0.61%；半导体板块相对活跃。",
    source: "权威行情数据源 · 日线收盘",
    sourceUrl: "https://example.com/market/us-close",
    asOf: "2026-08-22 16:00 America/New_York",
    publishedAt: "2026-08-22 16:15 -04:00",
    retrievedAt: "2026-08-23 07:43 +08:00",
    retrievalMethod: "market-api/v2",
    schemaVersion: "evidence.market.v1",
    quality: "FRESH",
    hash: "8d9e7a2f4b7c0d31…e812",
  },
  "E-PRE-002": {
    id: "E-PRE-002",
    type: "宏观事件",
    title: "今日经济日历",
    content: "今日 09:30 公布重点行业数据；20:30 海外通胀分项披露。",
    source: "官方经济日历聚合",
    sourceUrl: "https://example.com/calendar",
    asOf: "2026-08-23",
    publishedAt: "2026-08-22 21:10 +08:00",
    retrievedAt: "2026-08-23 07:45 +08:00",
    retrievalMethod: "document-crawler/v1",
    schemaVersion: "evidence.event.v1",
    modelRunId: "run-pre-0823-01",
    quality: "FRESH",
    hash: "1f3728ea8f95669d…cc21",
  },
  "E-INTRA-001": {
    id: "E-INTRA-001",
    type: "盘中行情",
    title: "科大讯飞盘中异动",
    content:
      "10:47 起成交量快速放大，价格跌破风控观察线 43.50 元；当前跌幅 3.21%。",
    source: "交易所授权行情快照",
    sourceUrl: "https://example.com/quote/002230",
    asOf: "2026-08-23 11:25 +08:00",
    publishedAt: "2026-08-23 11:25 +08:00",
    retrievedAt: "2026-08-23 11:25:08 +08:00",
    retrievalMethod: "market-stream/v1",
    schemaVersion: "evidence.quote.v1",
    quality: "FRESH",
    hash: "6a414e058dcc2b6a…a88d",
  },
  "E-FUND-001": {
    id: "E-FUND-001",
    type: "基金估值",
    title: "易方达消费行业估值快照",
    content: "基于最近披露持仓覆盖 72.4%，估算净值 1.7326；较上一净值 +0.48%。",
    source: "基金公司披露 + 交易所行情",
    sourceUrl: "https://example.com/fund/110022",
    asOf: "2026-08-23 14:28 +08:00",
    publishedAt: "2026-07-21 18:00 +08:00",
    retrievedAt: "2026-08-23 14:29 +08:00",
    retrievalMethod: "fund-estimator/v1",
    schemaVersion: "evidence.fund-estimate.v1",
    modelRunId: "run-fund-0823-01",
    quality: "ESTIMATED",
    hash: "02bd2e99df4f940b…377c",
  },
  "E-POST-001": {
    id: "E-POST-001",
    type: "决策事件",
    title: "招商银行计划执行核对",
    content:
      "盘前计划：若银行板块放量并站稳观察位，保留仓位。用户 10:12 回复“继续持有”，当日无成交。",
    source: "企业微信交互事件 + 计划快照",
    sourceUrl: "wecom://message/demo-post-001",
    asOf: "2026-08-23 15:00 +08:00",
    publishedAt: "2026-08-23 16:31 +08:00",
    retrievedAt: "2026-08-23 16:32 +08:00",
    retrievalMethod: "event-store/read-model-v1",
    schemaVersion: "evidence.decision.v2",
    modelRunId: "run-post-0823-01",
    quality: "FRESH",
    hash: "bdc6ff27f6f86b45…937e",
  },
};
