export type TenantId = string
export type UserId = string

export type AssetType = "CN_STOCK" | "EXCHANGE_FUND" | "OFF_EXCHANGE_FUND"

export type ReportSession = "PRE_MARKET" | "INTRADAY" | "FUND_1430" | "POST_MARKET"

export type DataQuality = "FRESH" | "ESTIMATED" | "STALE" | "CONFLICT" | "UNAVAILABLE"

export type DecisionStatus = "DRAFT" | "CONFIRMED" | "CORRECTED" | "NOT_EXECUTED"

export interface Tenant {
  id: TenantId
  name: string
  shortName: string
}

export interface UserProfile {
  id: UserId
  tenantId: TenantId
  name: string
  initials: string
  riskProfile: "CONSERVATIVE" | "BALANCED" | "AGGRESSIVE"
  weComConnected: boolean
}

export interface Asset {
  id: string
  type: AssetType
  symbol: string
  name: string
  market: "SH" | "SZ" | "FUND_OTC"
  price: number
  changePercent: number
  priceAsOf: string
  quality: DataQuality
  tags: string[]
  sparkline: number[]
}

export interface Position {
  id: string
  tenantId: TenantId
  assetId: string
  quantity: number
  costBasis: number
  marketValue: number
  pnlPercent: number
}

export interface SourceEvidence {
  id: string
  publisher: string
  title: string
  sourceType: "MARKET_DATA" | "DISCLOSURE" | "RESEARCH" | "MEDIA"
  publishedAt: string
  url: string
  quality: DataQuality
}

export interface ReportSection {
  id: string
  eyebrow: string
  title: string
  body: string
  tone: "POSITIVE" | "WARNING" | "NEUTRAL" | "INFO"
  assetIds?: string[]
}

export interface InvestmentReport {
  id: string
  tenantId: TenantId
  session: ReportSession
  title: string
  summary: string
  generatedAt: string
  dataCutoffAt: string
  quality: DataQuality
  sections: ReportSection[]
  evidenceIds?: string[]
}

export interface ReviewDecision {
  id: string
  tenantId: TenantId
  assetId: string
  status: DecisionStatus
  thesis: string
  nextAction: string
  recordedAt: string
}

export type MainView = "REPORTS" | "PORTFOLIO" | "WATCHLIST" | "SOURCES"
