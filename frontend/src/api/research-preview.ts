import { request } from "./client";
import type {
  ActionableZoneResult,
  Analysis,
  DailyResearch,
  OpportunityContext,
} from "./investment";
import type { RegistryTopic } from "./research-topic-registry";

export interface PreviewFund {
  key: string;
  subjectKind: "fund" | "nav";
  subjectId: string;
  fundCode: string | null;
  market: string | null;
  label: string;
  directionKeys: string[];
  investmentEligible: false;
  formalEligibilityReasons?: string[];
  originalProposals: Array<{
    directionKey: string;
    track?: string;
    note?: string;
  }>;
  anchor: ActionableZoneResult | null;
  analysis: Analysis | null;
  missingReasons: string[];
}
export interface ResearchPreview {
  version: "research-preview-v1";
  mode: "HISTORICAL_SIMULATION";
  runId: string;
  validatedAt: string;
  generatedAt: string;
  validatorVersion: string;
  readOnly: true;
  investmentEligible: false;
  state: "COMPLETE" | "PARTIAL";
  sourceDigests: Record<string, string>;
  report: DailyResearch["report"];
  opportunityContext: OpportunityContext;
  topicRegistry: {
    items: RegistryTopic[];
    coverage?: Record<string, unknown>;
  } | null;
  funds: PreviewFund[];
  missingReasons: string[];
}
export async function getResearchPreview(runId: string, signal?: AbortSignal) {
  return (
    await request<ResearchPreview>(
      `/v1/operations/research-preview/${encodeURIComponent(runId)}`,
      { signal },
    )
  ).data;
}
