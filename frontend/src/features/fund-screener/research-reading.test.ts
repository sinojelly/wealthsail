import { readingText, researchSummary } from "./research-reading";

it("renders internal indicator units and theme labels in Chinese with bounded decimal precision", () => {
  const original =
    "GEO_CRUDE_OIL；M1_M2_GAP=-4 percentage_points；CN_T10=1.6512345678901；PARTIAL";
  expect(readingText(original, 500)).toBe(
    "地缘与原油；M1与M2剪刀差=-4.0 个百分点；中国10年国债收益率=1.6512；部分依据可用",
  );
  expect(original).toContain("1.6512345678901");
});

it("keeps economic conclusions in the macro summary and removes collector diagnostics without rewriting originals", () => {
  const text = "信用边际修复。媒体零命中，当前抓取20条。仍需关注需求兑现。";
  expect(researchSummary(text)).toBe("信用边际修复。仍需关注需求兑现。");
  expect(text).toContain("媒体零命中");
});

it("reads first-plan diagnostics and mathematical parameter markup without rewriting source text or dollar amounts", () => {
  const text = String.raw`previousValueDirections为空，首次建立同方向计划。真实波幅周期 $H=14.0000$；相对强弱周期 $f=14.0000$；净值标准差窗口 $\Box=20.0000$；偏离200期均线上限 $\mathbb{R}=-0.0800$；净值绝对变化倍数 $\chi=1.4000$。价格 $20 与 $30；预算 $100。`;
  expect(readingText(text, 600)).toBe(
    "首次建立该方向基准计划。真实波幅周期 14；相对强弱周期 14；净值标准差窗口 20；偏离200期均线上限 -0.08；净值绝对变化倍数 1.4。价格 $20 与 $30；预算 $100。",
  );
  expect(text).toContain("previousValueDirections为空");
  expect(text).toContain(String.raw`$\Box=20.0000$`);
});

it("does not let a preceding dollar amount swallow a later parameter expression", () => {
  expect(readingText("预算$100，真实波幅周期 $H=14.0000$；目标$120", 200)).toBe(
    "预算$100，真实波幅周期 14；目标$120",
  );
});
