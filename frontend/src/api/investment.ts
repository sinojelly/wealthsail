import { request } from "./client";
export interface Direction {
  id: string;
  marketId: string;
  marketLabel: string;
  styleId: string;
  label: string;
  framework: string;
}
export interface ResearchIndex {
  id: string;
  code: string;
  name: string;
  market: string;
  exposure: string;
  fundCount: number;
  state: string;
}
export interface ResearchFund {
  instrumentId: string | null;
  fundCode: string;
  fundName: string;
  fundType: string;
  tradeForm: "ETF" | "FUND";
  market: string;
  currency: string;
  latestValue: number | null;
  latestDate: string | null;
  navRangePosition: number | null;
  technicalScore: number | null;
  technicalState?: string | null;
  dataState: string;
  updatedAt: string | null;
  performanceState?: string | null;
  valueState?: string | null;
  valueEligible?: boolean | null;
  return63?: number | null;
  return252?: number | null;
  maxDrawdown?: number | null;
  volatility20?: number | null;
}
export interface SearchResult extends Partial<ResearchFund> {
  kind: "index" | "fund";
  id: string;
  code: string;
  name: string;
  indexId: string | null;
  directionId: string | null;
}
export interface MacroContext {
  state: string;
  months?: number;
  windowStart?: string;
  windowEnd?: string;
  items: Array<{
    code: string;
    label: string;
    value: number | null;
    asOf: string | null;
    publishedAt: string | null;
    source: string | null;
    state: string;
    unit?: string | null;
    sourceUri?: string | null;
    historyMonths?: number;
    sampleCount?: number;
    periodChange?: number | null;
    trendState?: string;
    history?: Array<{
      asOf: string;
      value: number;
      unit?: string | null;
      source?: string | null;
      sourceUri?: string | null;
      state?: string;
      publishedAt?: string | null;
      retrievedAt?: string | null;
      revision?: number;
    }>;
  }>;
}
export interface PerformanceEvidence {
  state: string;
  eligible: boolean;
  return63: number | null;
  return252: number | null;
  maxDrawdown: number | null;
  volatility20: number | null;
  historyStart: string | null;
  historyEnd: string | null;
  sampleCount: number;
  reasons: string[];
}
export interface OperatingCosts {
  state: string;
  managementRate?: number | null;
  custodyRate?: number | null;
  salesServiceRate?: number | null;
  totalRate?: number | null;
  sourceUri?: string | null;
  retrievedAt?: string | null;
}
export interface ValuationEvidence {
  state: string;
  latest?: number | null;
  percentile?: number | null;
  sampleCount?: number;
  historyStart?: string | null;
  historyEnd?: string | null;
  sourceUri?: string | null;
}
export interface ValueEvidence {
  eligible: boolean;
  state: "RESEARCH_CANDIDATE" | "NOT_CANDIDATE" | "INCOMPLETE";
  reasons: string[];
  evidence: {
    pe?: ValuationEvidence;
    pb?: ValuationEvidence;
    costs?: OperatingCosts;
    performance?: {
      state: string;
      eligible: boolean;
      maxDrawdown?: number | null;
    };
    limitations?: string[];
  };
}
export interface Analysis {
  state: string;
  score: number | null;
  indicators: Record<string, number | null>;
  reasons: Array<{
    key: string;
    reason: string;
    passed: boolean;
    points: number;
    weight: number;
  }>;
  points: Array<{ date: string; close: number }>;
  asOf?: string;
  source?: string;
  inputHash?: string;
  algorithmVersion?: string;
  seriesKind?: string;
  performance?: PerformanceEvidence;
  costs?: OperatingCosts;
  valueEvidence?: ValueEvidence;
  valuation?: {
    state: string;
    requestedEnd?: string | null;
    sourceUri?: string | null;
    pe?: {
      state: string;
      metric?: string;
      points?: Array<{ date: string; value: number }>;
    };
    pb?: {
      state: string;
      metric?: string;
      points?: Array<{ date: string; value: number }>;
    };
  };
}
export interface ScreenFilters {
  directionId?: string;
  indexId?: string;
  keyword?: string;
  tradeForm?: string;
  sortBy?: string;
  sortOrder?: string;
  analysisAsOf?: string;
  scorableOnly?: string;
  performanceOnly?: string;
  valueCandidateOnly?: string;
  minHistoryDays?: string;
  page: number;
  pageSize: number;
}
export interface ScreenResult {
  items: ResearchFund[];
  total: number;
  page: number;
  pageSize: number;
  analysisAsOf?: string;
}
async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await request<T>(`/v1${path}`, { signal })).data;
}
export const getDirections = (signal?: AbortSignal) =>
  read<Direction[]>("/investment/directions", signal);
export const getIndices = (id: string, signal?: AbortSignal) =>
  read<ResearchIndex[]>(
    `/investment/directions/${encodeURIComponent(id)}/indices`,
    signal,
  );
export const getMarketContext = (signal?: AbortSignal, months = 36) =>
  read<MacroContext>(`/investment/market-context?months=${months}`, signal);
export const searchResearch = (keyword: string, signal?: AbortSignal) =>
  read<SearchResult[]>(
    `/investment/search?keyword=${encodeURIComponent(keyword)}`,
    signal,
  );
export function screenFunds(filters: ScreenFilters, signal?: AbortSignal) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== "") params.set(k, String(v));
  });
  return read<ScreenResult>(`/funds/screener?${params}`, signal);
}
export const getAnalysis = (
  kind: "index" | "fund" | "nav",
  id: string,
  signal?: AbortSignal,
) =>
  read<Analysis>(
    `/investment/${kind === "index" ? "indices" : kind === "nav" ? "fund-ranges" : "instruments"}/${encodeURIComponent(id)}/analysis`,
    signal,
  );
export async function refreshResearch(
  kind: "index" | "fund" | "nav",
  id: string,
) {
  return (
    await request<{ runId: string; created: boolean }>(
      "/v1/investment/refresh",
      { method: "POST", body: JSON.stringify({ kind, id }) },
    )
  ).data;
}
export const getRefreshStatus = (id: string, signal?: AbortSignal) =>
  read<{ status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" }>(
    `/investment/refresh/${encodeURIComponent(id)}`,
    signal,
  );
