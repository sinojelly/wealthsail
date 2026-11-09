import { planNavSnapshot } from "./plan-nav-snapshot";
import { frozenPlan } from "./personal-funds.test-data";

it("uses the same disclosed series as the current price panel for the opportunity chart", () => {
  const plan = frozenPlan("000001");
  plan.series = {
    state: "READY",
    fundCode: "000001",
    asOf: plan.asOf,
    unit: "CNY/份",
    priceBasis: "UNIT_NAV",
    sourceUri: plan.sourceUri,
    retrievedAt: null,
    points: [
      { date: "2026-10-19", value: "1.0000" },
      { date: "2026-10-20", value: "1.1200" },
    ],
  };
  const snapshot = planNavSnapshot(plan, "基金");
  expect(snapshot?.points.at(-1)).toEqual({
    date: plan.asOf,
    unitNav: "1.1200",
  });
  expect(snapshot?.summary.percentilePct).toBe(75);
  expect(snapshot?.asOf).toBe(plan.asOf);
});

it("does not mix a different fund's series or estimated prices into a NAV chart", () => {
  const plan = frozenPlan("000001");
  plan.series = {
    state: "READY",
    fundCode: "000002",
    asOf: plan.asOf,
    unit: "CNY/份",
    priceBasis: "UNIT_NAV",
    sourceUri: null,
    retrievedAt: null,
    points: [{ date: "2026-10-20", value: "1.1200" }],
  };
  expect(planNavSnapshot(plan, "基金")).toBeUndefined();
});
