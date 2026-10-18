import { request } from "./client";
import type {
  ActionableZoneResult,
  OpportunityScope,
  PlanTrack,
  ValueDirection,
} from "./investment";
export type StudyScope = OpportunityScope;
export interface StudyCondition {
  metric: string;
  subjectKey?: string | null;
  operator: "GT" | "GTE" | "LT" | "LTE";
  threshold: string | number;
  effect: "STRENGTHEN" | "WEAKEN" | "TRIGGER" | "INVALIDATE";
  unit?: string;
}
export interface StudyObservation {
  metric: string;
  subjectKey?: string | null;
  label?: string;
  value: string | number | null;
  unit?: string;
  asOf?: string | null;
  state?: string;
  sourceUri?: string;
  retrievedAt?: string;
  verification?: string;
}
export interface StudySnapshot {
  actionablePlan?: ActionableZoneResult;
  valueDirection?: Omit<ValueDirection, "plans">;
  title: string;
  phase?: string | null;
  importance?: string | null;
  unknowns?: string[];
  affectedDirectionIds?: string[];
  thesis: string;
  transmission: string[];
  supports: string[];
  counterFactors: string[];
  watchFor: string[];
  invalidationConditions: string[];
  scenarios?: Array<{ condition: string; impact: string; counter: string }>;
  sources: Array<{
    id: string;
    url?: string;
    title?: string;
    quote?: string;
    publishedDate?: string | null;
    publishedAt?: string | null;
    retrievedAt?: string | null;
    verification?: string;
  }>;
  origin: {
    originType: string;
    reportRunId?: string | null;
    eventId?: string | null;
    reportAsOf?: string | null;
    valueDirectionKey?: string | null;
  };
  scope: StudyScope;
  createdAt: string;
  expiresOn: string;
  conditions: StudyCondition[];
  observations: StudyObservation[];
  benchmarkIndexId?: string | null;
}
export interface StudyTrack {
  priceWatch?: StudyPriceWatch | null;
  id: string;
  title: string;
  enabled: boolean;
  generation: number;
  status: string;
  createdAt: string;
  updatedAt: string;
  unreadCount: number;
  scope: StudyScope;
}
export interface StudyPriceWatch {
  frozenPlan: ActionableZoneResult | null;
  confirmedNav: {
    value: string | null;
    asOf: string | null;
    retrievedAt?: string | null;
    sourceUri?: string | null;
    unit?: string | null;
    priceBasis?: string | null;
    quality?: string | null;
    usable?: boolean;
  } | null;
  estimate1430: {
    value: string | null;
    asOf: string | null;
    checkedAt?: string | null;
    sourceUri?: string | null;
    unit?: string | null;
    priceBasis?: string | null;
    state?: string | null;
    runDate?: string | null;
    usable?: boolean;
  } | null;
  holding: { state: "CONFIRMED" | "UNKNOWN" | "NONE"; asOf?: string | null };
  state:
    | "ENTRY_ZONE"
    | "HOLDING"
    | "STOP_LOSS"
    | "TAKE_PROFIT"
    | "OBSERVING"
    | "UNKNOWN";
  basis: "CONFIRMED_NAV" | "ESTIMATE_1430" | null;
  asOf: string | null;
  reason: string;
}
export interface StudyVersion {
  id: string;
  version: number;
  generation: number;
  snapshot: StudySnapshot;
  reason?: string;
  createdAt: string;
}
export interface StudyCheck {
  id: string;
  versionId: string;
  generation: number;
  evaluatedAt: string;
  result: {
    state: string;
    quality: string;
    baseline: boolean;
    hasChange: boolean;
    stateAsOf?: string | null;
    conditionResults: Array<{
      id: string;
      metric: string;
      result: string;
      value: number | null;
      unit?: string;
      asOf?: string | null;
      reason?: string;
    }>;
    observations?: StudyObservation[];
  };
}
export interface StudyNote {
  id: string;
  versionId: string;
  kind: "DECISION" | "REVIEW" | "ACK" | "LIFECYCLE";
  payload: {
    operation?: string;
    enabled?: boolean;
    action?: string;
    verdict?: string;
    reason?: string;
    lessons?: string;
    errorCategories?: string[];
  };
  createdAt: string;
}
export interface EligibleStudyTrade {
  id: string;
  fillId: string;
  instrumentId: string;
  instrumentName: string;
  side: string;
  quantity: string;
  unitPrice: string;
  fee: string;
  currency?: string | null;
  tradeDate: string;
  status: "CONFIRMED";
  executionTimePrecision: "DAY";
}
export interface StudyTradeLink {
  id: string;
  versionId: string;
  fillId: string;
  linkedAt: string;
  reason?: string;
  executionTimePrecision: "DAY";
  timingRelation: "BEFORE_TRADE" | "POST_HOC" | "UNKNOWN";
  fill: EligibleStudyTrade;
}
export interface StudyDetail {
  priceWatch?: StudyPriceWatch | null;
  excursions?: {
    state?: string;
    mfePct: string | null;
    maePct: string | null;
    baseAsOf: string | null;
    lastAsOf: string | null;
    sampleCount: number | null;
    priceBasis: string | null;
    unit: string | null;
    reasons?: string[];
  } | null;
  track: StudyTrack;
  version: StudyVersion;
  versions: StudyVersion[];
  checks: StudyCheck[];
  notes: StudyNote[];
  tradeLinks: StudyTradeLink[];
  history: { limit: number; offset: number; hasMore: boolean };
  capabilities: { canEdit: boolean; canCheck: boolean; canLinkTrade: boolean };
  review?: {
    observations: Array<{
      metric: string;
      initialValue: number | null;
      currentValue: number | null;
      initialAsOf?: string;
      currentAsOf?: string;
      change: number | null;
      unit?: string;
      state: string;
    }>;
    performance: {
      state: string;
      netReturn: null | number;
      excessReturn: null | number;
      maxDrawdown: null | number;
      opportunityCost: null | number;
      reasons: string[];
    };
    causalAttribution: string;
  };
}
export type CreateStudy =
  | {
      originType: "DAILY_QUANT_SCAN";
      reportRunId: string;
      subjectKind: "fund" | "nav";
      subjectId: string;
      planTrack: PlanTrack;
      horizonDays?: number;
    }
  | {
      originType: "DAILY_DIRECTION";
      reportRunId: string;
      valueDirectionKey: string;
      subjectKind: "index" | "fund" | "nav";
      subjectId: string;
      planTrack: PlanTrack;
      horizonDays?: number;
    }
  | {
      originType: "DAILY_EVENT";
      reportRunId: string;
      eventId: string;
      scope?: StudyScope;
      horizonDays?: number;
    }
  | {
      originType: "SELECTION";
      scope: StudyScope;
      horizonDays?: number;
      planTrack?: PlanTrack;
    };
export interface StudyVersionRequest {
  expectedGeneration: number;
  reason: string;
  horizonDays?: number;
  conditions?: StudyCondition[];
  thesis?: string;
  transmission?: string[];
  counterFactors?: string[];
  watchFor?: string[];
  invalidationConditions?: string[];
}
export interface StudyNoteRequest {
  expectedGeneration: number;
  versionId?: string;
  kind: "DECISION" | "REVIEW" | "ACK";
  action?: "WATCH" | "PLAN_BUY" | "PLAN_SELL" | "HOLD" | "IGNORE";
  verdict?: "SUPPORTED" | "MIXED" | "REFUTED" | "INCONCLUSIVE";
  reason?: string;
  lessons?: string;
  errorCategories?: string[];
  checkId?: string;
}
const base = "/v1/investment/study-tracks";
async function read<T>(path: string, signal?: AbortSignal) {
  return (await request<T>(base + path, { signal })).data;
}
async function write(path: string, body: unknown) {
  return (
    await request<StudyDetail>(base + path, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify(body),
    })
  ).data;
}
export const getStudyTracks = (signal?: AbortSignal) =>
  read<{ items: StudyTrack[]; total: number }>("", signal);
export const getStudyDetail = (id: string, signal?: AbortSignal, offset = 0) =>
  read<StudyDetail>(
    `/${encodeURIComponent(id)}?limit=50&offset=${offset}`,
    signal,
  );
export const createStudyTrack = (body: CreateStudy) => write("", body);
export const updateStudyVersion = (id: string, body: StudyVersionRequest) =>
  write(`/${encodeURIComponent(id)}/versions`, body);
export const setStudyStatus = (
  id: string,
  body: { expectedGeneration: number; enabled: boolean },
) => write(`/${encodeURIComponent(id)}/status`, body);
export const checkStudy = (id: string, expectedGeneration: number) =>
  write(`/${encodeURIComponent(id)}/check`, { expectedGeneration });
export const addStudyNote = (id: string, body: StudyNoteRequest) =>
  write(`/${encodeURIComponent(id)}/notes`, body);
export const getEligibleStudyTrades = (id: string, signal?: AbortSignal) =>
  read<{ items: EligibleStudyTrade[]; total: number }>(
    `/${encodeURIComponent(id)}/eligible-trades`,
    signal,
  );
export const linkStudyTrade = (
  id: string,
  body: {
    expectedGeneration: number;
    fillId: string;
    versionId?: string;
    reason: string;
  },
) => write(`/${encodeURIComponent(id)}/trade-links`, body);
