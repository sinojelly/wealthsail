import { HttpError } from "./client";
import type { OpportunityContext } from "./investment";
import { hydrateOpportunityChartSeries } from "./opportunity-chart-transport";

const firstKey = "a".repeat(64);
const secondKey = "b".repeat(64);

function curve(overrides: Record<string, unknown> = {}) {
  return {
    state: "READY",
    fundCode: "000001",
    asOf: "2026-11-11",
    sourceUri: "https://data.test/nav",
    retrievedAt: "2026-11-11T01:00:00Z",
    unit: "CNY",
    priceBasis: "UNIT_NAV",
    points: [{ date: "2026-11-11", value: "1.2345" }],
    ...overrides,
  };
}

function context(
  report: Record<string, unknown>,
  transport?: unknown,
): OpportunityContext {
  const data = {
    state: "READY",
    items: [],
    dailyResearch: {
      methodVersion: "daily-research-v1",
      state: "READY",
      runId: "current-run",
      reportRunId: "report-run",
      scheduledFor: null,
      startedAt: null,
      completedAt: null,
      asOf: null,
      nextRunAt: null,
      report: {
        summary: "完整研究",
        coverage: [],
        sources: [],
        events: [],
        ...report,
      },
    },
  };
  return (transport === undefined
    ? data
    : { ...data, chartSeries: transport }) as unknown as OpportunityContext;
}

function reportOf(data: OpportunityContext) {
  return data.dailyResearch!.report! as unknown as Record<string, unknown>;
}

function expectInvalid(data: OpportunityContext) {
  let failure: unknown;
  try {
    hydrateOpportunityChartSeries(data);
  } catch (error) {
    failure = error;
  }
  expect(failure).toBeInstanceOf(HttpError);
  expect(failure).toMatchObject({ code: "INVALID_CHART_SERIES" });
  expect((failure as HttpError).message).not.toContain("private-diagnostic");
}

it("restores every nested report chart without changing report identities or arbitrary curve metadata", () => {
  const first = curve({ vendorExtension: { quality: "accepted" } });
  const second = curve({
    asOf: "2026-11-10",
    sourceUri: "https://other.test/nav",
  });
  const payload = context(
    {
      valueDirections: [
        { key: "AI", plans: [{ series: { seriesRef: firstKey } }] },
      ],
      researchTopics: [
        { key: "MEMORY", fundPlans: [{ series: { seriesRef: firstKey } }] },
      ],
      mentionedFunds: [
        {
          fundCode: "000001",
          actionableZones: { series: { seriesRef: secondKey } },
        },
      ],
      quantitativeScan: { items: [{ series: { seriesRef: firstKey } }] },
      futureExtension: { rows: [[{ series: { seriesRef: firstKey } }]] },
    },
    { version: "shared-v1", items: { [firstKey]: first, [secondKey]: second } },
  );
  const hydrated = hydrateOpportunityChartSeries(payload);

  expect(hydrated).toEqual(
    context({
      valueDirections: [{ key: "AI", plans: [{ series: first }] }],
      researchTopics: [{ key: "MEMORY", fundPlans: [{ series: first }] }],
      mentionedFunds: [
        { fundCode: "000001", actionableZones: { series: second } },
      ],
      quantitativeScan: { items: [{ series: first }] },
      futureExtension: { rows: [[{ series: first }]] },
    }),
  );
  const report = reportOf(hydrated);
  const directions = report.valueDirections as Array<{
    plans: Array<{ series: unknown }>;
  }>;
  const topics = report.researchTopics as Array<{
    fundPlans: Array<{ series: unknown }>;
  }>;
  expect(directions[0].plans[0].series).toBe(first);
  expect(topics[0].fundPlans[0].series).toBe(first);
  expect(hydrated).not.toHaveProperty("chartSeries");
  expect(payload).toHaveProperty("chartSeries");
  expect(reportOf(payload).valueDirections).toEqual([
    { key: "AI", plans: [{ series: { seriesRef: firstKey } }] },
  ]);
});

it("preserves untouched subtrees and clones only ancestors of replaced report series", () => {
  const untouched = { metadata: ["keep", { source: "unchanged" }] };
  const first = curve();
  const payload = context(
    {
      untouched,
      rows: [untouched, { nested: { series: { seriesRef: firstKey } } }],
    },
    { version: "shared-v1", items: { [firstKey]: first } },
  );
  const inputReport = reportOf(payload);
  const result = hydrateOpportunityChartSeries(payload);
  const outputReport = reportOf(result);

  expect(result).not.toBe(payload);
  expect(result.items).toBe(payload.items);
  expect(result.dailyResearch).not.toBe(payload.dailyResearch);
  expect(outputReport).not.toBe(inputReport);
  expect(outputReport.untouched).toBe(untouched);
  expect((outputReport.rows as unknown[])[0]).toBe(untouched);
  expect(outputReport.rows).not.toBe(inputReport.rows);
});

it("returns the same legacy context when no shared table or strict report reference exists", () => {
  const inline = curve({ points: [] });
  const payload = context({
    rows: [{ series: inline }],
    legacy: [{ series: null }, { series: [1, 2] }],
  });
  expect(hydrateOpportunityChartSeries(payload)).toBe(payload);
});

it("does not decode references outside report series fields or reference-shaped objects with extra fields", () => {
  const data = context({
    reference: { seriesRef: firstKey },
    rows: [{ series: { seriesRef: "private-diagnostic", futureField: true } }],
  });
  Object.assign(data, { series: { seriesRef: "outside-report" } });
  Object.assign(data.dailyResearch!, {
    series: { seriesRef: "outside-report" },
  });
  expect(hydrateOpportunityChartSeries(data)).toBe(data);
});

it("does not traverse inline chart points or decode reference-looking data inside them", () => {
  const inline = curve();
  const point = { date: "2026-11-11", value: "1.2345" };
  Object.defineProperty(point, "series", {
    enumerable: true,
    get() {
      throw new Error("inline points must not be visited");
    },
  });
  inline.points = [point];
  const data = context({ rows: [{ series: inline }] });
  expect(hydrateOpportunityChartSeries(data)).toBe(data);
});

it("accepts an empty points curve and keeps an equal-code curve with different metadata distinct", () => {
  const first = curve({ points: [], custom: { confirmed: true } });
  const second = curve({ points: [], retrievedAt: "2026-11-11T02:00:00Z" });
  const data = context(
    {
      rows: [
        { series: { seriesRef: firstKey } },
        { series: { seriesRef: secondKey } },
      ],
    },
    { version: "shared-v1", items: { [firstKey]: first, [secondKey]: second } },
  );
  const rows = reportOf(hydrateOpportunityChartSeries(data)).rows as Array<{
    series: unknown;
  }>;
  expect(rows[0].series).toBe(first);
  expect(rows[1].series).toBe(second);
  expect(rows[0].series).not.toBe(rows[1].series);
});

it.each([
  null,
  {},
  { version: "private-diagnostic", items: {} },
  { version: "shared-v1" },
  { version: "shared-v1", items: null },
  { version: "shared-v1", items: [] },
  { version: "shared-v1", items: { "private-diagnostic": curve() } },
  { version: "shared-v1", items: { ["A".repeat(64)]: curve() } },
  { version: "shared-v1", items: { [firstKey]: null } },
  { version: "shared-v1", items: { [firstKey]: [] } },
  {
    version: "shared-v1",
    items: { [firstKey]: { points: "private-diagnostic" } },
  },
  { version: "shared-v1", items: { [firstKey]: { seriesRef: secondKey } } },
])(
  "rejects malformed transport even when an invalid table entry is unused: %j",
  (transport) => {
    expectInvalid(context({}, transport));
  },
);

it.each(["private-diagnostic", "a".repeat(63), "A".repeat(64), null, 7, {}])(
  "rejects malformed strict chart references with a fixed safe error: %j",
  (seriesRef) => {
    expectInvalid(
      context(
        { rows: [{ series: { seriesRef } }] },
        { version: "shared-v1", items: {} },
      ),
    );
  },
);

it("rejects a strict chart reference when the table is absent", () => {
  expectInvalid(context({ rows: [{ series: { seriesRef: firstKey } }] }));
});

it("rejects a missing chart instead of silently dropping the plot", () => {
  expectInvalid(
    context(
      { rows: [{ series: { seriesRef: secondKey } }] },
      { version: "shared-v1", items: { [firstKey]: curve() } },
    ),
  );
});

it("does not resolve a chart from an inherited table entry", () => {
  const items = Object.create({ [firstKey]: curve() });
  expectInvalid(
    context(
      { rows: [{ series: { seriesRef: firstKey } }] },
      { version: "shared-v1", items },
    ),
  );
});

it("preserves __proto__ as ordinary JSON data without changing any prototype", () => {
  const report = JSON.parse(
    `{"__proto__":{"series":{"seriesRef":"${firstKey}"}},"rows":[]}`,
  );
  const data = context(report, {
    version: "shared-v1",
    items: { [firstKey]: curve() },
  });
  const originalPrototype = Object.getPrototypeOf(reportOf(data));
  const result = reportOf(hydrateOpportunityChartSeries(data));
  expect(Object.hasOwn(result, "__proto__")).toBe(true);
  expect(Object.getPrototypeOf(result)).toBe(originalPrototype);
  expect(result["__proto__"]).toEqual({ series: curve() });
  expect(Object.prototype).not.toHaveProperty("series");
  expect(reportOf(data)["__proto__"]).toEqual({
    series: { seriesRef: firstKey },
  });
});

it("removes a valid empty transport table without cloning an unchanged report", () => {
  const data = context({ rows: [] }, { version: "shared-v1", items: {} });
  const result = hydrateOpportunityChartSeries(data);
  expect(result).not.toBe(data);
  expect(result).not.toHaveProperty("chartSeries");
  expect(result.dailyResearch).toBe(data.dailyResearch);
});
