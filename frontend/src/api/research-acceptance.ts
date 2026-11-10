import { request } from "./client";
import type {
  ActionableZoneResult,
  DeterministicRepair,
  IndustryCycle,
} from "./investment";

export type AcceptanceIndustryRisk = Partial<
  Pick<
    IndustryCycle,
    | "stage"
    | "label"
    | "riskLevel"
    | "riskFlags"
    | "confirmedRiskFlags"
    | "suspectedRiskFlags"
    | "evidenceGaps"
    | "evidenceSummary"
  >
> & { persistedRiskFlags?: string[] };

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
  phase?: "PLANNED" | "ANNOUNCED" | "IMPLEMENTED" | "REPORTED" | "OPINION";
  importance?: "HIGH" | "MEDIUM" | "LOW";
  channels?: string[];
  scenarios?: Array<{ condition: string; impact: string; counter: string }>;
  unknowns?: string[];
  watchFor?: string[];
  sourceIds?: string[];
  scheduledAt?: string | null;
  scheduledDate?: string | null;
  industryCycle?: AcceptanceIndustryRisk | null;
  relatedTopicKeys?: string[];
  relatedFormalDirectionKeys?: string[];
  relatedMacroConcepts?: string[];
  candidateConceptLabels?: string[];
  referenceResolution?: {
    version: "research-event-links-v1";
    resolved: Array<{
      namespace: "TOPIC" | "FORMAL_DIRECTION" | "LEGACY_DIRECTION";
      inputKey: string;
      key: string;
      method: "EXACT" | "CANONICAL";
    }>;
    unresolved: Array<{
      namespace: "TOPIC" | "FORMAL_DIRECTION" | "LEGACY_DIRECTION";
      key: string;
      reasonCode: "UNKNOWN_REFERENCE" | "AMBIGUOUS_REFERENCE";
    }>;
  };
}

export interface ResearchModelExecution {
  state: "QUEUED" | "RUNNING" | "TERMINATING" | "EXITED" | "UNKNOWN";
  dispatchStarted: boolean | null;
  terminationRequested: boolean | null;
  reaped: boolean | null;
  descendantsConfirmed: boolean | null;
  exitConfirmed: boolean | null;
  terminationReason: string | null;
  queuedAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
  elapsedMs: number | null;
}

export interface ResearchWorkload {
  version: "research-workload-v1" | "research-workload-v2";
  modelElapsedScope?: "MODEL_OPERATION";
  executionStatistics?: import("./research-progress").ResearchExecutionStatistics;
  executionProgress?: import("./research-progress").ResearchProgress;
  modelExecution?: ResearchModelExecution;
  promptBytes?: number;
  modelElapsedMs?: number;
  sourceVerificationElapsedMs?: number;
  inputTokens?: number;
  cachedInputTokens?: number;
  outputTokens?: number;
  executionCoverage: "COMPLETE" | "PARTIAL" | "UNAVAILABLE";
  observedWebToolCalls: number | null;
  observedSearchQueries: number | null;
  observedPageOpens: number | null;
  observedPageFinds: number | null;
  preDispatchEnforcement: false;
  providerRetries: null;
  searchSoftLimitExceeded: boolean | null;
}

export interface ResearchAcceptanceOutcome {
  id: string;
  researchId: string;
  kind: "INITIAL" | "REPLAY";
  decision: "COMPLETE" | "PARTIAL" | "EMPTY" | "REJECTED";
  validatorVersion: string;
  validatorRevision?: string | null;
  buildCommit?: string | null;
  revisionStatus?: "AVAILABLE" | "UNAVAILABLE";
  validatedAt: string;
  recordedAt?: string;
  formalPublishable: boolean;
  repairedFormalPublished?: boolean;
  deterministicRepair?: DeterministicRepair;
  researchWorkload?: ResearchWorkload;
  actionable: false;
  issueCount: number;
  issueListComplete: boolean;
  acceptedContentComplete?: boolean;
  canInspectArtifacts: boolean;
  canReplay: boolean;
  archiveState?: "AUDIT_PENDING" | "ARCHIVING" | "ARCHIVED" | "UNRECOVERABLE";
  archiveMessage?: string;
  archivePending?: boolean;
  recoverableMaterials?: Partial<
    Record<"PACKET" | "RAW_MODEL" | "SOURCE_CHECKS", boolean>
  >;
  counts: Record<
    string,
    { inputCount: number; acceptedCount: number; quarantinedCount: number }
  >;
  accepted: {
    macroOverview?: {
      summary?: string;
      macroCycle?: string;
      coverage: Array<{ topic: string; status: string; summary: string }>;
      verification: "MODEL_UNVERIFIED";
      sourceIds: string[];
    };
    sources: Array<{
      id: string;
      title: string;
      url?: string;
      quote?: string;
      verification?: string;
      publishedDate?: string | null;
      publishedAt?: string | null;
      retrievedAt?: string | null;
    }>;
    industryCycleReviews: Array<{ industry: string } & AcceptanceIndustryRisk>;
    valueDirections: AcceptanceObservation[];
    mentionedFunds?: Array<{
      fundCode: string;
      name?: string;
      label?: string;
      directionKey?: string;
      directionTitle?: string;
      actionableZones?: ActionableZoneResult | null;
    }>;
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
