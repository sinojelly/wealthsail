import type { MacroContext, OpportunitySeries } from "./investment";

export interface MacroCycleInsight {
  title?: string;
  stage?: string;
  summary?: string;
  impact?: string;
  action?: string;
  driver?: string;
  industryId?: string;
  label?: string;
  evidenceKind?: "OBSERVED_FACT" | "MARKET_EXPECTATION" | "MODEL_ASSESSMENT";
  factVerification?: string;
  asOf?: string | null;
  sourceIds?: string[];
  importance?: "HIGH" | "MEDIUM" | "LOW";
}

export interface MacroPolicySignal {
  text: string;
  title: string;
  sourceUri?: string | null;
  asOf?: string | null;
  kind: string;
}

export interface MacroCycleInsights {
  version?: string;
  globalLiquidity?: MacroCycleInsight;
  domesticCycle?: MacroCycleInsight;
  marketRegime?: MacroCycleInsight;
  keyFactors?: MacroCycleInsight[];
  industries?: MacroCycleInsight[];
  macroSnapshot?: { items: MacroContext["items"]; series: OpportunitySeries[] };
  policySignals?: MacroPolicySignal[];
  rateExpectation?: {
    state: string;
    asOf?: string | null;
    spreadBp?: number | null;
    us2yChange5Bp?: number | null;
    us10yChange5Bp?: number | null;
    summary: string;
  };
}
