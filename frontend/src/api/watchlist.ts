import { request } from "./client";
import type { InstrumentType, WatchlistItem } from "./types";

export interface AddWatchlistInput {
  symbol: string;
  /** 以下元数据来自搜索结果，用于主数据缺失时的自动登记（服务端兜底核验）。 */
  name?: string;
  assetType?: InstrumentType;
  market?: string;
  currency?: string;
}

export async function list(): Promise<WatchlistItem[]> {
  const envelope = await request<WatchlistItem[]>("/v1/watchlist");
  return envelope.data;
}

export async function add(input: AddWatchlistInput): Promise<WatchlistItem> {
  const envelope = await request<WatchlistItem>("/v1/watchlist", {
    method: "POST",
    body: JSON.stringify({
      symbol: input.symbol,
      ...(input.name ? { name: input.name } : {}),
      ...(input.assetType ? { assetType: input.assetType } : {}),
      ...(input.market ? { market: input.market } : {}),
      ...(input.currency ? { currency: input.currency } : {}),
    }),
  });
  return envelope.data;
}

export async function remove(symbol: string): Promise<WatchlistItem> {
  const envelope = await request<WatchlistItem>(`/v1/watchlist/${encodeURIComponent(symbol)}`, {
    method: "DELETE",
  });
  return envelope.data;
}
