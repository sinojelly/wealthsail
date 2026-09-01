import { request } from "./client";
import type { WatchlistItem } from "./types";

export async function list(): Promise<WatchlistItem[]> {
  const envelope = await request<WatchlistItem[]>("/v1/watchlist");
  return envelope.data;
}

export async function add(input: { symbol: string }): Promise<WatchlistItem> {
  const envelope = await request<WatchlistItem>("/v1/watchlist", {
    method: "POST",
    body: JSON.stringify({ symbol: input.symbol }),
  });
  return envelope.data;
}

export async function remove(symbol: string): Promise<WatchlistItem> {
  const envelope = await request<WatchlistItem>(`/v1/watchlist/${encodeURIComponent(symbol)}`, {
    method: "DELETE",
  });
  return envelope.data;
}
