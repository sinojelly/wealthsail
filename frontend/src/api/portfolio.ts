import { request } from "./client";
import type {
  InstrumentSearchResult,
  NavTrend,
  PortfolioSummary,
  PositionDto,
  PositionImportConfirmation,
  PositionImportDraft,
  PositionMutationPayload,
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

export async function createPositionImport(
  payload: PositionMutationPayload,
  idempotencyKey: string,
): Promise<PositionImportDraft> {
  const envelope = await request<PositionImportDraft>(
    "/v1/portfolio/imports",
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    },
  );
  return envelope.data;
}

export async function confirmPositionImport(
  batchId: string,
  idempotencyKey: string,
): Promise<PositionImportConfirmation> {
  const envelope = await request<PositionImportConfirmation>(
    `/v1/portfolio/imports/${encodeURIComponent(batchId)}/confirm`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    },
  );
  return envelope.data;
}
