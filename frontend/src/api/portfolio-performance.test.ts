import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getPerformanceBaselines,
  revisePerformanceBaseline,
} from "./portfolio";

const meta = {
  requestId: "req-performance",
  asOf: "2026-08-30T20:00:00+08:00",
  qualityStatus: "FRESH",
  schemaVersion: 1,
};

const baseline = {
  accountId: "account-1",
  accountName: "天天基金账户",
  baselineAsOf: "2026-08-30",
  baselineTotalAsset: "87888.32",
  initialCumulativePnl: "-1200.00",
  source: "USER",
  version: 2,
  createdAt: "2026-08-30T20:00:00+08:00",
};

afterEach(() => vi.unstubAllGlobals());

describe("performance baseline API", () => {
  it("lists the current user's account baselines", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: [baseline], meta }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    expect(await getPerformanceBaselines()).toEqual([baseline]);
    expect(fetchMock.mock.calls[0][0]).toContain(
      "/v1/portfolio/performance-baselines",
    );
  });

  it("sends a versioned idempotent append command", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: baseline, meta }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await revisePerformanceBaseline({
      accountId: "account-1",
      initialCumulativePnl: "-1200",
      version: 1,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(url).toContain("/v1/portfolio/performance-baselines/account-1");
    expect(init.method).toBe("PUT");
    expect(headers.get("If-Match")).toBe('"1"');
    expect(headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({
      initialCumulativePnl: "-1200",
    });
  });
});
