import type {
  DataQuality,
  InvestmentReport,
  ReportSession,
  TenantId,
} from "./types"

// Session order is a product contract shared by the scheduler, API and clients.
// Keeping it here prevents every UI from inventing a different daily workflow.
const REPORT_SESSIONS: readonly ReportSession[] = [
  "PRE_MARKET",
  "INTRADAY",
  "FUND_1430",
  "POST_MARKET",
]

export function getAvailableSessions(): ReportSession[] {
  return [...REPORT_SESSIONS]
}

export function getReportForTenant(
  reports: InvestmentReport[],
  tenantId: TenantId,
  session: ReportSession,
): InvestmentReport | null {
  // Tenant filtering is repeated here intentionally. The production API and RLS
  // remain authoritative, while this guard prevents accidental client-side bleed.
  return (
    reports.find(
      (report) => report.tenantId === tenantId && report.session === session,
    ) ?? null
  )
}

export function getDataQualityLabel(quality: DataQuality): string {
  const labels: Record<DataQuality, string> = {
    FRESH: "实时",
    ESTIMATED: "估算",
    STALE: "数据已滞后",
    CONFLICT: "来源存在冲突",
    UNAVAILABLE: "数据不可用",
  }

  return labels[quality]
}

export function getSessionLabel(session: ReportSession): string {
  const labels: Record<ReportSession, string> = {
    PRE_MARKET: "盘前简报",
    INTRADAY: "盘中跟踪",
    FUND_1430: "基金 14:30",
    POST_MARKET: "盘后复盘",
  }

  return labels[session]
}
