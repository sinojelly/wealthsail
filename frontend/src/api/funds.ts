import { request } from "./client";

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
  inceptionDate: string | null;
  dataSource: "AKSHARE" | "UNAVAILABLE";
  updatedAt: string | null;
  status: "AVAILABLE" | "UNAVAILABLE";
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
