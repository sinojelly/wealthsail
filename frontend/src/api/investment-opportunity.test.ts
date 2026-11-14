import * as api from "./investment";
import { HttpError, request, requestWithTimeout } from "./client";
vi.mock("./client", async (original) => ({
  ...(await original<typeof import("./client")>()),
  request: vi.fn(),
  requestWithTimeout: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
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
    chartFormat: "shared-v2",
  });
  expect(init?.signal).toBe(controller.signal);
  expect(vi.mocked(requestWithTimeout).mock.calls.at(-1)![2]).toBe(90_000);
});

it("hydrates general report values before legacy chart references at the API boundary", async () => {
  const a = "a".repeat(64),
    b = "b".repeat(64);
  const curve = {
    fundCode: "000001",
    points: [{ date: "2026-11-12", value: "1.25" }],
    extension: { source: "preserved" },
  };
  const wire = {
    state: "READY",
    items: [],
    dailyResearch: {
      report: { valueDirections: [{ plans: [{ reportValueRef: a }] }] },
    },
    reportValues: {
      version: "shared-v2",
      items: {
        [a]: {
          currentPrice: "1.25",
          series: { seriesRef: b },
          extension: { full: true },
        },
      },
    },
    chartSeries: { version: "shared-v1", items: { [b]: curve } },
  };
  vi.mocked(requestWithTimeout).mockResolvedValueOnce({
    data: wire,
    meta: {},
  } as never);
  const result = await api.getOpportunityContext({ months: 36 });
  expect(result.dailyResearch?.report?.valueDirections?.[0].plans[0]).toEqual({
    currentPrice: "1.25",
    series: curve,
    extension: { full: true },
  });
  expect(result).not.toHaveProperty("reportValues");
  expect(result).not.toHaveProperty("chartSeries");
  expect(wire.dailyResearch.report.valueDirections[0].plans[0]).toEqual({
    reportValueRef: a,
  });
});

it("falls back exactly once to shared-v1 on an old server's 422 using the same scope and cancellation", async () => {
  const controller = new AbortController();
  vi.mocked(requestWithTimeout)
    .mockRejectedValueOnce(new HttpError("unsupported format", 422))
    .mockResolvedValueOnce({
      data: { state: "READY", items: [] },
      meta: {},
    } as never);
  await api.getOpportunityContext(
    { months: 36, fundCode: "000001" },
    controller.signal,
  );
  expect(requestWithTimeout).toHaveBeenCalledTimes(2);
  const calls = vi.mocked(requestWithTimeout).mock.calls;
  expect(
    calls.map(([url]) =>
      new URL(url, "https://example.test").searchParams.get("chartFormat"),
    ),
  ).toEqual(["shared-v2", "shared-v1"]);
  expect(
    calls.every(
      ([url, init]) =>
        new URL(url, "https://example.test").searchParams.get("fundCode") ===
          "000001" && init?.signal === controller.signal,
    ),
  ).toBe(true);
  expect(calls[1][2]).toBeGreaterThan(0);
  expect(calls[1][2]).toBeLessThanOrEqual(90000);
  expect(request).not.toHaveBeenCalled();
});

it.each([408, 500, 502])(
  "does not fall back after status %s",
  async (status) => {
    const failure = new HttpError("private-diagnostic", status);
    vi.mocked(requestWithTimeout).mockRejectedValueOnce(failure);
    await expect(api.getOpportunityContext({ months: 36 })).rejects.toBe(
      failure,
    );
    expect(requestWithTimeout).toHaveBeenCalledTimes(1);
  },
);

it("never retries corrupt v2 payloads as a legacy read", async () => {
  vi.mocked(requestWithTimeout).mockResolvedValueOnce({
    data: {
      dailyResearch: { report: { value: { reportValueRef: "a".repeat(64) } } },
      reportValues: { version: "shared-v2", items: {} },
    },
    meta: {},
  } as never);
  await expect(api.getOpportunityContext({ months: 36 })).rejects.toMatchObject(
    { code: "INVALID_REPORT_TRANSPORT" },
  );
  expect(requestWithTimeout).toHaveBeenCalledTimes(1);
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
