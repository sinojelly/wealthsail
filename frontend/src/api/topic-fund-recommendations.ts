import { request } from "./client";

export type TopicRiskStatus =
  "VERIFIED_SAFE" | "RISK_ALERT" | "UNVERIFIED_RISK";
export type FundMatchLevel = "CORE_MATCH" | "DYNAMIC_MATCH" | "BROAD_FALLBACK";

export interface TopicRecommendedFund {
  fundCode: string;
  name: string;
  matchLevel: FundMatchLevel;
  matchReason: string;
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
  recommendedFunds: TopicRecommendedFund[];
  recommendationStatus: "MATCHED" | "NO_MATCH" | "SOURCE_PENDING";
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
