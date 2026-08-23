export type DecimalString = string;
export type QualityCode = "FRESH" | "ESTIMATED" | "STALE" | "MISSING";

export interface ApiEnvelope<T> {
  data: T;
  requestId: string;
  generatedAt: string;
}

export interface SettlementPreviewDto {
  fillId: string;
  cashBefore: DecimalString;
  cashDelta: DecimalString;
  cashAfter: DecimalString;
}

export interface RiskPreviewDto {
  previousMaxPosition: DecimalString;
  nextMaxPosition: DecimalString;
  impactedHoldings: number;
  newAlerts: number;
  policyHash: string;
}
