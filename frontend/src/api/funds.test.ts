import { fmtPercentileLabel } from "./funds";

it("labels a NAV range position without implying valuation", () => {
  expect(fmtPercentileLabel(72.5)).toBe("区间位置 72.50% · 区间偏高");
  expect(fmtPercentileLabel(null)).toBe("--");
});
