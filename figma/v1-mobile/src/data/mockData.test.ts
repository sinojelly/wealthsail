import { describe, expect, it } from "vitest"

import { assets, positions, reports, tenants, users } from "./mockData"

describe("MVP fixture integrity", () => {
  it("covers three isolated users and every MVP asset category", () => {
    expect(tenants).toHaveLength(3)
    expect(users).toHaveLength(3)
    expect(new Set(users.map((user) => user.tenantId)).size).toBe(3)
    expect(new Set(assets.map((asset) => asset.type))).toEqual(
      new Set(["CN_STOCK", "EXCHANGE_FUND", "OFF_EXCHANGE_FUND"]),
    )
  })

  it("provides one report per tenant and daily session", () => {
    expect(reports).toHaveLength(tenants.length * 4)
    expect(
      new Set(reports.map((report) => `${report.tenantId}:${report.session}`))
        .size,
    ).toBe(reports.length)
  })

  it("keeps every position linked to an existing tenant and asset", () => {
    const tenantIds = new Set(tenants.map((tenant) => tenant.id))
    const assetIds = new Set(assets.map((asset) => asset.id))

    for (const position of positions) {
      expect(tenantIds.has(position.tenantId)).toBe(true)
      expect(assetIds.has(position.assetId)).toBe(true)
    }
  })
})
