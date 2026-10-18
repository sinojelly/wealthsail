import { readingText } from "./research-reading";

it("renders internal indicator units and theme labels in Chinese with bounded decimal precision", () => {
  const original =
    "GEO_CRUDE_OIL；M1_M2_GAP=-4 percentage_points；CN_T10=1.6512345678901；PARTIAL";
  expect(readingText(original, 500)).toBe(
    "地缘与原油；M1与M2剪刀差=-4.0 个百分点；中国10年国债收益率=1.6512；部分依据可用",
  );
  expect(original).toContain("1.6512345678901");
});
