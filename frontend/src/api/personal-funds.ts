import { request } from "./client";
import type { ActionableZoneResult } from "./investment";
import type { FundNavHistory } from "./funds";
import type { StudyPriceWatch } from "./study-api";

export type FundRelationship =
  "HELD" | "TRACKED" | "WATCHLIST" | "RESEARCH_MENTIONED";
export interface PersonalFundReference {
  key: string;
  fundCode: string | null;
  instrumentId?: string | null;
  fundMarket: string;
  label: string;
  relationships: FundRelationship[];
  investmentEligible: false;
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
    policy: "AUTO_BACKFILL_AND_REPRICE";
    states: Record<string, { state: string; reason?: string }>;
  };
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
