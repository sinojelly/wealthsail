import { request, requestWithTimeout } from "./client";
import type { ActionableZoneResult } from "./investment";
import type { FundNavHistory } from "./funds";
import type { StudyPriceWatch } from "./study-api";

export type FundRelationship =
  "HELD" | "TRACKED" | "WATCHLIST" | "RESEARCH_MENTIONED";
export interface HoldingProfit {
  state: "AVAILABLE" | "COST_UNAVAILABLE" | "PRICE_UNAVAILABLE";
  shares: string | null;
  totalCost: string | null;
  unitCost: string | null;
  costAsOf: string | null;
  latestNav: string | null;
  priceAsOf: string | null;
  priceBasis: "CONFIRMED_NAV" | "ESTIMATED_NAV" | null;
  profitPct: string | null;
  profitAmount: string | null;
}
export interface PersonalFundReference {
  key: string;
  fundCode: string | null;
  instrumentId?: string | null;
  fundMarket: string;
  label: string;
  relationships: FundRelationship[];
  investmentEligible: false;
  holdingProfit?: HoldingProfit | null;
  confirmedNav: StudyPriceWatch["confirmedNav"];
  currentEstimate:
    | (NonNullable<StudyPriceWatch["currentEstimate"]> & {
        sourceVerification?: "LOCAL_RECORD_ONLY" | "PUBLIC_SOURCE";
      })
    | null;
  plan: ActionableZoneResult | null;
  history: FundNavHistory | null;
  comments?: Array<{
    title: string;
    thesis: string;
    risks: Array<{ summary: string; sourceIds?: string[] }>;
  }>;
  missingReasons: string[];
}
export interface PersonalFundsReference {
  version: "personal-funds-reference-v1";
  source: "LOCAL_DATABASE_ONLY";
  readOnly: true;
  asOf: string;
  funds: PersonalFundReference[];
  navSync?: {
    policy:
      "AUTO_BACKFILL_AND_REPRICE" | "LOCAL_DATABASE_ONLY" | "EXPLICIT_REFRESH";
    states: Record<string, { state: string; reason?: string }>;
  };
}
export async function refreshPersonalFundsReference(signal?: AbortSignal) {
  return (
    await requestWithTimeout<PersonalFundsReference>(
      "/v1/investment/personal-funds-reference/refresh",
      {
        method: "POST",
        signal,
      },
      120_000,
    )
  ).data;
}
export async function getPersonalFundsReference(
  includeResearchMentions: boolean,
  signal?: AbortSignal,
) {
  return (
    await request<PersonalFundsReference>(
      `/v1/investment/personal-funds-reference?includeResearchMentions=${includeResearchMentions}`,
      { signal },
    )
  ).data;
}
