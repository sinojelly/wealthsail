import { HttpError } from "./client";
import type { OpportunityContext } from "./investment";
import { hydrateOpportunityReportValues } from "./opportunity-report-transport";

const a = "a".repeat(64),
  b = "b".repeat(64),
  c = "c".repeat(64);
function context(report: unknown, table?: unknown): OpportunityContext {
  return {
    state: "READY",
    items: [],
    dailyResearch: { report },
    ...(table === undefined ? {} : { reportValues: table }),
  } as unknown as OpportunityContext;
}
function shared(items: Record<string, unknown>) {
  return { version: "shared-v2", items };
}
function reportOf(value: OpportunityContext) {
  return value.dailyResearch!.report! as unknown as Record<string, unknown>;
}
function invalid(value: OpportunityContext) {
  let failure: unknown;
  try {
    hydrateOpportunityReportValues(value);
  } catch (error) {
    failure = error;
  }
  expect(failure).toBeInstanceOf(HttpError);
  expect(failure).toMatchObject({
    status: 502,
    code: "INVALID_REPORT_TRANSPORT",
  });
  expect((failure as HttpError).message).not.toContain("private-diagnostic");
}

it("restores full plans, all nested curves and unknown extensions without mutating inputs", () => {
  const curve = {
    fundCode: "000001",
    unit: "CNY/份",
    priceBasis: "UNIT_NAV",
    points: [{ date: "2026-11-12", value: "1.25", nullable: null }],
    sourceUri: "https://example.test/nav",
    retrievedAt: "2026-11-12T07:00:00+08:00",
  };
  const plan = {
    fundCode: "000001",
    currentPrice: "1.25",
    tracks: [
      {
        entryZoneMin: "1.10",
        entryZoneMax: "1.20",
        takeProfitT1: "1.40",
        takeProfitT2: "1.50",
        stopLossPrice: "1.00",
      },
    ],
    series: curve,
    unknownExtension: { flags: [true, false, null], number: 1.2345 },
  };
  const reference = { reportValueRef: a };
  const untouched = { summary: "原研究正文", sourceIds: ["s1"] };
  const value = context(
    {
      untouched,
      valueDirections: [{ plans: [reference] }],
      researchTopics: [{ fundPlans: [reference] }],
      mentionedFunds: [{ actionableZones: reference }],
      quantitativeScan: { items: [reference] },
      future: [[reference]],
    },
    shared({ [a]: plan }),
  );
  const before = structuredClone(value);
  const result = hydrateOpportunityReportValues(value),
    report = reportOf(result);
  expect(report).toEqual({
    untouched,
    valueDirections: [{ plans: [plan] }],
    researchTopics: [{ fundPlans: [plan] }],
    mentionedFunds: [{ actionableZones: plan }],
    quantitativeScan: { items: [plan] },
    future: [[plan]],
  });
  const direction = (report.valueDirections as Array<{ plans: unknown[] }>)[0]
    .plans[0];
  expect(direction).toBe(
    (report.quantitativeScan as { items: unknown[] }).items[0],
  );
  expect(report.untouched).toBe(untouched);
  expect(result.items).toBe(value.items);
  expect(value).toEqual(before);
  expect(result).not.toHaveProperty("reportValues");
});

it("resolves nested table references once and preserves array order and primitive values", () => {
  const value = context(
    {
      left: { reportValueRef: a },
      right: { reportValueRef: a },
      nullable: { reportValueRef: c },
    },
    shared({
      [a]: [
        { reportValueRef: b },
        0,
        false,
        "",
        null,
        { x: { reportValueRef: b } },
      ],
      [b]: { nested: "完整正文" },
      [c]: null,
    }),
  );
  const report = reportOf(hydrateOpportunityReportValues(value));
  expect(report.left).toEqual([
    { nested: "完整正文" },
    0,
    false,
    "",
    null,
    { x: { nested: "完整正文" } },
  ]);
  expect(report.left).toBe(report.right);
  expect(report.nullable).toBeNull();
  const entries = report.left as Array<unknown>;
  expect(entries[0]).toBe((entries[5] as { x: unknown }).x);
});

it("restores shared long text, numeric and boolean values with their exact original types", () => {
  const text = "完整宏观与行业研究正文。".repeat(100);
  const value = context(
    {
      title: { reportValueRef: a },
      body: { reportValueRef: a },
      number: { reportValueRef: b },
      flag: { reportValueRef: c },
    },
    shared({ [a]: text, [b]: 1.2345, [c]: false }),
  );
  expect(reportOf(hydrateOpportunityReportValues(value))).toEqual({
    title: text,
    body: text,
    number: 1.2345,
    flag: false,
  });
});

it("keeps every distinct large value and point without a response-size item cap", () => {
  const entries = Array.from(
    { length: 601 },
    (_, index) =>
      [
        index.toString(16).padStart(64, "0"),
        {
          fundCode: String(index).padStart(6, "0"),
          points: Array.from({ length: 10 }, (_, point) => ({
            value: point,
            date: String(point),
          })),
          clock: index,
        },
      ] as const,
  );
  const value = context(
    { rows: entries.map(([key]) => ({ reportValueRef: key })) },
    shared(Object.fromEntries(entries)),
  );
  expect(reportOf(hydrateOpportunityReportValues(value)).rows).toEqual(
    entries.map(([, item]) => item),
  );
});

it("preserves reference-looking business data and legacy transport when no v2 table exists", () => {
  const value = context({
    literal: { reportValueRef: "business-value", other: 1 },
    strict: { reportValueRef: a },
    series: { seriesRef: b },
  });
  Object.assign(value, { chartSeries: { version: "shared-v1", items: {} } });
  expect(hydrateOpportunityReportValues(value)).toBe(value);
});

it("copies prototype-like keys as data without invoking a setter or polluting object prototypes", () => {
  const extension = JSON.parse(
    '{"__proto__":{"reportValueRef":"' +
      b +
      '"},"constructor":{"reportValueRef":"' +
      b +
      '"}}',
  );
  const value = context({ extension }, shared({ [b]: { safe: true } }));
  const result = reportOf(hydrateOpportunityReportValues(value))
    .extension as Record<string, unknown>;
  expect(Object.hasOwn(result, "__proto__")).toBe(true);
  expect(result.__proto__).toEqual({ safe: true });
  expect(result.constructor).toEqual({ safe: true });
  expect(Object.getPrototypeOf(result)).toBe(Object.prototype);
  expect(Object.prototype).not.toHaveProperty("safe");
});

it.each([
  null,
  [],
  {},
  { version: "shared-v1", items: {} },
  { version: "shared-v2", items: [] },
  { version: "shared-v2", items: null },
  shared({ ["A".repeat(64)]: {} }),
  shared({ "private-diagnostic": {} }),
])("rejects invalid table %j", (table) => invalid(context({}, table)));
it.each(["private-diagnostic", "g".repeat(64), a, 1, null])(
  "rejects malformed or missing reference %j",
  (key) => invalid(context({ value: { reportValueRef: key } }, shared({}))),
);
it("rejects reference-shaped objects with extra fields instead of losing their extensions", () =>
  invalid(
    context(
      { value: { reportValueRef: a, privateNote: "private-diagnostic" } },
      shared({ [a]: {} }),
    ),
  ));

it("detects direct and indirect reference cycles", () => {
  invalid(
    context(
      { value: { reportValueRef: a } },
      shared({ [a]: { reportValueRef: a } }),
    ),
  );
  invalid(
    context(
      { value: { reportValueRef: a } },
      shared({ [a]: { reportValueRef: b }, [b]: [{ reportValueRef: a }] }),
    ),
  );
});
it("detects object cycles in both tables and inline portions", () => {
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;
  invalid(context({ value: { reportValueRef: a } }, shared({ [a]: cyclic })));
  invalid(context({ cyclic }, shared({})));
});
it.each([
  undefined,
  NaN,
  Infinity,
  BigInt(1),
  () => true,
  new Date(),
  new Map(),
  new Set(),
  /x/,
  new (class Custom {
    value = 1;
  })(),
])("rejects non-JSON nested values including unused entries: %s", (value) =>
  invalid(context({}, shared({ [a]: { value } }))),
);
it("rejects accessors, symbols and sparse arrays without evaluating arbitrary code", () => {
  let invoked = false;
  const getter = Object.defineProperty({}, "value", {
    enumerable: true,
    get() {
      invoked = true;
      return "private-diagnostic";
    },
  });
  invalid(context({}, shared({ [a]: getter })));
  expect(invoked).toBe(false);
  invalid(
    context({}, shared({ [a]: { [Symbol("private-diagnostic")]: true } })),
  );
  invalid(context({}, shared({ [a]: new Array(3) })));
});
