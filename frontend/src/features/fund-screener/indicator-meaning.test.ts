import { indicatorMeaning } from "./indicator-meaning";
it("explains a PMI level without granting investment eligibility", () => {
  expect(indicatorMeaning("PMI")).toContain("50");
  expect(indicatorMeaning("PMI")).not.toContain("可以买入");
});
it("distinguishes price change, financing and real-rate mechanisms", () => {
  expect(indicatorMeaning("CPI_YOY")).toContain("居民消费");
  expect(indicatorMeaning("M1_M2_GAP")).toContain("口径");
  expect(indicatorMeaning("US_T10_REAL")).toContain("实际利率");
  expect(indicatorMeaning("UNKNOWN")).toContain("仍需核查");
});
