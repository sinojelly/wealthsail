import { navPercentile } from "./nav-percentile";

it("uses the selected NAV sequence with deterministic midrank ties", () => {
  expect(navPercentile([1, 1, 2, 2])).toBe(75);
  expect(navPercentile([2, 2])).toBe(50);
  expect(navPercentile([1, 3, 2])).toBe(50);
  expect(navPercentile([])).toBeNull();
});
