import { request } from "./client";
import type { StudyPriceWatch } from "./study-api";

export interface LatestFundQuote {
  fundCode: string;
  confirmedNav: StudyPriceWatch["confirmedNav"];
  currentEstimate: StudyPriceWatch["currentEstimate"];
}
export interface LatestFundQuotes {
  items: LatestFundQuote[];
  asOf: string;
}
export async function getLatestFundQuotes(
  codes: string[],
): Promise<LatestFundQuotes> {
  return (
    await request<LatestFundQuotes>(
      `/v1/funds/quotes?codes=${encodeURIComponent([...new Set(codes)].sort().join(","))}`,
    )
  ).data;
}

/** Explicit quote refresh only; normal reads never contact a quote provider. */
export async function refreshFundQuotes(
  codes: string[],
): Promise<LatestFundQuotes> {
  return (
    await request<LatestFundQuotes>(
      `/v1/funds/quotes/refresh?codes=${encodeURIComponent([...new Set(codes)].sort().join(","))}`,
      { method: "POST" },
    )
  ).data;
}

/** reference.fund_nav_ranges 缓存行的可用形态。 */
export interface FundNavRange {
  fundCode: string;
  fundName: string;
  fundType: string;
  latestNav: number | null;
  latestNavDate: string | null;
  highestNav: number | null;
  highestNavDate: string | null;
  lowestNav: number | null;
  lowestNavDate: string | null;
  drawdownFromHigh: number | null;
  riseFromLow: number | null;
  currentPercentile: number | null;
  navRangePosition?: number | null;
  historyStartDate?: string | null;
  inceptionDate: string | null;
  dataSource: "AKSHARE" | "UNAVAILABLE" | null;
  updatedAt: string | null;
  status: "AVAILABLE" | "UNAVAILABLE" | "NOT_COLLECTED";
}

/** 未收录基金：后端显式返回的非错误状态。 */
export interface FundRangeNotCollected {
  fundCode: string;
  status: "NOT_COLLECTED";
  message: string;
}

export type FundRangeResponse = FundNavRange | FundRangeNotCollected;

/** 按需抓取：Worker 异步执行，接口立即返回队列受理结果。 */
export interface FundFetchResponse {
  fundCode: string;
  status: "QUEUED";
  message: string;
  runId: string;
  created: boolean;
}

export async function searchFunds(keyword: string): Promise<FundNavRange[]> {
  const envelope = await request<FundNavRange[]>(
    `/v1/funds/search?keyword=${encodeURIComponent(keyword)}`,
  );
  return envelope.data;
}

/** 快速筛选类别：缓存浏览，不触发抓取。 */
export type FundBrowseCategory =
  "stable" | "growth" | "index" | "overseas" | "undervalued" | "near_high";

export async function browseFunds(
  category: FundBrowseCategory,
): Promise<FundNavRange[]> {
  const envelope = await request<FundNavRange[]>(
    `/v1/funds/browse?category=${encodeURIComponent(category)}`,
  );
  return envelope.data;
}

export async function getFundRange(
  fundCode: string,
): Promise<FundRangeResponse> {
  const envelope = await request<FundRangeResponse>(
    `/v1/funds/${encodeURIComponent(fundCode)}/range`,
  );
  return envelope.data;
}

export async function triggerFundFetch(
  fundCode: string,
): Promise<FundFetchResponse> {
  const envelope = await request<FundFetchResponse>(
    `/v1/funds/${encodeURIComponent(fundCode)}/fetch`,
    { method: "POST" },
  );
  return envelope.data;
}

/** 后端 NUMERIC 可能以数字或字符串到达，统一先转 number 再格式化。 */
function toNumber(value: number | string | null): number | null {
  if (value == null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 净值：保留 4 位，与累计净值精度一致。 */
export function fmtNav(value: number | string | null): string {
  const parsed = toNumber(value);
  return parsed == null ? "--" : parsed.toFixed(4);
}

/** 百分比字段（回撤/涨幅/分位）：后端为 0-100 数值。 */
export function fmtPercent(value: number | string | null): string {
  const parsed = toNumber(value);
  return parsed == null ? "--" : `${parsed.toFixed(2)}%`;
}

/** 当前分位的五档语义标签（20% 一档，含下界不含上界，顶部档含 100）。 */
export function percentileBand(
  value: number | string | null,
):
  | "接近历史底部"
  | "历史偏低"
  | "历史中位"
  | "历史偏高"
  | "接近历史顶部"
  | null {
  const parsed = toNumber(value);
  if (parsed == null) return null;
  if (parsed < 20) return "接近历史底部";
  if (parsed < 40) return "历史偏低";
  if (parsed < 60) return "历史中位";
  if (parsed < 80) return "历史偏高";
  return "接近历史顶部";
}

/** 当前分位在表格中的展示形态："72.50% 分位 · 历史偏高"。 */
export function fmtPercentileLabel(value: number | string | null): string {
  const parsed = toNumber(value);
  if (parsed == null) return "--";
  const bands = ["区间底部", "区间偏低", "区间中部", "区间偏高", "区间顶部"];
  return `区间位置 ${parsed.toFixed(2)}% · ${bands[Math.min(4, Math.max(0, Math.floor(parsed / 20)))]}`;
}

export function fmtDate(value: string | null): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("zh-CN", { hour12: false });
}

export function fmtTimestamp(value: string | null): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

/** YYYY/MM/DD 补零格式，用于表格脚注的更新日期。 */
export function fmtDateSlash(value: string | null): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}/${month}/${day}`;
}

export type FundNavPeriod = "6m" | "1y" | "2y" | "all";
export interface FundNavHistory {
  methodVersion: "fund-unit-nav-trend-v1";
  fundCode: string;
  fundName: string;
  period: FundNavPeriod;
  priceBasis: "UNIT_NAV";
  unit: string;
  state: "READY" | "PARTIAL" | "NOT_COLLECTED" | "UNAVAILABLE";
  asOf: string | null;
  retrievedAt: string | null;
  sourceUri: string | null;
  points: Array<{ date: string; unitNav: string }>;
  coverage: {
    startDate: string | null;
    endDate: string | null;
    fullHistory: boolean;
    requestedStartDate: string | null;
    sampleCount: number;
  };
  summary: {
    minimumNav: string | null;
    maximumNav: string | null;
    currentNav: string | null;
    percentilePct: number | null;
    percentileMethod: string;
  };
  refresh: {
    state: "IDLE" | "QUEUED" | "RUNNING" | "FAILED" | "READY" | "PARTIAL";
    reason?: string;
    runId: string | null;
    pollAfterMs: number | null;
  };
}
export async function getFundNavHistory(
  code: string,
  period: FundNavPeriod,
  signal?: AbortSignal,
): Promise<FundNavHistory> {
  const envelope = await request<FundNavHistory>(
    `/v1/funds/${encodeURIComponent(code)}/nav-history?period=${period}`,
    { signal },
  );
  return envelope.data;
}

/** Queue NAV repair only after the user explicitly requests it. */
export async function refreshFundNavHistory(
  code: string,
  period: FundNavPeriod,
  signal?: AbortSignal,
): Promise<FundNavHistory> {
  return (
    await request<FundNavHistory>(
      `/v1/funds/${encodeURIComponent(code)}/nav-history/refresh?period=${period}`,
      { method: "POST", signal },
    )
  ).data;
}
