import { request } from "./client";
export interface RegistryTopic {
  topicId: string;
  key: string;
  title: string;
  summary: string;
  unknowns?: string[];
  followUp?: string[];
  insightCategory?: "DYNAMIC" | "INDUSTRY" | "MULTI_SCOPE";
  evidenceCoverageScore?: number;
  status: "FORMAL" | "OBSERVATION_ONLY" | "PENDING_REVIEW" | "QUARANTINED";
  reasonCode: string;
  reason: string;
  impact: string;
  measures: string[];
  pendingTasks: Array<{
    taskId: string;
    industryId?: string;
    kind: string;
    query: string;
    searchTerm?: string;
    status: string;
    window?: { start: string; end: string; lookbackDays: number };
  }>;
  sourceIds: string[];
  match: {
    status:
      "STANDARD" | "STRICT_ALIAS" | "COMPOSITE" | "NEW_TOPIC" | "UNMATCHED";
    industryIds: string[];
    relatedIndustryIds: string[];
    evidence: string[];
    registryVersion: string;
    scopeVerified?: boolean | null;
    unresolvedClaims?: string[];
    proposedScopes?: Array<{
      industryId: string;
      searchTerm: string;
      registryVersion: string;
    }>;
  };
  investmentEligible: boolean;
}
export interface TopicRegistryPage {
  researchId: string;
  snapshotId: string;
  validatedAt: string;
  revision: string | null;
  revisionStatus?: "AVAILABLE" | "UNAVAILABLE";
  registryAvailable?: true;
  actionable: false;
  items: RegistryTopic[];
  highlights?: {
    version: "topic-display-priority-v1";
    limit: 10;
    catalogCount: number;
    actionable: false;
    items: Array<{
      topic: RegistryTopic;
      priority: {
        importance: "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN";
        checkedSourceCoveragePercent: number | null;
        scopeVerified: boolean;
        researchProgress: RegistryTopic["status"];
        reasons: string[];
      };
    }>;
  };
  totalCount: number;
  filteredCount?: number;
  nextCursor: string | null;
  hasMore?: boolean;
  coverage: {
    totalCount: number;
    discoveredTopicCount?: number;
    evidenceBackedTopicCount?: number;
    pendingVerificationTopicCount?: number;
    evidenceBackedTopicRatioPercent?: number | null;
    evaluableFundDirectionCount?: number;
    qualifiedFormalDirectionCount?: number;
    researchDeliveryStatus?:
      "VALIDATED_READ_ONLY" | "OBSERVATION_READ_ONLY" | "UNAVAILABLE";
    investmentQualificationStatus?:
      "QUALIFIED_CANDIDATES" | "NO_QUALIFIED_OPPORTUNITIES" | "BLOCKED";
    formalEligibilityReasons?: Partial<
      Record<
        | "NO_FORMAL_PROPOSAL"
        | "FUND_PLAN_MISSING"
        | "SOURCE_EVIDENCE_PENDING"
        | "INDUSTRY_IDENTITY_PENDING"
        | "INDUSTRY_AUDIT_PENDING"
        | "FORMAL_VALIDATION_FAILED"
        | "PUBLICATION_SAFETY_BLOCKED",
        number
      >
    >;
    validEventCount?: number;
    validUpcomingCount?: number;
    unresolvedReferenceCount?: number;
    storedCount?: number;
    countsByStatus?: Record<string, number>;
    plannedScopeCount: number;
    reviewedScopeCount: number;
    pendingScopeCount: number;
    searchExecutionVerified?: boolean;
    executionMatchStatus?:
      | "EXECUTION_UNMATCHED"
      | "EXECUTION_MATCHED"
      | "EXECUTION_PARTIALLY_MATCHED"
      | "EXECUTION_NOT_OBSERVED"
      | "EXECUTION_UNVERIFIED";
    executionPlannedTaskCount?: number;
    executionMatchedTaskCount?: number;
    executionObservedQueryCount?: number | null;
    executionUnmatchedQueryHashCount?: number | null;
    executionPendingDirectionCount?: number;
    executionReceiptTransportReason?:
      "RECEIPT_SUMMARIZED_SIZE_LIMIT" | "RECEIPT_OMITTED_SIZE_LIMIT";
    structureCompleteScopeCount?: number;
    executionVerifiedScopeCount?: number;
    evidenceVerifiedCheckCount?: number;
    executionVerifiedCheckCount?: number;
    strictRevalidated?: boolean;
    strictDirectionCount?: number;
    autoPublishAllowed?: boolean;
    autoPublishBlockerCount?: number;
    maxModelCalls?: number;
    additionalModelCalls?: number;
    maxInputBytes?: number;
    maxOutputBytes?: number;
    maxExecutionSeconds?: number;
    deferredScopeCount?: number;
    maxTopics?: number;
    maxFormalDirections?: number;
    maxScopes?: number;
    maxQueries?: number;
    maxSources?: number;
  };
}
export type TopicRegistryResult =
  | TopicRegistryPage
  | {
      researchId: string;
      registryAvailable: false;
      archiveState: "AUDIT_PENDING" | "ARCHIVING" | "UNRECOVERABLE";
      items: [];
      actionable: false;
      topicArchiveIssue?: string;
      reason?: string;
    };
export async function getResearchTopicRegistry(
  runId: string,
  options: {
    limit: 12 | 20 | 30;
    cursor?: string;
    includeHighlights?: boolean;
  },
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ limit: String(options.limit) });
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.includeHighlights) params.set("includeHighlights", "true");
  return (
    await request<TopicRegistryResult>(
      `/v1/investment/daily-research/topic-registry/${encodeURIComponent(runId)}?${params}`,
      { signal },
    )
  ).data;
}
