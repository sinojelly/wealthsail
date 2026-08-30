import { request } from "./client";
import type {
  HoldingStartConfirmationResult,
  HoldingStartEstimateBatchResult,
  HoldingStartEstimateConfirmation,
  InstrumentSearchResult,
  NavTrend,
  PortfolioSummary,
  PerformanceBaselineDto,
  PositionDto,
  PositionImportConfirmation,
  PositionImportDraft,
  PositionMutationPayload,
  PortfolioAssetScope,
  TiantianConfirmPayload,
  TiantianImportConfirmation,
  TiantianImportDetail,
  TiantianImportList,
  TiantianDuplicatePolicy,
} from "./types";
import { createIdempotencyKey } from "../lib/idempotency";

function scopeQuery(scope: PortfolioAssetScope): string {
  const params = new URLSearchParams({ scope: scope.kind });
  if (scope.kind === "MEMBER" && scope.memberId) {
    params.set("memberId", scope.memberId);
  }
  return params.toString();
}

export async function getSummary(
  scope: PortfolioAssetScope = { kind: "MINE" },
): Promise<PortfolioSummary> {
  const envelope = await request<PortfolioSummary>(
    `/v1/portfolio/summary?${scopeQuery(scope)}`,
  );
  return envelope.data;
}

export async function getPerformanceBaselines(): Promise<
  PerformanceBaselineDto[]
> {
  const envelope = await request<PerformanceBaselineDto[]>(
    "/v1/portfolio/performance-baselines",
  );
  return envelope.data;
}

export async function revisePerformanceBaseline(input: {
  accountId: string;
  initialCumulativePnl: string;
  version: number;
}): Promise<PerformanceBaselineDto> {
  const envelope = await request<PerformanceBaselineDto>(
    `/v1/portfolio/performance-baselines/${encodeURIComponent(input.accountId)}`,
    {
      method: "PUT",
      headers: {
        "Idempotency-Key": createIdempotencyKey(),
        "If-Match": `"${input.version}"`,
      },
      body: JSON.stringify({
        initialCumulativePnl: input.initialCumulativePnl,
      }),
    },
  );
  return envelope.data;
}

export async function getPositions(
  scope: PortfolioAssetScope = { kind: "MINE" },
): Promise<PositionDto[]> {
  const envelope = await request<PositionDto[]>(
    `/v1/portfolio/positions?${scopeQuery(scope)}`,
  );
  return envelope.data;
}

export async function getNavTrend(
  days = 60,
  scope: PortfolioAssetScope = { kind: "MINE" },
): Promise<NavTrend> {
  const envelope = await request<NavTrend>(
    `/v1/portfolio/nav-trend?days=${days}&${scopeQuery(scope)}`,
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
  const envelope = await request<PositionImportDraft>("/v1/portfolio/imports", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  });
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

export async function previewTiantianImport(
  file: File,
  target: { accountId?: string; accountName?: string },
  idempotencyKey: string,
  duplicatePolicy: TiantianDuplicatePolicy = "AUTO",
): Promise<TiantianImportDetail> {
  const form = new FormData();
  form.append("file", file);
  if (target.accountId) form.append("accountId", target.accountId);
  if (target.accountName) form.append("accountName", target.accountName);
  form.append("duplicatePolicy", duplicatePolicy);
  const envelope = await request<TiantianImportDetail>(
    "/v1/portfolio/imports/tiantian/preview",
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: form,
    },
  );
  return envelope.data;
}

export async function getTiantianImport(
  batchId: string,
): Promise<TiantianImportDetail> {
  const envelope = await request<TiantianImportDetail>(
    `/v1/portfolio/imports/${encodeURIComponent(batchId)}`,
  );
  return envelope.data;
}

export async function listTiantianImports(
  options: { cursor?: string; limit?: number; status?: "DRAFT" } = {},
): Promise<TiantianImportList> {
  const limit = options.limit ?? 20;
  const params = new URLSearchParams({
    sourceType: "TIANTIAN_FUND_ZIP",
    limit: String(limit),
  });
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.status) params.set("status", options.status);
  const envelope = await request<TiantianImportList>(
    `/v1/portfolio/imports?${params.toString()}`,
  );
  return envelope.data;
}

export async function confirmTiantianImport(
  batchId: string,
  version: number,
  payload: TiantianConfirmPayload,
  idempotencyKey: string,
): Promise<TiantianImportConfirmation> {
  const envelope = await request<TiantianImportConfirmation>(
    `/v1/portfolio/imports/${encodeURIComponent(batchId)}/confirm`,
    {
      method: "POST",
      headers: {
        "Idempotency-Key": idempotencyKey,
        "If-Match": `"${version}"`,
      },
      body: JSON.stringify(payload),
    },
  );
  return envelope.data;
}

export async function cancelTiantianImport(
  batchId: string,
  version: number,
  idempotencyKey: string,
): Promise<{ batchId: string; status: "CANCELLED"; version: number }> {
  const envelope = await request<{
    batchId: string;
    status: "CANCELLED";
    version: number;
  }>(`/v1/portfolio/imports/${encodeURIComponent(batchId)}/cancel`, {
    method: "POST",
    headers: {
      "Idempotency-Key": idempotencyKey,
      "If-Match": `"${version}"`,
    },
  });
  return envelope.data;
}

export async function retryTiantianMetrics(
  batchId: string,
  idempotencyKey: string,
): Promise<{ batchId: string; metricsStatus: "PENDING"; jobKey: string }> {
  const envelope = await request<{
    batchId: string;
    metricsStatus: "PENDING";
    jobKey: string;
  }>(`/v1/portfolio/imports/${encodeURIComponent(batchId)}/metrics/retry`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
  });
  return envelope.data;
}

export async function confirmTiantianHoldingStart(
  accountId: string,
  instrumentId: string,
  startedOn: string,
  idempotencyKey: string,
): Promise<HoldingStartConfirmationResult> {
  const envelope = await request<HoldingStartConfirmationResult>(
    `/v1/portfolio/positions/${encodeURIComponent(accountId)}/${encodeURIComponent(instrumentId)}/holding-start/confirm`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ startedOn }),
    },
  );
  return envelope.data;
}

export async function confirmTiantianHoldingStartEstimates(
  items: HoldingStartEstimateConfirmation[],
  idempotencyKey: string,
): Promise<HoldingStartEstimateBatchResult> {
  const envelope = await request<HoldingStartEstimateBatchResult>(
    "/v1/portfolio/positions/holding-start/confirm-estimates",
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ items }),
    },
  );
  return envelope.data;
}
