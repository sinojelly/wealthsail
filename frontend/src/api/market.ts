import { request } from "./client";

export interface HotStock {
  symbol: string;
  name: string;
  price: number | null;
  change_pct: number | null;
  volume: number | null;
  turnover: number | null;
}

export interface MarketHotResponse {
  items: HotStock[];
  updatedAt: string;
}

export async function getMarketHot(): Promise<MarketHotResponse> {
  const envelope = await request<MarketHotResponse>("/v1/market/hot");
  return envelope.data;
}

/** 成交量（手）→ 中文缩写：亿手 / 万手 / 手。 */
export function fmtVolumeHands(volume: number | null): string {
  if (volume == null) return "--";
  if (volume >= 1e8) return `${(volume / 1e8).toFixed(2)} 亿手`;
  if (volume >= 1e4) return `${(volume / 1e4).toFixed(2)} 万手`;
  return `${Math.round(volume)} 手`;
}

/** 成交额（元）→ 亿元。 */
export function fmtTurnoverYuan(turnover: number | null): string {
  if (turnover == null) return "--";
  return `${(turnover / 1e8).toFixed(2)} 亿`;
}

export interface MarketSentiment {
  tradeDate: string | null;
  totalUp: number | null;
  totalDown: number | null;
  totalFlat: number | null;
  limitUp: number | null;
  limitDown: number | null;
  turnover: string | null; // 亿元
  northFlow: string | null; // 亿元
  sentimentScore: string | null; // 0-100
  source: string;
  sourceAsOf: string | null;
  retrievedAt: string | null;
  qualityStatus: "FRESH" | "ESTIMATED" | "STALE" | "UNAVAILABLE";
  coveragePct: string | null;
  methodVersion: string | null;
  updatedAt: string | null;
}

export async function getMarketSentiment(): Promise<MarketSentiment> {
  const envelope = await request<MarketSentiment>("/v1/market/sentiment");
  return envelope.data;
}
