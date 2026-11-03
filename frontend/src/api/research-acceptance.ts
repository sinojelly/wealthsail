import { request } from "./client";
import type { IndustryCycle } from "./investment";

export interface AcceptanceIssue {
  issueId: string;
  researchId: string;
  itemType:
    | "REPORT"
    | "SOURCE"
    | "INDUSTRY_REVIEW"
    | "VALUE_DIRECTION"
    | "FUND_PLAN"
    | "EVENT"
    | "UPCOMING";
  itemIndex?: number | null;
  path?: string | null;
  ruleId?: string | null;
  reasonCode?: string | null;
  disposition: "QUARANTINED" | "BLOCKED" | "REPAIRED";
  message: string;
  businessKey?: string | null;
  dependencies?: Array<string | { itemType: string; businessKey: string }>;
  missingIndustryCodes?: string[];
}

export interface AcceptanceObservation {
  id?: string;
  key?: string;
  title?: string;
  thesis?: string;
  summary?: string;
  watchFor?: string[];
  sourceIds?: string[];
  scheduledAt?: string | null;
  scheduledDate?: string | null;
  industryCycle?: IndustryCycle | null;
}

export interface ResearchAcceptanceOutcome {
  id: string;
  researchId: string;
  kind: "INITIAL" | "REPLAY";
  decision: "COMPLETE" | "PARTIAL" | "EMPTY" | "REJECTED";
  validatorVersion: string;
  validatedAt: string;
  recordedAt?: string;
  formalPublishable: boolean;
  actionable: false;
  issueCount: number;
  issueListComplete: boolean;
  acceptedContentComplete?: boolean;
  canInspectArtifacts: boolean;
  canReplay: boolean;
  counts: Record<
    string,
    { inputCount: number; acceptedCount: number; quarantinedCount: number }
  >;
  accepted: {
    sources: Array<{
      id: string;
      title: string;
      url?: string;
      quote?: string;
      verification?: string;
    }>;
    industryCycleReviews: Array<{
      industry: string;
      evidenceSummary?: string;
      stage?: string;
      riskLevel?: string;
    }>;
    valueDirections: AcceptanceObservation[];
    events: AcceptanceObservation[];
    upcoming: AcceptanceObservation[];
  };
  issues?: AcceptanceIssue[];
}

export interface ResearchArtifacts {
  researchId: string;
  validatedAt?: string;
  complete: boolean;
  items: Array<{
    kind: "PACKET" | "RAW_MODEL" | "SOURCE_CHECKS";
    content: string | null;
    sha256?: string;
    byteLength?: number;
    omissionReason?: string | null;
  }>;
}

const path = (researchId: string) =>
  `/v1/investment/daily-research/validation/${encodeURIComponent(researchId)}`;

export async function getResearchAcceptance(
  researchId: string,
  signal?: AbortSignal,
) {
  return (
    await request<ResearchAcceptanceOutcome>(path(researchId), { signal })
  ).data;
}

export async function getResearchArtifacts(
  researchId: string,
  signal?: AbortSignal,
) {
  return (
    await request<ResearchArtifacts>(`${path(researchId)}/artifacts`, {
      signal,
    })
  ).data;
}

export async function getResearchReplays(
  researchId: string,
  signal?: AbortSignal,
) {
  return (
    await request<{ items: ResearchAcceptanceOutcome[] }>(
      `${path(researchId)}/replays`,
      { signal },
    )
  ).data;
}

export async function replayResearchAcceptance(
  researchId: string,
  commandKey: string,
) {
  return (
    await request<ResearchAcceptanceOutcome>(`${path(researchId)}/replays`, {
      method: "POST",
      body: JSON.stringify({ commandKey }),
    })
  ).data;
}
