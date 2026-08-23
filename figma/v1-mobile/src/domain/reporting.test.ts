import { describe, expect, it } from "vitest"

import {
  getAvailableSessions,
  getDataQualityLabel,
  getReportForTenant,
} from "./reporting"
import type { InvestmentReport } from "./types"

const reports: InvestmentReport[] = [
  {
    id: "rpt-a-pre",
    tenantId: "tenant-a",
    session: "PRE_MARKET",
    title: "盘前简报",
    summary: "测试摘要",
    generatedAt: "2026-08-23T08:20:00+08:00",
    dataCutoffAt: "2026-08-23T08:15:00+08:00",
    quality: "FRESH",
    sections: [],
  },
  {
    id: "rpt-b-pre",
    tenantId: "tenant-b",
    session: "PRE_MARKET",
    title: "另一个用户的盘前简报",
    summary: "不应跨租户可见",
    generatedAt: "2026-08-23T08:21:00+08:00",
    dataCutoffAt: "2026-08-23T08:16:00+08:00",
    quality: "STALE",
    sections: [],
  },
]

describe("reporting domain", () => {
  it("returns exactly the selected tenant's report", () => {
    expect(getReportForTenant(reports, "tenant-a", "PRE_MARKET")?.id).toBe(
      "rpt-a-pre",
    )
    expect(getReportForTenant(reports, "tenant-a", "POST_MARKET")).toBeNull()
  })

  it("keeps all four MVP report sessions in the product order", () => {
    expect(getAvailableSessions()).toEqual([
      "PRE_MARKET",
      "INTRADAY",
      "FUND_1430",
      "POST_MARKET",
    ])
  })

  it("translates data quality into an explicit user-facing label", () => {
    expect(getDataQualityLabel("FRESH")).toBe("实时")
    expect(getDataQualityLabel("STALE")).toBe("数据已滞后")
    expect(getDataQualityLabel("UNAVAILABLE")).toBe("数据不可用")
  })
})
