import * as api from "./investment";
import { request, requestWithTimeout } from "./client";
vi.mock("./client", () => ({ request: vi.fn(), requestWithTimeout: vi.fn() }));

beforeEach(() => {
  vi.mocked(request).mockResolvedValue({ data: {}, meta: {} } as never);
  vi.mocked(requestWithTimeout).mockResolvedValue({
    data: {},
    meta: {},
  } as never);
});

it("encodes the complete opportunity scope, opts into compact charts and forwards cancellation", async () => {
  const controller = new AbortController();
  await api.getOpportunityContext(
    {
      directionId: "a-share:broad",
      indexId: "hs300",
      instrumentId: "fund-id",
      fundCode: "510330",
      fundMarket: "SSE",
      months: 60,
    },
    controller.signal,
  );
  const [url, init] = vi.mocked(requestWithTimeout).mock.calls.at(-1)!;
  const parsed = new URL(url, "https://app.test");
  expect(parsed.pathname).toBe("/v1/investment/opportunity-context");
  expect(Object.fromEntries(parsed.searchParams)).toEqual({
    directionId: "a-share:broad",
    indexId: "hs300",
    instrumentId: "fund-id",
    fundCode: "510330",
    fundMarket: "SSE",
    months: "60",
    compactCharts: "true",
    chartFormat: "shared-v1",
  });
  expect(init?.signal).toBe(controller.signal);
  expect(vi.mocked(requestWithTimeout).mock.calls.at(-1)![2]).toBe(90_000);
});

it("hydrates shared chart transport before exposing the unchanged report to consumers", async () => {
  const id = "a".repeat(64);
  const series = {
    fundCode: "012733",
    priceBasis: "UNIT_NAV",
    unit: "CNY/份",
    asOf: "2026-11-12",
    sourceUri: "https://example.org/nav",
    points: [{ date: "2026-11-12", value: 1.23 }],
    extension: { verified: true },
  };
  const wire = {
    state: "READY",
    items: [],
    events: [],
    dailyResearch: {
      report: {
        summary: "完整已保存洞察",
        valueDirections: [
          { key: "AI", plans: [{ series: { seriesRef: id } }] },
        ],
        researchTopics: [
          { key: "MEMORY", plans: [{ series: { seriesRef: id } }] },
        ],
        mentionedFunds: [
          {
            fundCode: "012733",
            actionableZones: { series: { seriesRef: id } },
          },
        ],
      },
    },
    chartSeries: { version: "shared-v1", items: { [id]: series } },
  };
  vi.mocked(requestWithTimeout).mockResolvedValueOnce({
    data: wire,
    meta: {},
  } as never);
  const result = await api.getOpportunityContext({ months: 36 });
  expect(
    result.dailyResearch?.report?.valueDirections?.[0].plans?.[0].series,
  ).toBe(series);
  expect(
    result.dailyResearch?.report?.researchTopics?.[0].plans?.[0].series,
  ).toBe(series);
  expect(
    result.dailyResearch?.report?.mentionedFunds?.[0].actionableZones?.series,
  ).toBe(series);
  expect(result.events).toBe(wire.events);
  expect(result).not.toHaveProperty("chartSeries");
  expect(wire).toHaveProperty("chartSeries");
});

it("uses scoped JSON mutations for monitors and the notice acknowledgement endpoint", async () => {
  const scope = {
    directionId: "a-share:broad",
    indexId: "hs300",
    instrumentId: "fund-id",
    fundCode: "510330",
    fundMarket: "SSE",
  };
  await api.setConditionMonitor({ ...scope, enabled: true });
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/condition-monitors",
    { method: "POST", body: JSON.stringify({ ...scope, enabled: true }) },
  );
  await api.ackConditionNotice("notice/1");
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/condition-notices/notice%2F1/ack",
    { method: "POST" },
  );
});
