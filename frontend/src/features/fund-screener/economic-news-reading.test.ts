import {
  classifyEconomicNews,
  economicNewsReading,
  isEmptyCalendarStory,
} from "./economic-news-reading";

it("keeps the factual part of legacy editorial summaries and preserves the raw input", () => {
  const raw =
    "输入另报国联民生证券11月11日已完成互换便利及股票买入，属实施线索；年度策略会属于机构观点。银行与券商的收入、息差和减值传导分别验证。";
  const result = economicNewsReading("央行稳市安排，实施线索继续出现", raw, [
    { title: "央行三季度货政报告继续支持资本市场工具", quote: null },
  ]);
  expect(result.title).toBe("央行三季度货政报告继续支持资本市场工具");
  expect(result.summary).toBe("国联民生证券11月11日已完成互换便利及股票买入");
  expect(raw).toContain("属实施线索");
});

it("never turns an unconfirmed or negative factual statement into confirmed positive news", () => {
  const summary =
    "央行尚未宣布降息，不能当作政策已落地。英伟达盘后跌幅扩大至9%的传闻未获证实。";
  expect(economicNewsReading("央行政策观察", summary, []).summary).toBe(
    summary,
  );
});

it("does not replace an event with an unrelated or calendar source title", () => {
  expect(
    economicNewsReading("英国第三季度GDP", "英国第三季度GDP", [
      { title: "重点财经日历", quote: null },
    ]).title,
  ).toBe("英国第三季度GDP");
  expect(
    economicNewsReading("央行公布安排", "原新闻摘要", [
      { title: "核对原文", quote: null },
    ]).title,
  ).toBe("央行公布安排");
  expect(
    economicNewsReading("机器人行业风险分别研究", "制造业订单变化", [
      { title: "央行三季度货政报告继续支持资本市场工具", quote: null },
    ]).title,
  ).toBe("机器人行业风险");
});

it.each([
  ["英国第三季度GDP", "英国第三季度GDP", "国内经济", "国际局势"],
  ["美国上周初请与EIA库存", "财经日历", "待分类", "国际局势"],
  ["美联储维持利率不变", "中国出口企业可能受影响", "货币与政策", "国际局势"],
  [
    "中国出口企业应对美国关税",
    "美国关税影响中国企业订单",
    "国内经济",
    "国内经济",
  ],
  ["上海智能制造政策推进", "日本防务修订不能证明国内收入", "科技", "科技"],
  ["国内利率受美联储政策影响", "全球流动性变化", "货币与政策", "货币与政策"],
] as const)(
  "classifies the actual news subject: %s",
  (title, summary, original, expected) => {
    expect(classifyEconomicNews(title, summary, [], original)).toBe(expected);
  },
);

it("uses a linked foreign source only when the event has no explicit domestic subject", () => {
  expect(
    classifyEconomicNews(
      "第三季度GDP增长0.3%",
      "统计局发布数据",
      [{ title: "英国统计局第三季度GDP初值", quote: null }],
      "国内经济",
    ),
  ).toBe("国际局势");
  expect(
    classifyEconomicNews(
      "中国三季度GDP增长5%",
      "统计局发布数据",
      [{ title: "海外媒体：美国降息与中国增长", quote: null }],
      "国内经济",
    ),
  ).toBe("国内经济");
});

it("filters only empty planned calendar shells and retains substantive announced future measures", () => {
  expect(
    isEmptyCalendarStory("PLANNED", "英国第三季度GDP", "英国第三季度GDP"),
  ).toBe(true);
  expect(
    isEmptyCalendarStory("PLANNED", "美国初请、成屋销售与EIA库存", ""),
  ).toBe(true);
  expect(
    isEmptyCalendarStory(
      "PLANNED",
      "央行下月降准0.5个百分点",
      "央行下月降准0.5个百分点",
    ),
  ).toBe(false);
  expect(
    isEmptyCalendarStory(
      "PLANNED",
      "探路者卫星定于11月14日发射",
      "探路者卫星定于11月14日发射",
    ),
  ).toBe(false);
  expect(
    isEmptyCalendarStory(
      "PLANNED",
      "央行下周净买入1000亿国债",
      "央行下周净买入1000亿国债",
    ),
  ).toBe(false);
  expect(
    isEmptyCalendarStory(
      "PLANNED",
      "英国GDP发布",
      "英国统计局将公布GDP，市场预期环比增长0.3%",
    ),
  ).toBe(false);
  expect(isEmptyCalendarStory("REPORTED", "英国第三季度GDP", "")).toBe(false);
});
