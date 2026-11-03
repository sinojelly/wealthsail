import { request } from "./client";
import type {
  AcceptanceIndustryRisk,
  ResearchAcceptanceOutcome,
} from "./research-acceptance";

export interface RepairQuarantine {
  itemType: string;
  itemIndex?: number | null;
  ruleId?: string;
  reason: string;
  title?: string;
  summary?: string;
  importance?: "HIGH" | "MEDIUM" | "LOW";
  mandatory?: boolean;
  catalysts?: Array<{ summary: string; sourceIds: string[] }>;
  industryCycle?: AcceptanceIndustryRisk | null;
  risks?: Array<{ summary: string; sourceIds: string[] }>;
  watchFor?: string[];
  unknowns?: string[];
  channels?: string[];
  scenarios?: Array<{ condition: string; impact: string; counter: string }>;
  sourceIds?: string[];
  sourceEvidence?: ResearchAcceptanceOutcome["accepted"]["sources"];
  path?: string;
  businessKey?: string;
  dependencies?: unknown[];
}
export interface HistoricalRepair {
  id?: string;
  sourceRunId: string;
  kind: "HISTORICAL_REPAIR";
  state: "PREVIEW" | "DRAFT" | "APPROVED" | "REJECTED" | "PUBLISHED";
  publicationScope: "HISTORICAL_READ_ONLY";
  actionable: false;
  validatedAt: string;
  createdAt?: string;
  currentFreshness: "EXPIRED" | "HISTORICAL_ONLY";
  originalStatus: "FAILED";
  decision: "NO_CHANGE" | "BLOCKED" | "REPAIRED";
  strictPass: boolean;
  autoPublishAllowed: boolean;
  historicalPublishAllowed: boolean;
  safetyBlocked: boolean;
  repairVersion: string;
  repairRevision?: string | null;
  buildCommit?: string | null;
  originalOutcome?: Partial<ResearchAcceptanceOutcome>;
  accepted: ResearchAcceptanceOutcome["accepted"];
  acceptedContentComplete?: boolean;
  canGenerate?: boolean;
  canInspectRepairDetails?: boolean;
  canReview: boolean;
  canReject?: boolean;
  canPublish: boolean;
  creatorId?: string;
  reviewerId?: string | null;
  quarantined?: RepairQuarantine[];
  blockers?: RepairBlocker[];
  autoBlockers?: RepairBlocker[];
  hardBlockers?: RepairBlocker[];
  blockedReasons?: string[];
  retained?: { counts: Record<string, unknown>; directionKeys: string[] };
  sourceDigests?: Record<string, string>;
}
export interface RepairBlocker {
  code: string;
  reason: string;
  itemType?: string;
  itemIndex?: number | null;
  path?: string;
}
export interface HistoricalRepairDetails {
  id: string;
  sourceRunId: string;
  state: HistoricalRepair["state"];
  validatedAt: string;
  createdAt: string;
  publicationScope: "HISTORICAL_READ_ONLY";
  actionable: false;
  candidateDigest: string;
  sourceDigests: Record<string, string>;
  provenance: Record<string, unknown>;
  privateResult: Record<string, unknown>;
}
const base = "/v1/investment/daily-research/history-repairs";
const read = async <T>(path: string, signal?: AbortSignal) =>
  (await request<T>(base + path, { signal })).data;
const write = async (path: string, body: unknown) =>
  (
    await request<HistoricalRepair>(base + path, {
      method: "POST",
      body: JSON.stringify(body),
    })
  ).data;
export const previewHistoricalRepair = (
  sourceRunId: string,
  signal?: AbortSignal,
) =>
  read<HistoricalRepair>(
    `/from/${encodeURIComponent(sourceRunId)}/preview`,
    signal,
  );
export const listHistoricalRepairs = (
  sourceRunId?: string,
  signal?: AbortSignal,
) =>
  read<{ items: HistoricalRepair[]; canGenerate?: boolean }>(
    sourceRunId ? `?sourceRunId=${encodeURIComponent(sourceRunId)}` : "",
    signal,
  );
export const getHistoricalRepair = (id: string, signal?: AbortSignal) =>
  read<HistoricalRepair>(`/${encodeURIComponent(id)}`, signal);
export const getHistoricalRepairDetails = (id: string, signal?: AbortSignal) =>
  read<HistoricalRepairDetails>(`/${encodeURIComponent(id)}/details`, signal);
export const generateHistoricalRepair = (
  sourceRunId: string,
  commandKey: string,
) => write(`/from/${encodeURIComponent(sourceRunId)}`, { commandKey });
export const reviewHistoricalRepair = (
  id: string,
  commandKey: string,
  decision: "APPROVE" | "REJECT",
) => write(`/${encodeURIComponent(id)}/reviews`, { commandKey, decision });
export const publishHistoricalRepair = (id: string, commandKey: string) =>
  write(`/${encodeURIComponent(id)}/publish`, { commandKey });
