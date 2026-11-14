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

export interface MacroCycleInsights {
  version?: string;
  globalLiquidity?: MacroCycleInsight;
  domesticCycle?: MacroCycleInsight;
  marketRegime?: MacroCycleInsight;
  keyFactors?: MacroCycleInsight[];
  industries?: MacroCycleInsight[];
}
