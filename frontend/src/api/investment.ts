import { request } from "./client";
export interface Direction {
  id: string;
  marketId: string;
  marketLabel: string;
  styleId: string;
  label: string;
  framework: string;
  macroResearch?: {
    summary: string;
    channels: Array<{
      label: string;
      evidenceCodes: string[];
      observation: string;
      impact: string;
      counterCase: string;
      watchFor: string[];
    }>;
    missingEvidence: string[];
    methodVersion: string;
  } | null;
  relatedEvents?: MacroEvent[];
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
  latestValueBasis?: "UNIT_NAV" | "ACCUMULATED_NAV" | "ADJUSTED_PRICE" | null;
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
export interface MacroEvent {
  id: string;
  title: string;
  category: string;
  sourceType: string;
  publisher: string;
  sourceUri: string | null;
  publishedAt: string | null;
  retrievedAt: string | null;
  summary: string;
  directionIds: string[];
}
export interface MacroContext {
  state: string;
  months?: number;
  windowStart?: string;
  windowEnd?: string;
  research?: {
    methodVersion: string;
    summary: string;
    dimensions: Array<{
      id: string;
      label: string;
      state: string;
      summary: string;
      evidenceCodes: string[];
      missingEvidence: string[];
    }>;
    changes: Array<{ code: string; summary: string }>;
    limitations: string[];
  } | null;
  events?: MacroEvent[];
  eventState?: string;
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
    referencePeriod?: string | null;
    valueVerification?: "VERIFIED" | "PENDING" | "CONFLICT" | "UNAVAILABLE";
    publicationStatus?: "CONFIRMED" | "UNKNOWN";
    officialEvidence?: {
      publisher: string;
      sourceUri: string | null;
      value: number | null;
      publishedAt: string | null;
      retrievedAt: string | null;
    } | null;
    nextRelease?: {
      status: "SCHEDULED" | "OVERDUE" | "UNKNOWN";
      scheduledAt: string | null;
      referencePeriod: string | null;
      sourceUri: string | null;
    } | null;
    benchmark?: { value: number; label: string } | null;
    statistics?: {
      previousValue: number | null;
      change3Months: number | null;
      change6Months: number | null;
      percentile: number | null;
      median: number | null;
      sampleCount: number;
      firstPeriod: string | null;
      lastPeriod: string | null;
      missingPeriods: string[];
    } | null;
    interpretation?: {
      level: string;
      momentum: string;
      summary: string;
      limitations: string[];
      methodVersion: string;
    } | null;
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
export interface OpportunityScope {
  directionId?: string;
  indexId?: string;
  instrumentId?: string;
  fundCode?: string;
  fundMarket?: string;
}
export interface OpportunityQuery extends OpportunityScope {
  months: number;
}
export interface OpportunitySeries {
  code: string;
  label: string;
  frequency: "DAILY" | "MONTHLY" | "YTD";
  unit: string | null;
  value: number | null;
  asOf: string | null;
  sourceUri: string | null;
  sourceName: string | null;
  publishedAt: string | null;
  retrievedAt: string | null;
  availableAt: string | null;
  timeBasis: string | null;
  methodVersion: string | null;
  metadata?: {
    definitionKey?: string | null;
    definitionVersion?: string | null;
    accumulationStart?: string | null;
    accumulationEnd?: string | null;
  };
  state:
    "READY" | "STALE" | "CONFLICT" | "NOT_COLLECTED" | "PERMISSION_REQUIRED";
  changes: {
    previousValue: number | null;
    change5Samples: number | null;
    change20Samples: number | null;
    changeUnit: "bp" | "pp" | null;
    periodChange?: number | null;
    change3Months?: number | null;
    change6Months?: number | null;
    median: number | null;
    percentile: number | null;
    sampleCount: number;
  };
  history: Array<{
    date: string;
    value: number | null;
    state: string;
    unit?: string | null;
    sourceUri?: string | null;
    sourceName?: string | null;
    publishedAt?: string | null;
    retrievedAt?: string | null;
    availableAt?: string | null;
    timeBasis?: string;
    revision?: number;
    definitionKey?: string | null;
    definitionVersion?: string | null;
    metadata?: { definitionKey?: string; definitionVersion?: string };
  }>;
}
export interface MarketEvidence {
  indexId: string;
  label: string;
  state: string;
  historyQuality?: string;
  trendWindow?: { start: string; end: string; sampleCount: number } | null;
  valuation: {
    state: string;
    pe?: ValuationEvidence | null;
    pb?: ValuationEvidence | null;
    summary?: string;
  };
  trend: {
    state: string;
    ma200?: number | null;
    momentum63?: number | null;
    distanceMA200?: number | null;
  };
  risk: {
    state: string;
    volatility20?: number | null;
    maxDrawdown?: number | null;
  };
  earnings: { state: string; summary?: string };
  missingEvidence: string[];
  sourceUri: string | null;
  asOf: string | null;
}
export type PlanTrack = "LEFT_VALUE" | "RIGHT_TREND";
export type PlanPrice = number | string | null;
export interface ActionableZoneTrack {
  track: PlanTrack;
  state: "CANDIDATE" | "WATCH" | "INSUFFICIENT";
  entryZoneMin: PlanPrice;
  entryZoneMax: PlanPrice;
  stopLossPrice: PlanPrice;
  takeProfitT1: PlanPrice;
  takeProfitT2: PlanPrice;
  reasons: string[];
  priceState: string;
  missingEvidence?: string[];
}
export interface ActionableZoneResult {
  methodVersion: string;
  subjectKind: "index" | "fund" | "nav";
  subjectId: string;
  label: string;
  priceBasis: string;
  unit: string | null;
  asOf: string | null;
  sourceUri: string | null;
  currentPrice: PlanPrice;
  indicators: Record<string, PlanPrice>;
  tracks: ActionableZoneTrack[];
  selectedTrack?: PlanTrack;
  parameters?: Record<string, number | number[]>;
  orderPriceAvailable?: boolean;
}
export interface OpportunityContext extends MacroContext {
  actionableZones?: ActionableZoneResult[];
  dailyResearch?: DailyResearch | null;
  series?: OpportunitySeries[];
  marketEvidence?: MarketEvidence[];
  directionModel?: {
    summary?: string;
    methodVersion: string;
    state: string;
    supports: Array<{
      label: string;
      summary: string;
      evidenceIds: string[];
      state: string;
    }>;
    counterFactors: Array<{
      label: string;
      summary: string;
      evidenceIds: string[];
      state: string;
    }>;
    watchFor: string[];
    requiredEvidence: string[];
    missingEvidence: string[];
    missingRequiredEvidence?: string[];
    recommendedChecks?: string[];
    failedConditions?: Array<{
      id: string;
      label: string;
      reason: string;
      evidenceIds: string[];
    }>;
    researchState?: string;
    qualificationState?: string;
  } | null;
  decision?: {
    state: string;
    actionableZones?: ActionableZoneResult;
    marketState: string;
    personalState: string;
    summary: string;
    nextChecks: string[];
    assessment?: {
      holdingState: string;
      riskState: string;
      marketResearchState: string;
    };
  } | null;
  portfolio?: {
    state: string;
    heldSelected: boolean | null;
    selectedWeight: number | null;
    sameIndexWeight: number | null;
    cashRatio: number | null;
    asOf: string | null;
    summary: string;
    missingEvidence: string[];
    budgetViolation?: boolean;
    budgetState?: string;
  } | null;
  notices?: ConditionNotice[];
}
export interface DailyResearch {
  methodVersion: string;
  scheduleState?: "NOT_DUE" | "DUE_NOT_DISPATCHED" | "DISPATCHED" | "DISABLED";
  todayScheduledFor?: string | null;
  state:
    | "NOT_RUN"
    | "RUNNING"
    | "READY"
    | "STALE"
    | "FAILED"
    | "UNCERTAIN"
    | "DISABLED";
  runId: string | null;
  reportRunId?: string | null;
  reportCompletedAt?: string | null;
  reportSlotDate?: string | null;
  scheduledFor: string | null;
  startedAt: string | null;
  completedAt: string | null;
  asOf: string | null;
  nextRunAt: string | null;
  errorClass?: string | null;
  report: {
    methodVersion?: string;
    asOf?: string;
    summary: string;
    coverage: Array<{ topic: string; status: string; summary: string }>;
    sources: Array<{
      id: string;
      url: string;
      title: string;
      quote: string;
      publishedDate: string | null;
      publishedAt: string | null;
      retrievedAt: string | null;
      verification: string;
      contentHash?: string | null;
    }>;
    events: Array<{
      id: string;
      title: string;
      summary: string;
      phase: "PLANNED" | "ANNOUNCED" | "IMPLEMENTED" | "REPORTED" | "OPINION";
      importance: string;
      sourceIds: string[];
      channels: string[];
      affectedDirectionIds: string[];
      scenarios: Array<{ condition: string; impact: string; counter: string }>;
      watchFor: string[];
      unknowns: string[];
      marketReaction: "UNVERIFIED";
      reasoningType: "MODEL_CONDITIONAL";
      factVerification?: "UNVERIFIED";
    }>;
    upcoming: Array<{
      id: string;
      title: string;
      phase: "PLANNED";
      scheduledDate: string | null;
      scheduledAt: string | null;
      sourceIds: string[];
      affectedDirectionIds: string[];
      watchFor: string[];
    }>;
    energy: Array<{
      id: string;
      code: string;
      label: string;
      value: number | null;
      unit: "USD/barrel" | "USD/gallon";
      frequency: "DAILY" | "WEEKLY";
      asOf: string | null;
      sourceUri: string | null;
      retrievedAt: string | null;
      publishedAt: null;
      timeBasis: "OBSERVATION_ONLY";
      state: "READY" | "STALE";
      previousValue: number | null;
      previousDate: string | null;
      change: number | null;
      changePct: number | null;
      sampleCount: number;
    }>;
  } | null;
}
export interface ConditionNotice {
  id: string;
  title: string;
  summary: string;
  severity: string;
  createdAt: string;
  readAt: string | null;
}
export interface ConditionMonitor extends OpportunityScope {
  id: string;
  enabled: boolean;
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
  actionableZones?: ActionableZoneResult;
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
export function getOpportunityContext(
  query: OpportunityQuery,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return read<OpportunityContext>(
    `/investment/opportunity-context?${params}`,
    signal,
  );
}
export const getConditionMonitors = (signal?: AbortSignal) =>
  read<ConditionMonitor[]>("/investment/condition-monitors", signal);
export async function setConditionMonitor(
  scope: OpportunityScope & { enabled: boolean },
) {
  return (
    await request<{ id: string; enabled: boolean }>(
      "/v1/investment/condition-monitors",
      { method: "POST", body: JSON.stringify(scope) },
    )
  ).data;
}
export async function ackConditionNotice(id: string) {
  return (
    await request<{ id: string; readAt: string }>(
      `/v1/investment/condition-notices/${encodeURIComponent(id)}/ack`,
      { method: "POST" },
    )
  ).data;
}
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
