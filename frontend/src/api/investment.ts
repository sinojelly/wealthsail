import { request } from "./client";
import type { ResearchAcceptanceOutcome } from "./research-acceptance";
import type { TopicRegistryPage } from "./research-topic-registry";
export interface Direction {
  id: string;
  marketId: string;
  marketLabel: string;
  styleId: string;
  label: string;
  framework: string;
  macroResearch?: {
    summary: string;
    channels: Array<{
      label: string;
      evidenceCodes: string[];
      observation: string;
      impact: string;
      counterCase: string;
      watchFor: string[];
    }>;
    missingEvidence: string[];
    methodVersion: string;
  } | null;
  relatedEvents?: MacroEvent[];
}
export interface ResearchIndex {
  id: string;
  code: string;
  name: string;
  market: string;
  exposure: string;
  fundCount: number;
  state: string;
}
export interface ResearchFund {
  instrumentId: string | null;
  fundCode: string;
  fundName: string;
  fundType: string;
  tradeForm: "ETF" | "FUND";
  market: string;
  currency: string;
  latestValue: number | null;
  latestDate: string | null;
  latestValueBasis?: "UNIT_NAV" | "ACCUMULATED_NAV" | "ADJUSTED_PRICE" | null;
  navRangePosition: number | null;
  technicalScore: number | null;
  technicalState?: string | null;
  dataState: string;
  updatedAt: string | null;
  performanceState?: string | null;
  valueState?: string | null;
  valueEligible?: boolean | null;
  return63?: number | null;
  return252?: number | null;
  maxDrawdown?: number | null;
  volatility20?: number | null;
}
export interface SearchResult extends Partial<ResearchFund> {
  kind: "index" | "fund";
  id: string;
  code: string;
  name: string;
  indexId: string | null;
  directionId: string | null;
}
export interface MacroEvent {
  id: string;
  title: string;
  category: string;
  sourceType: string;
  publisher: string;
  sourceUri: string | null;
  publishedAt: string | null;
  retrievedAt: string | null;
  summary: string;
  directionIds: string[];
}
export interface MacroContext {
  state: string;
  months?: number;
  windowStart?: string;
  windowEnd?: string;
  research?: {
    methodVersion: string;
    summary: string;
    dimensions: Array<{
      id: string;
      label: string;
      state: string;
      summary: string;
      evidenceCodes: string[];
      missingEvidence: string[];
    }>;
    changes: Array<{ code: string; summary: string }>;
    limitations: string[];
  } | null;
  events?: MacroEvent[];
  eventState?: string;
  items: Array<{
    code: string;
    label: string;
    value: number | null;
    asOf: string | null;
    publishedAt: string | null;
    source: string | null;
    state: string;
    unit?: string | null;
    sourceUri?: string | null;
    historyMonths?: number;
    sampleCount?: number;
    periodChange?: number | null;
    trendState?: string;
    referencePeriod?: string | null;
    valueVerification?: "VERIFIED" | "PENDING" | "CONFLICT" | "UNAVAILABLE";
    publicationStatus?: "CONFIRMED" | "UNKNOWN";
    officialEvidence?: {
      publisher: string;
      sourceUri: string | null;
      value: number | null;
      publishedAt: string | null;
      retrievedAt: string | null;
    } | null;
    nextRelease?: {
      status: "SCHEDULED" | "OVERDUE" | "UNKNOWN";
      scheduledAt: string | null;
      referencePeriod: string | null;
      sourceUri: string | null;
    } | null;
    benchmark?: { value: number; label: string } | null;
    statistics?: {
      previousValue: number | null;
      change3Months: number | null;
      change6Months: number | null;
      percentile: number | null;
      median: number | null;
      sampleCount: number;
      firstPeriod: string | null;
      lastPeriod: string | null;
      missingPeriods: string[];
    } | null;
    interpretation?: {
      level: string;
      momentum: string;
      summary: string;
      limitations: string[];
      methodVersion: string;
    } | null;
    history?: Array<{
      asOf: string;
      value: number;
      unit?: string | null;
      source?: string | null;
      sourceUri?: string | null;
      state?: string;
      publishedAt?: string | null;
      retrievedAt?: string | null;
      revision?: number;
    }>;
  }>;
}
export interface PerformanceEvidence {
  state: string;
  eligible: boolean;
  return63: number | null;
  return252: number | null;
  maxDrawdown: number | null;
  volatility20: number | null;
  historyStart: string | null;
  historyEnd: string | null;
  sampleCount: number;
  reasons: string[];
}
export interface OpportunityScope {
  directionId?: string;
  indexId?: string;
  instrumentId?: string;
  fundCode?: string;
  fundMarket?: string;
  themeCode?: ThemeCode;
}
export type ThemeCode =
  | "HAINAN_FTP"
  | "PUDONG_DEV"
  | "GEO_CRUDE_OIL"
  | "AI_MEMORY_HBM"
  | "COMMERCIAL_SPACE";
export type ResearchThemeKey =
  ThemeCode | "UNCLASSIFIED" | `DISCOVERY:${string}`;
export interface StrategyMemoryRule {
  key: string;
  themeCode: ResearchThemeKey;
  category: string;
  ruleText: string;
  sampleCount: number;
  supportedCount: number;
  mixedCount: number;
  refutedCount: number;
  inconclusiveCount: number;
  supportRate: string | null;
  profitSampleCount: number;
  profitWinCount: number;
  profitLossCount: number;
  profitFlatCount: number;
  winRate: string | null;
  updatedAt: string;
}
export interface ThemeGraphRow {
  themeCode: ThemeCode;
  themeLabel: string;
  assetKind: string;
  assetCode: string;
  assetName: string;
  market: string | null;
  instrumentId?: string | null;
  relationshipType: string;
  sourceUri: string | null;
  metadata?: Record<string, unknown>;
}
export interface AlphaObservation {
  code: string;
  value: PlanPrice;
  unit?: string | null;
  referenceDate?: string | null;
  asOf?: string | null;
  retrievedAt?: string | null;
  publishedAt?: string | null;
  sourceUri?: string | null;
  sourceName?: string | null;
  state?: string;
}
export interface AlphaPublic {
  asOf?: string | null;
  observations?: AlphaObservation[];
  catalysts?: Array<{
    id: string;
    title: string;
    content?: string;
    publishedAt?: string | null;
    retrievedAt?: string | null;
    sourceUri?: string | null;
  }>;
  odds?: Array<{
    marketId: string;
    question: string;
    outcome: string;
    impliedProbabilityPct: PlanPrice;
    observedAt?: string | null;
    retrievedAt?: string | null;
    sourceUri?: string | null;
  }>;
  launches?: Array<{
    id: string;
    name: string;
    phase: string;
    scheduledAt?: string | null;
    retrievedAt?: string | null;
    sourceUri?: string | null;
  }>;
  sourceResults?: Record<
    string,
    {
      status: string;
      records?: number;
      retrievedAt?: string | null;
      limitations?: string[];
    }
  >;
}
export interface OpportunityQuery extends OpportunityScope {
  months: number;
}
export interface OpportunitySeries {
  code: string;
  label: string;
  frequency: "DAILY" | "MONTHLY" | "YTD";
  unit: string | null;
  value: number | null;
  asOf: string | null;
  sourceUri: string | null;
  sourceName: string | null;
  publishedAt: string | null;
  retrievedAt: string | null;
  availableAt: string | null;
  timeBasis: string | null;
  methodVersion: string | null;
  metadata?: {
    definitionKey?: string | null;
    definitionVersion?: string | null;
    accumulationStart?: string | null;
    accumulationEnd?: string | null;
  };
  state:
    "READY" | "STALE" | "CONFLICT" | "NOT_COLLECTED" | "PERMISSION_REQUIRED";
  changes: {
    previousValue: number | null;
    change5Samples: number | null;
    change20Samples: number | null;
    changeUnit: "bp" | "pp" | null;
    periodChange?: number | null;
    change3Months?: number | null;
    change6Months?: number | null;
    median: number | null;
    percentile: number | null;
    sampleCount: number;
  };
  history: Array<{
    date: string;
    value: number | null;
    state: string;
    unit?: string | null;
    sourceUri?: string | null;
    sourceName?: string | null;
    publishedAt?: string | null;
    retrievedAt?: string | null;
    availableAt?: string | null;
    timeBasis?: string;
    revision?: number;
    definitionKey?: string | null;
    definitionVersion?: string | null;
    metadata?: { definitionKey?: string; definitionVersion?: string };
  }>;
}
export interface MarketEvidence {
  indexId: string;
  label: string;
  state: string;
  historyQuality?: string;
  trendWindow?: { start: string; end: string; sampleCount: number } | null;
  valuation: {
    state: string;
    pe?: ValuationEvidence | null;
    pb?: ValuationEvidence | null;
    summary?: string;
  };
  trend: {
    state: string;
    ma200?: number | null;
    momentum63?: number | null;
    distanceMA200?: number | null;
  };
  risk: {
    state: string;
    volatility20?: number | null;
    maxDrawdown?: number | null;
  };
  earnings: { state: string; summary?: string };
  missingEvidence: string[];
  sourceUri: string | null;
  asOf: string | null;
}
export type PlanTrack = "LEFT_VALUE" | "RIGHT_TREND";
export type PlanPrice = number | string | null;
export interface OpportunityPriority {
  opportunityScore?: number;
  rewardRiskRatio?: PlanPrice;
  scoreBreakdown?: Record<string, unknown>;
  scoreMethodVersion?: string;
}
export interface EntrySafety {
  entryStatus?: string | null;
  entryStatusTone?: string | null;
  entryBlocked?: boolean;
  entryReady?: boolean;
  inBuyZone?: boolean;
  inSellZone?: boolean;
}
export interface ActionableZoneTrack extends OpportunityPriority, EntrySafety {
  track: PlanTrack;
  state: "CANDIDATE" | "WATCH" | "INSUFFICIENT";
  candidate?: boolean;
  entryZoneMin: PlanPrice;
  entryZoneMax: PlanPrice;
  stopLossPrice: PlanPrice;
  takeProfitT1: PlanPrice;
  takeProfitT2: PlanPrice;
  sellZoneMin?: PlanPrice;
  sellZoneMax?: PlanPrice;
  estimatedProfitMinPct?: PlanPrice;
  estimatedProfitMaxPct?: PlanPrice;
  currentToSellMaxPct?: PlanPrice;
  buyZoneAnchor?:
    | "MA60_HISTORICAL_SUPPORT"
    | "MA20_CAPPED_P65"
    | "SHORT_HISTORY_REFERENCE"
    | "LEFT_BOLLINGER_SUPPORT"
    | "TWO_YEAR_LOWER_SUPPORT"
    | "TWO_YEAR_LOW_SUPPORT";
  reasons: string[];
  priceState: string;
  missingEvidence?: string[];
}
export type IndustryRiskLevel = "NONE" | "UNPROVEN" | "SUSPECTED" | "CONFIRMED";
export type IndustryRiskKind =
  | "FINANCING_COOLING"
  | "LAYOFFS_CLOSURES"
  | "ORDER_CUTS"
  | "OVERCAPACITY_PRICE_WAR"
  | "PRODUCTION_DELAY";
export interface IndustryRiskCheck {
  kind: IndustryRiskKind;
  status: "FOUND" | "NOT_FOUND" | "UNVERIFIED";
  verification?: string;
  claimSupported?: boolean;
  industryScopeVerified?: boolean;
  strength?: IndustryRiskLevel;
  independentSourceCount?: number;
  summary: string;
  query: string;
  sourceIds: string[];
  recency?: string;
  timeVerification?: string;
  eventTimeVerification?: string;
  eventPeriod?: string | null;
  eventPeriodStart?: string | null;
  eventPeriodEnd?: string | null;
}
export interface IndustryEvidence {
  kind:
    | "PROFIT_GROWTH"
    | "CASH_COLLECTION"
    | "PAID_ORDERS"
    | "POLICY_EVENT"
    | "COMMERCIALIZATION_EARLY"
    | "EARNINGS_NOT_DELIVERED"
    | "EXPANSION_PLAN"
    | "FRAMEWORK_AGREEMENT";
  summary: string;
  sourceIds: string[];
  companyName?: string | null;
  entityId?: string | null;
  reportPeriod?: string | null;
  factIdentityVerified?: boolean;
  industryScopeVerified?: boolean;
}
export interface IndustryCycle {
  stage:
    | "EARNINGS_DELIVERY"
    | "POLICY_CATALYST"
    | "EARLY_COMMERCIALIZATION"
    | "UNKNOWN";
  label?: string;
  riskLevel?: IndustryRiskLevel;
  leftDiscountScale?: 0 | 0.5 | 1;
  earningsVerified?: boolean;
  earningsIndustryScopeVerified?: boolean | null;
  earningsEntity?: string | null;
  earningsReportPeriod?: string | null;
  forwardPlansOnly?: boolean;
  riskFlags?: string[];
  confirmedRiskFlags?: string[];
  suspectedRiskFlags?: string[];
  evidenceGaps?: string[];
  riskChecks?: IndustryRiskCheck[];
  auditStructureStatus?: "COMPLETE" | "INCOMPLETE";
  searchExecutionVerified?: boolean;
  evidenceSummary?: string;
  evidence?: IndustryEvidence[];
  sourceIds?: string[];
  industryRiskWarning?: string | null;
}
export interface RelativeStrength {
  state?: string;
  benchmark?: string;
  asOf?: string | null;
  /** Percentage points; -5.25 means underperforming by 5.25 points. */
  excessReturn20d: number | null;
  excessReturn60d: number | null;
  excessReturnUnit?: "PERCENTAGE_POINTS";
  /** Positive percentage decline from the 60-day high; 18.5 means down 18.5%. */
  drawdown60d: number | null;
  drawdownUnit?: "PERCENT";
  breakdownDate: string | null;
  weakening?: boolean | null;
  lowerHighs?: boolean | null;
  trendWeakening?: boolean | null;
  consecutiveWeakDays?: number | null;
  reasons?: string[];
  missingEvidence?: string[];
}
export interface ActionableZoneResult extends OpportunityPriority, EntrySafety {
  pricePosition?: {
    status:
      | "ENTRY_ZONE"
      | "NEAR_ENTRY_ZONE"
      | "NEUTRAL"
      | "OVERHEATED"
      | "DATA_PENDING";
    distanceToEntryPct?: number;
    label?: string;
    asOf?: string | null;
    reasons?: string[];
    actionable?: false;
  };
  industryCycle?: IndustryCycle | null;
  industryRiskWarning?: string | null;
  relativeStrength?: RelativeStrength | null;
  methodVersion: string;
  subjectKind: "index" | "fund" | "nav";
  subjectId: string;
  label: string;
  priceBasis: string;
  priceTimeBasis?: "LATEST_DISCLOSED_NAV";
  unit: string | null;
  asOf: string | null;
  sourceUri: string | null;
  currentPrice: PlanPrice;
  researchAsOf?: string | null;
  researchMethodVersion?: string | null;
  researchSourceUri?: string | null;
  priceRefresh?: {
    state: "READY" | "PARTIAL" | "UNAVAILABLE";
    calculatedAt?: string | null;
    navAsOf: string | null;
    methodVersion: string;
    reason?: string | null;
  };
  /** Percent return for currentPrice/asOf; 1.23 means 1.23%. */
  currentChangePct?: PlanPrice;
  series?: {
    state: string;
    fundCode: string;
    asOf: string | null;
    unit: string;
    priceBasis: string;
    sourceUri: string | null;
    retrievedAt: string | null;
    points: Array<{ date: string; value: string | number }>;
  };
  indicators: Record<string, PlanPrice>;
  historicalPriceWindow?: {
    state: string;
    requiredSamples: number;
    sampleCount: number;
    start: string | null;
    end: string | null;
  };
  tracks: ActionableZoneTrack[];
  selectedTrack?: PlanTrack;
  parameters?: Record<string, number | number[] | string>;
  orderPriceAvailable?: boolean;
  themeCode?: ResearchThemeKey | null;
  track?: PlanTrack;
  priceRevisionStatus?:
    "INITIAL" | "MAINTAINED" | "UPGRADED" | "DOWNGRADED" | "TAKE_PROFIT_ALERT";
  priceRevisionNote?: string | null;
  calibrationRuleKeys?: string[];
  calibrationNote?: string | null;
  fundCode?: string | null;
  valueDirectionKey?: string | null;
  valueDirectionTitle?: string | null;
  volatilityMethod?: string | null;
  market?: "FUND_CN" | "SSE" | null;
  subscriptionRestricted?: boolean;
  subscriptionLabel?: string | null;
  subscriptionStatus?: {
    purchaseStatus: string;
    dailyLimitCny: string | null;
    restricted: boolean;
    label: string | null;
    retrievedAt?: string;
  };
  preferredEntryEligible?: boolean;
}
export interface QuantitativeScanItem extends ActionableZoneResult {
  signals: Array<
    EntrySafety & {
      track: PlanTrack;
      triggerState: "CANDIDATE" | "WATCH";
      inEntryZone: boolean;
      candidate?: boolean;
      entryReady?: boolean;
      distanceToEntryPct: number;
    }
  >;
  discoverySector?: string;
  historyQuality?: string;
  historySampleCount?: number;
}
export interface QuantitativeScan {
  methodVersion: string;
  asOf: string | null;
  minimumHistorySamples: number;
  coverage: { pricedFundCount: number; targetHistoryFundCount: number };
  items: QuantitativeScanItem[];
}
export interface ValueDirection extends OpportunityPriority {
  auditStatus?: "AUDITED" | "UNAUDITED_SCOPE" | string;
  industryCycle?: IndustryCycle | null;
  industryRiskWarning?: string | null;
  key: string;
  title: string;
  thesis: string;
  signal: "LEFT_VALUE" | "RIGHT_TREND" | "TAKE_PROFIT" | "WATCH" | "AVOID";
  qualificationStatus?: "OBSERVATION_ONLY" | "PENDING_REVIEW" | "FORMAL";
  investmentEligible?: boolean;
  formalEligibilityReasons?: string[];
  sourceIds: string[];
  catalysts?: Array<{ summary: string; sourceIds: string[] }>;
  risks?: Array<{ summary: string; sourceIds: string[] }>;
  macroCodes: string[];
  quantitativeFundCodes?: string[];
  quantitativeEvidence?: unknown[];
  macroEvidence?: Array<{
    code: string;
    label?: string | null;
    value: number | string | null;
    unit?: string | null;
    asOf?: string | null;
    referenceDate?: string | null;
    referencePeriod?: string | null;
    sourceUri?: string | null;
    retrievedAt?: string | null;
  }>;
  watchFor: string[];
  plans: ActionableZoneResult[];
}
export interface SellRiskAlert {
  fundCode: string;
  label: string;
  relationship: Array<"HELD" | "TRACKED">;
  state: "STOP_LOSS" | "TAKE_PROFIT_T2" | "TAKE_PROFIT_T1" | "RSI_OVERBOUGHT";
  basis: "CONFIRMED_NAV" | "ESTIMATE_1430";
  value: PlanPrice;
  asOf: string | null;
  sourceUri: string | null;
  rsi14: PlanPrice;
  rsiAsOf: string | null;
  planOrigin: "FROZEN_STUDY" | "DAILY_REPORT" | "NONE";
  trackId?: string | null;
  reportRunId?: string | null;
  selectedTrack?: PlanTrack | null;
  stopLossPrice: PlanPrice;
  takeProfitT1: PlanPrice;
  takeProfitT2: PlanPrice;
}
export interface SellRiskAlerts {
  asOf: string | null;
  holdingsAsOf?: string | null;
  items: SellRiskAlert[];
  limitations: string[];
  actionReminders?: PersonalActionReminder[];
  normalHoldings?: PersonalActionReminder[];
  summary?: {
    heldCount: number;
    evaluatedCount: number;
    alertCount: number;
    normalCount: number;
    unavailableCount: number;
  };
}
export interface PersonalActionReminder {
  planAsOf?: string | null;
  fundCode: string;
  label: string;
  relationship: Array<"HELD" | "TRACKED">;
  state: "BUY_ZONE" | "SELL_ZONE" | "STOP_LOSS" | "LARGE_DAILY_MOVE" | "NORMAL";
  basis: "CONFIRMED_NAV" | "ESTIMATE_1430" | "INTRADAY_ESTIMATE";
  value: PlanPrice;
  asOf: string | null;
  sourceUri?: string | null;
  planOrigin:
    "FROZEN_STUDY" | "DAILY_REPORT" | "CURRENT_ACTIONABLE_ZONES" | "NONE";
  selectedTrack?: PlanTrack | null;
  entryZoneMin: PlanPrice;
  entryZoneMax: PlanPrice;
  sellZoneMin: PlanPrice;
  sellZoneMax: PlanPrice;
  stopLossPrice: PlanPrice;
  distanceToBuyPct: number | null;
  distanceToSellPct: number | null;
  distanceToStopPct: number | null;
  dailyChangePct: number | null;
  dailyChangeAsOf?: string | null;
  triggerStates: Array<
    "BUY_ZONE" | "SELL_ZONE" | "STOP_LOSS" | "LARGE_DAILY_MOVE"
  >;
}
export interface OpportunityContext extends MacroContext {
  sellRiskAlerts?: SellRiskAlerts;
  strategyMemoryRules?: StrategyMemoryRule[];
  themes?: ThemeGraphRow[];
  alphaPublic?: AlphaPublic | null;
  actionableZones?: ActionableZoneResult[];
  dailyResearch?: DailyResearch | null;
  series?: OpportunitySeries[];
  marketEvidence?: MarketEvidence[];
  directionModel?: {
    summary?: string;
    methodVersion: string;
    state: string;
    supports: Array<{
      label: string;
      summary: string;
      evidenceIds: string[];
      state: string;
    }>;
    counterFactors: Array<{
      label: string;
      summary: string;
      evidenceIds: string[];
      state: string;
    }>;
    watchFor: string[];
    requiredEvidence: string[];
    missingEvidence: string[];
    missingRequiredEvidence?: string[];
    recommendedChecks?: string[];
    failedConditions?: Array<{
      id: string;
      label: string;
      reason: string;
      evidenceIds: string[];
    }>;
    researchState?: string;
    qualificationState?: string;
  } | null;
  decision?: {
    state: string;
    actionableZones?: ActionableZoneResult;
    marketState: string;
    personalState: string;
    summary: string;
    nextChecks: string[];
    assessment?: {
      holdingState: string;
      riskState: string;
      marketResearchState: string;
    };
  } | null;
  portfolio?: {
    state: string;
    heldSelected: boolean | null;
    selectedWeight: number | null;
    sameIndexWeight: number | null;
    cashRatio: number | null;
    asOf: string | null;
    summary: string;
    missingEvidence: string[];
    budgetViolation?: boolean;
    budgetState?: string;
  } | null;
  notices?: ConditionNotice[];
}
export type DailyResearchFailureStage =
  | "MODEL_EXECUTION"
  | "SOURCE_VERIFICATION"
  | "REPORT_VALIDATION"
  | "PERSISTING";
export type DailyResearchFailureReasonCode =
  | "DIRECTION_VALIDATION_FAILED"
  | "FUND_PLAN_INVALID"
  | "SCHEDULE_DATE_INVALID"
  | "SCHEMA_VALIDATION_FAILED"
  | "SOURCE_DATE_INVALID"
  | "SOURCE_REFERENCE_INVALID"
  | "TIMEOUT"
  | "CLI_MODEL_TIMEOUT"
  | "RUNNER_OPERATION_TIMEOUT"
  | "WORKER_RPC_TIMEOUT"
  | "WORKER_OUTER_TIMEOUT"
  | "RUNNER_DISCONNECTED"
  | "MODEL_EXIT_UNCONFIRMED"
  | "NETWORK_UNAVAILABLE"
  | "PAYLOAD_VALIDATION_FAILED"
  | "UNEXPECTED_ERROR"
  | "CANCELLED"
  | "PIPELINE_RUNTIME_EXPIRED";
export type DailyResearchFailureRuleId =
  | "INDUSTRY_REVIEW_DUPLICATE"
  | "INDUSTRY_REVIEW_COVERAGE_MISSING"
  | "INDUSTRY_RISK_CHECK_DUPLICATE"
  | "INDUSTRY_RISK_CHECK_MISSING"
  | "INDUSTRY_RISK_SOURCE_MISSING"
  | "INDUSTRY_CYCLE_SOURCE_INVALID"
  | "DIRECTION_INDUSTRY_REVIEW_UNKNOWN"
  | "DIRECTION_DECLARED_INDUSTRY_UNKNOWN"
  | "DIRECTION_PLAN_SECTOR_UNREVIEWED"
  | "INDUSTRY_REVIEW_BUDGET_EXCEEDED"
  | "DIRECTION_MACRO_REFERENCE_UNKNOWN"
  | "DIRECTION_CALIBRATION_RULE_UNKNOWN"
  | "DIRECTION_EVIDENCE_INSUFFICIENT";
export interface DeterministicRepair {
  version: string;
  originalDecision: "PARTIAL";
  strictRevalidated: boolean;
  quarantinedCount: number;
  quarantined?: Array<{
    itemType: string;
    itemIndex?: number | null;
    businessKey?: string;
    ruleId?: string;
    reason: string;
  }>;
  counts: Record<string, unknown>;
  repairRevision?: string | null;
  buildCommit?: string | null;
}
export interface EconomicResearchDelivery {
  version: "economic-research-delivery-v1";
  researchId: string | null;
  status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "PENDING" | "UNKNOWN";
  asOf: string | null;
  validatedAt: string | null;
  recordedAt: string | null;
  archiveState:
    "ARCHIVED" | "AUDIT_PENDING" | "ARCHIVING" | "UNRECOVERABLE" | null;
  archivePending: boolean;
  coverage?: Partial<TopicRegistryPage["coverage"]> | null;
  observations: ResearchAcceptanceOutcome["accepted"];
  acceptedContentComplete: boolean;
  issueCount?: number;
  economicIssueCount?: number;
  issueListComplete?: boolean;
  actionable: false;
}
export interface FundObservationTrack {
  track: PlanTrack;
  priceState: string;
  technicalState: string;
  entryZoneMin: PlanPrice;
  entryZoneMax: PlanPrice;
  stopLossPrice: PlanPrice;
  takeProfitT1: PlanPrice;
  takeProfitT2: PlanPrice;
  distanceToEntryPct: number | null;
  inEntryZone: boolean | null;
  observationStatus:
    "TECHNICAL_IN_ZONE" | "WATCH" | "AWAY" | "UNAVAILABLE" | "STALE";
  metrics: Record<
    | "rsi14"
    | "ma20"
    | "ma60"
    | "ma200"
    | "excessReturn20d"
    | "excessReturn60d"
    | "drawdown60d",
    PlanPrice
  >;
  missingEvidence: string[];
  entryBlocked?: boolean | null;
  entryBlockReason?: string | null;
  entryStatus?: string | null;
  inSellZone?: boolean | null;
  entryStatusTone?: string | null;
}
export interface FundObservationRisk extends IndustryCycle {
  industry: string;
  researchedAt: string;
  active?: boolean;
  persistedRiskFlags?: string[];
  confirmedAt?: string;
  confirmedRiskAt?: Record<string, string>;
}
export interface FundObservationItem {
  fundCode: string;
  name: string;
  subjectKind: "fund" | "nav" | null;
  subjectId: string | null;
  identityVerified: boolean;
  identityProof: {
    scope: "FROZEN_SERVER_CATALOG";
    sourceUri: string | null;
    verifiedAt: string | null;
  } | null;
  confirmedNav: {
    value: PlanPrice;
    asOf: string | null;
    retrievedAt: string | null;
    sourceUri: string | null;
  } | null;
  referenceTracks: FundObservationTrack[];
  observationStatus:
    | "OBSERVABLE"
    | "PRICE_INCOMPLETE"
    | "NOT_SCANNED"
    | "IDENTITY_UNVERIFIED"
    | "STALE";
  subscription: {
    status: "UNKNOWN" | "OPEN" | "LIMITED" | "SUSPENDED";
    restricted: boolean | null;
  };
  industry: {
    industryIds: string[];
    unresolvedSector: string | null;
    auditStatus:
      "NOT_ALLOCATED" | "PENDING_PROOF" | "STRUCTURE_AND_EXECUTION_VERIFIED";
    missingChecks: string[];
  };
  risk: {
    current: FundObservationRisk[];
    historical: Array<FundObservationRisk & { active: boolean }>;
  };
  topicAssociations: Array<{
    topicId: string | null;
    key: string;
    title: string;
    relation: "CONFIRMED_SCOPE_LINK" | "DECLARED_RELATED_ONLY";
    sourceIds: string[];
  }>;
  relativeStrength?: RelativeStrength | null;
  missingEvidence: string[];
  actionable: false;
  investmentEligible: false;
}
export interface FundObservation {
  version: "fund-observation-v1";
  researchId: string;
  researchAsOf: string | null;
  evaluatedAt: string | null;
  status: "AVAILABLE" | "PARTIAL" | "STALE" | "UNAVAILABLE";
  actionable: false;
  investmentEligible: false;
  coverage: {
    catalogFundCount: number;
    scannedFundCount: number;
    confirmedNavFundCount: number;
    pricedFundCount: number;
    observableFundCount: number;
    unpricedFundCount: number;
    highlightCount: number;
    selectedScopeCount: number;
    deferredScopeCount: number;
    executionVerifiedCheckCount: number;
  };
  highlights: FundObservationItem[];
  items: FundObservationItem[];
  unresolvedRiskNotice: string | null;
  unresolvedRiskScopes: string[];
  missingEvidence?: string[];
}
export interface DailyResearch {
  automaticResearchCooldown?: {
    active: boolean;
    hours: 23;
    lastAiDispatchAt: string | null;
    automaticEligibleAt: string | null;
  };
  economicResearch?: EconomicResearchDelivery;
  fundObservation?: FundObservation;
  schedule?: {
    mode: "DAILY" | "WEEKDAYS";
    time: string;
    timezone: "Asia/Shanghai";
    startWindowMinutes: number;
  };
  runMode?: "SCHEDULED" | "DEBUG" | "MANUAL" | null;
  reportRunMode?: "SCHEDULED" | "DEBUG" | "MANUAL" | null;
  methodVersion: string;
  scheduleState?:
    | "NOT_DUE"
    | "DUE_NOT_DISPATCHED"
    | "DISPATCHED"
    | "DISABLED"
    | "NON_WORKDAY"
    | "MISSED";
  todayScheduledFor?: string | null;
  state:
    | "NOT_RUN"
    | "RUNNING"
    | "READY"
    | "STALE"
    | "FAILED"
    | "UNCERTAIN"
    | "DISABLED"
    | "MISSED";
  pipeline?: {
    id: string;
    state: "RUNNING" | "READY" | "FAILED" | "UNCERTAIN" | "MISSED";
    stage: string;
    startedAt?: string | null;
    startWindowEndsAt?: string | null;
    runtimeDeadlineAt?: string | null;
    updatedAt?: string | null;
    finishedAt?: string | null;
    sourceBatchId?: string | null;
    researchRunId?: string | null;
    errorClass?: string | null;
    quality?: {
      allowed: boolean;
      status: "READY" | "PARTIAL" | "BLOCKED";
      reason: string;
      macroCount: number;
      catalystCount: number;
      reliableNavCount: number;
      failedSources: string[];
    } | null;
  } | null;
  runId: string | null;
  reportRunId?: string | null;
  reportCompletedAt?: string | null;
  reportSlotDate?: string | null;
  scheduledFor: string | null;
  startedAt: string | null;
  completedAt: string | null;
  asOf: string | null;
  nextRunAt: string | null;
  errorClass?: string | null;
  failureStage?: DailyResearchFailureStage | null;
  failureReasonCode?: DailyResearchFailureReasonCode | null;
  failureRuleId?: DailyResearchFailureRuleId | null;
  failureItemIndex?: number | null;
  failureIndustryCodes?: string[] | null;
  report: {
    deterministicRepair?: DeterministicRepair;
    methodVersion?: string;
    asOf?: string;
    summary: string;
    macroCycle?: string | null;
    actionablePlans?: ActionableZoneResult[];
    quantitativeScan?: QuantitativeScan;
    valueDirections?: ValueDirection[];
    topicCatalog?: Array<{
      key: string;
      title: string;
      thesis?: string;
      summary?: string;
      auditStatus?: string;
      plans?: ActionableZoneResult[];
      fundPlans?: ActionableZoneResult[];
    }>;
    researchTopics?: NonNullable<DailyResearch["report"]>["topicCatalog"];
    mentionedFunds?: Array<{
      fundCode: string;
      name?: string;
      label?: string;
      directionKey?: string;
      directionTitle?: string;
      actionableZones?: ActionableZoneResult | null;
    }>;
    industryResearchCoverage?: {
      maximumCandidates: number;
      candidateCount: number;
      omittedCandidateCount: number;
      omittedRiskCandidateCount?: number;
      status: "COMPLETE" | "PARTIAL";
    };
    coverage: Array<{ topic: string; status: string; summary: string }>;
    sources: Array<{
      id: string;
      url: string;
      title: string;
      quote: string;
      publishedDate: string | null;
      publishedAt: string | null;
      retrievedAt: string | null;
      verification: string;
      contentHash?: string | null;
      frozenTitleMatched?: boolean;
      frozenQuoteMatched?: boolean;
    }>;
    events: Array<{
      id: string;
      title: string;
      summary: string;
      phase: "PLANNED" | "ANNOUNCED" | "IMPLEMENTED" | "REPORTED" | "OPINION";
      importance: string;
      sourceIds: string[];
      channels: string[];
      affectedDirectionIds: string[];
      scenarios: Array<{ condition: string; impact: string; counter: string }>;
      watchFor: string[];
      unknowns: string[];
      marketReaction: "UNVERIFIED";
      reasoningType: "MODEL_CONDITIONAL";
      factVerification?: "UNVERIFIED";
    }>;
    upcoming: Array<{
      id: string;
      title: string;
      phase: "PLANNED";
      scheduledDate: string | null;
      scheduledAt: string | null;
      sourceIds: string[];
      affectedDirectionIds: string[];
      watchFor: string[];
    }>;
    energy: Array<{
      id: string;
      code: string;
      label: string;
      value: number | null;
      unit: "USD/barrel" | "USD/gallon";
      frequency: "DAILY" | "WEEKLY";
      asOf: string | null;
      sourceUri: string | null;
      retrievedAt: string | null;
      publishedAt: null;
      timeBasis: "OBSERVATION_ONLY";
      state: "READY" | "STALE";
      previousValue: number | null;
      previousDate: string | null;
      change: number | null;
      changePct: number | null;
      sampleCount: number;
    }>;
  } | null;
}
export interface ConditionNotice {
  id: string;
  title: string;
  summary: string;
  severity: string;
  createdAt: string;
  readAt: string | null;
}
export interface ConditionMonitor extends OpportunityScope {
  id: string;
  enabled: boolean;
}
export interface OperatingCosts {
  state: string;
  managementRate?: number | null;
  custodyRate?: number | null;
  salesServiceRate?: number | null;
  totalRate?: number | null;
  sourceUri?: string | null;
  retrievedAt?: string | null;
}
export interface ValuationEvidence {
  state: string;
  latest?: number | null;
  percentile?: number | null;
  sampleCount?: number;
  historyStart?: string | null;
  historyEnd?: string | null;
  sourceUri?: string | null;
}
export interface ValueEvidence {
  eligible: boolean;
  state: "RESEARCH_CANDIDATE" | "NOT_CANDIDATE" | "INCOMPLETE";
  reasons: string[];
  evidence: {
    pe?: ValuationEvidence;
    pb?: ValuationEvidence;
    costs?: OperatingCosts;
    performance?: {
      state: string;
      eligible: boolean;
      maxDrawdown?: number | null;
    };
    limitations?: string[];
  };
}
export interface Analysis {
  navChanges?: {
    basis: "UNIT_NAV_CHANGE";
    unit: "decimal_fraction";
    change63: number | null;
    change252: number | null;
    sampleCount: number;
    missingWindows: number[];
    reasons: string[];
  };
  actionableZones?: ActionableZoneResult;
  state: string;
  score: number | null;
  indicators: Record<string, number | null>;
  reasons: Array<{
    key: string;
    reason: string;
    passed: boolean;
    points: number;
    weight: number;
  }>;
  points: Array<{ date: string; close: number }>;
  asOf?: string;
  source?: string;
  inputHash?: string;
  algorithmVersion?: string;
  seriesKind?: string;
  performance?: PerformanceEvidence;
  costs?: OperatingCosts;
  valueEvidence?: ValueEvidence;
  valuation?: {
    state: string;
    requestedEnd?: string | null;
    sourceUri?: string | null;
    pe?: {
      state: string;
      metric?: string;
      points?: Array<{ date: string; value: number }>;
    };
    pb?: {
      state: string;
      metric?: string;
      points?: Array<{ date: string; value: number }>;
    };
  };
}
export interface ScreenFilters {
  directionId?: string;
  indexId?: string;
  keyword?: string;
  tradeForm?: string;
  sortBy?: string;
  sortOrder?: string;
  analysisAsOf?: string;
  scorableOnly?: string;
  performanceOnly?: string;
  minHistoryDays?: string;
  page: number;
  pageSize: number;
}
export interface ScreenResult {
  items: ResearchFund[];
  total: number;
  page: number;
  pageSize: number;
  analysisAsOf?: string;
}
async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await request<T>(`/v1${path}`, { signal })).data;
}
export const getDirections = (signal?: AbortSignal) =>
  read<Direction[]>("/investment/directions", signal);
export const getIndices = (id: string, signal?: AbortSignal) =>
  read<ResearchIndex[]>(
    `/investment/directions/${encodeURIComponent(id)}/indices`,
    signal,
  );
export const getMarketContext = (signal?: AbortSignal, months = 36) =>
  read<MacroContext>(`/investment/market-context?months=${months}`, signal);
export function getOpportunityContext(
  query: OpportunityQuery,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return read<OpportunityContext>(
    `/investment/opportunity-context?${params}`,
    signal,
  );
}
export const getConditionMonitors = (signal?: AbortSignal) =>
  read<ConditionMonitor[]>("/investment/condition-monitors", signal);
export async function setConditionMonitor(
  scope: OpportunityScope & { enabled: boolean },
) {
  return (
    await request<{ id: string; enabled: boolean }>(
      "/v1/investment/condition-monitors",
      { method: "POST", body: JSON.stringify(scope) },
    )
  ).data;
}
export async function ackConditionNotice(id: string) {
  return (
    await request<{ id: string; readAt: string }>(
      `/v1/investment/condition-notices/${encodeURIComponent(id)}/ack`,
      { method: "POST" },
    )
  ).data;
}
export const searchResearch = (keyword: string, signal?: AbortSignal) =>
  read<SearchResult[]>(
    `/investment/search?keyword=${encodeURIComponent(keyword)}`,
    signal,
  );
export function screenFunds(filters: ScreenFilters, signal?: AbortSignal) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== "") params.set(k, String(v));
  });
  return read<ScreenResult>(`/funds/screener?${params}`, signal);
}
export const getAnalysis = (
  kind: "index" | "fund" | "nav",
  id: string,
  signal?: AbortSignal,
) =>
  read<Analysis>(
    `/investment/${kind === "index" ? "indices" : kind === "nav" ? "fund-ranges" : "instruments"}/${encodeURIComponent(id)}/analysis`,
    signal,
  );
export async function refreshResearch(
  kind: "index" | "fund" | "nav",
  id: string,
) {
  return (
    await request<{ runId: string; created: boolean }>(
      "/v1/investment/refresh",
      { method: "POST", body: JSON.stringify({ kind, id }) },
    )
  ).data;
}
export const getRefreshStatus = (id: string, signal?: AbortSignal) =>
  read<{ status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" }>(
    `/investment/refresh/${encodeURIComponent(id)}`,
    signal,
  );
