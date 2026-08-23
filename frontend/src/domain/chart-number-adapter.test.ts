import { normalizeChartIndex } from "./chart-number-adapter";

it("normalizes decimal-string portfolio values for chart comparison without touching ledger values", () => {
  expect(normalizeChartIndex("1921840", "1768000")).toBeCloseTo(108.701357, 6);
});
