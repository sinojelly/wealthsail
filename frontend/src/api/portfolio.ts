import { request } from "./client";
import type {
  DeletePositionResult,
  InstrumentSearchResult,
  NavTrend,
  PortfolioSummary,
  PositionDto,
  PositionMutationPayload,
  PositionMutationResult,
  PositionUpdatePayload,
} from "./types";

export async function getSummary(): Promise<PortfolioSummary> {
  const envelope = await request<PortfolioSummary>("/v1/portfolio/summary");
  return envelope.data;
}

export async function getPositions(): Promise<PositionDto[]> {
  const envelope = await request<PositionDto[]>("/v1/portfolio/positions");
  return envelope.data;
}

export async function getNavTrend(days = 60): Promise<NavTrend> {
  const envelope = await request<NavTrend>(
    `/v1/portfolio/nav-trend?days=${days}`,
  );
  return envelope.data;
}

export async function searchInstruments(
  keyword: string,
): Promise<InstrumentSearchResult[]> {
  const envelope = await request<{
    items: InstrumentSearchResult[];
    total: number;
  }>(`/v1/portfolio/search?keyword=${encodeURIComponent(keyword)}`);
  return envelope.data.items;
}

export async function addPosition(
  payload: PositionMutationPayload,
): Promise<PositionMutationResult> {
  const envelope = await request<PositionMutationResult>(
    "/v1/portfolio/positions",
    { method: "POST", body: JSON.stringify(payload) },
  );
  return envelope.data;
}

export async function updatePosition(
  symbol: string,
  payload: PositionUpdatePayload,
): Promise<PositionMutationResult> {
  const envelope = await request<PositionMutationResult>(
    `/v1/portfolio/positions/${encodeURIComponent(symbol)}`,
    { method: "PATCH", body: JSON.stringify(payload) },
  );
  return envelope.data;
}

export async function deletePosition(
  symbol: string,
): Promise<DeletePositionResult> {
  const envelope = await request<DeletePositionResult>(
    `/v1/portfolio/positions/${encodeURIComponent(symbol)}`,
    { method: "DELETE" },
  );
  return envelope.data;
}
