import { request } from "./client";
import type { ActionableZoneResult } from "./investment";
import type { FundNavHistory } from "./funds";

export type TopicRiskStatus =
  "VERIFIED_SAFE" | "RISK_ALERT" | "UNVERIFIED_RISK";
export type FundMatchLevel = "CORE_MATCH" | "DYNAMIC_MATCH" | "BROAD_FALLBACK";
export type TopicPolarity =
  | "POSITIVE_OPPORTUNITY"
  | "NEGATIVE_RISK"
  | "MIXED"
  | "MACRO_OBSERVATION"
  | "UNDETERMINED";
export type FundAssociationLevel =
  "DIRECT_INDEX" | "CATEGORY_MATCH" | "NAME_CANDIDATE" | "BROAD_FALLBACK";

export interface TopicRecommendedFund {
  actionableZones?: ActionableZoneResult | null;
  history?: FundNavHistory | null;
  confirmedNav?: {
    value: string | number | null;
    asOf: string | null;
    changePct?: string | number | null;
  } | null;
  fundCode: string;
  name: string;
  matchLevel: FundMatchLevel;
  matchReason: string;
  associationLevel?: FundAssociationLevel;
  associationReason?: string;
  alternativeShareClasses?: Array<{ fundCode: string; name: string }>;
  salesChannel?: "OFF_EXCHANGE" | "EXCHANGE" | "UNKNOWN";
  pricePosition?: {
    status:
      | "ENTRY_ZONE"
      | "NEAR_ENTRY_ZONE"
      | "NEUTRAL"
      | "OVERHEATED"
      | "DATA_PENDING";
    distanceToEntryPct?: number;
    label: string;
    asOf?: string | null;
    reasons: string[];
    actionable: false;
  };
  subscription?: { status: string; restricted: boolean | null } | null;
  quantitativeScore?: number | null;
  unadjustedQuantitativeScore?: number;
  missingEvidence?: string[];
  riskWarnings?: string[];
  sourceUri?: string | null;
  canSubscribe?: boolean;
  canBuyNow?: false;
  actionable?: false;
}

export interface TopicFundRecommendation {
  topicId: string;
  key: string;
  title: string;
  thesis: string;
  sourceIds: string[];
  riskStatus: TopicRiskStatus;
  riskScorePenalty: number;
  riskWarnings: string[];
  topicPolarity?: TopicPolarity;
  polarityReasons?: string[];
  recommendedFunds: TopicRecommendedFund[];
  relatedRiskFunds?: TopicRecommendedFund[];
  indirectFunds?: TopicRecommendedFund[];
  observationFunds?: TopicRecommendedFund[];
  priority?: {
    importance?: "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN";
    checkedSourceCoveragePercent?: number | null;
    checkedSourceCount?: number;
    scopeVerified?: boolean;
    researchProgress?: string;
    reasons?: string[];
  };
  recommendationStatus:
    | "MATCHED"
    | "NO_MATCH"
    | "SOURCE_PENDING"
    | "RISK_ONLY"
    | "OBSERVATION_ONLY"
    | "INDIRECT_ONLY";
  matchMessage?: string | null;
  missingEvidence: string[];
  actionable: false;
}

export interface TopicFundRecommendationsPage {
  researchId: string;
  validatedAt: string;
  evaluatedAt: string;
  status?: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE";
  archiveState?: string;
  missingEvidence?: string[];
  items: TopicFundRecommendation[];
  coverage: {
    topicCount: number;
    matchedTopicCount: number;
    recommendedFundCount: number;
  };
  actionable: false;
}

export async function getTopicFundRecommendations(
  runId: string,
  signal?: AbortSignal,
) {
  return (
    await request<TopicFundRecommendationsPage>(
      `/v1/investment/daily-research/topic-fund-recommendations/${encodeURIComponent(runId)}`,
      { signal },
    )
  ).data;
}
