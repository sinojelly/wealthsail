import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  ActionableZoneResult,
  DailyResearch,
  ValueDirection,
} from "../../api/investment";
import ValueDirections from "./components/ValueDirections";

const plan = (code = "006000"): ActionableZoneResult => ({
  methodVersion: "actionable-zones-v3",
  subjectKind: "nav",
  subjectId: code,
  fundCode: code,
  label: `行业研究基金${code}`,
  priceBasis: "UNIT_NAV",
  unit: "CNY/份",
  asOf: "2026-10-16",
  sourceUri: `https://fund.eastmoney.com/${code}.html`,
  currentPrice: "2.1108",
  volatilityMethod: "NAV_ABS_CHANGE_STD_V1",
  indicators: { ma20: "2.1098", atr14: null, navVolatility14: "0.0266" },
  parameters: { navAbsoluteChangeWindow: 14, navStdMultiplier: 0.65 },
  selectedTrack: "LEFT_VALUE",
  priceRevisionStatus: "INITIAL",
  priceRevisionNote: "previousValueDirections为空，首次建立同方向计划",
  tracks: [
    {
      track: "LEFT_VALUE",
      state: "CANDIDATE",
      entryZoneMin: "2.0724",
      entryZoneMax: "2.1404",
      stopLossPrice: "1.9851",
      takeProfitT1: "2.3761",
      takeProfitT2: "2.5231",
      priceState: "COMPLETE",
      reasons: ["超卖后等待需求与价格同步验证"],
      missingEvidence: [],
    },
  ],
});
const direction: ValueDirection = {
  key: "technology_recovery",
  title: "算力与存储产业链",
  thesis: "回购意向、经营增长与产业会议共同提供景气观察线索。",
  signal: "LEFT_VALUE",
  sourceIds: ["news:1", "news:2"],
  macroCodes: ["PMI"],
  macroEvidence: [
    {
      code: "PMI",
      value: 50.1,
      unit: "index_point",
      referencePeriod: "2026-09",
      sourceUri: "https://example.org/pmi",
    },
  ],
  watchFor: ["核查下次财报与回购进度"],
  plans: [plan(), plan("006001")],
};
const report = (directions: ValueDirection[] = [direction]) =>
  ({
    state: "READY",
    reportRunId: "report:today",
    report: {
      valueDirections: structuredClone(directions),
      sources: [
        {
          id: "news:1",
          title: "行业龙头回购提议",
          url: "https://example.org/buyback",
        },
        {
          id: "news:2",
          title: "相关企业限售股解禁",
          url: "https://example.org/unlock",
        },
      ],
    },
  }) as DailyResearch;

it("keeps one price table per fund and independently opens technical evidence without nested plans", async () => {
  const data = report();
  const original = JSON.stringify(data);
  render(<ValueDirections data={data} />);
  const cards = screen.getAllByRole("article", {
    name: /行业研究基金.*买卖计划/,
  });
  for (const card of cards) {
    const summary = within(card).getByText("查看技术指标与算法依据");
    const details = summary.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    await userEvent.click(summary);
    expect(details).toHaveAttribute("open");
    expect(within(details).getByText(/20期均线/)).toBeVisible();
    expect(
      within(details).getByText(/超卖后等待需求与价格同步验证/),
    ).toBeVisible();
    expect(
      within(details).getByRole("link", { name: "原始价格来源" }),
    ).toHaveAttribute(
      "href",
      card.textContent?.includes("006001")
        ? "https://fund.eastmoney.com/006001.html"
        : "https://fund.eastmoney.com/006000.html",
    );
    expect(details.querySelector("details")).toBeNull();
    expect(details.querySelector("dl")).toBeNull();
    expect(within(card).getAllByText("建议买入区间")).toHaveLength(1);
    expect(within(card).getAllByText("止盈目标一")).toHaveLength(1);
    expect(within(card).getAllByText("2.0724–2.1404")).toHaveLength(1);
    expect(card).not.toHaveTextContent("入场区间：");
    expect(
      within(card).getByRole("status", { name: "价格计划调整" }),
    ).toHaveTextContent("首次建立该方向基准计划");
  }
  expect(cards.every((card) => card.querySelector("details")?.open)).toBe(true);
  expect(JSON.stringify(data)).toBe(original);
});

it("shows compact macro references instead of repeating macro cards in every direction", async () => {
  const data = report();
  const original = JSON.stringify(data);
  render(<ValueDirections data={data} />);
  const card = screen.getByRole("region", { name: "算力与存储产业链" });
  expect(card.querySelector('[aria-label="该方向宏观依据"]')).toBeNull();
  const summary = within(card).getByText("综合依据与后续观察");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(summary);
  expect(within(card).getByText(/宏观参考：/)).toHaveTextContent(
    "制造业PMI 50.1 指数点（2026年9月）",
  );
  expect(
    within(card).getByRole("link", { name: "行业龙头回购提议" }),
  ).toBeVisible();
  expect(within(card).getByText("核查下次财报与回购进度")).toBeVisible();
  expect(JSON.stringify(data)).toBe(original);
});

it("keeps sourced catalysts and risks visible on the main card without copying unrelated sources", () => {
  const row = {
    ...direction,
    catalysts: [
      { summary: "同产业链多家公司提出回购计划", sourceIds: ["news:1"] },
    ],
    risks: [
      {
        summary: "相关公司即将集中解禁，观察潜在供给压力",
        sourceIds: ["news:2"],
      },
    ],
  };
  render(<ValueDirections data={report([row])} />);
  const catalyst = screen.getByRole("region", { name: "机会催化" });
  const risk = screen.getByRole("region", { name: "关键风险" });
  expect(catalyst).toBeVisible();
  expect(risk).toBeVisible();
  expect(catalyst).toHaveTextContent("同产业链多家公司提出回购计划");
  expect(risk).toHaveTextContent("相关公司即将集中解禁，观察潜在供给压力");
  expect(risk).toHaveClass("op-value-risks");
  expect(
    within(risk).getByRole("link", { name: /相关企业限售股解禁/ }),
  ).toHaveAttribute("href", "https://example.org/unlock");
  expect(
    within(risk).queryByRole("link", { name: /行业龙头回购提议/ }),
  ).not.toBeInTheDocument();
});

it("renders all six directions including useful unpriced observations and keeps their evidence folded", () => {
  const rows = Array.from({ length: 6 }, (_, index) => ({
    ...direction,
    key: `sector_${index}`,
    title: `不同产业机会${index + 1}`,
    signal: "WATCH" as const,
    plans: [],
  }));
  render(<ValueDirections data={report(rows)} />);
  const root = screen.getByRole("region", { name: "今日价值方向与买卖计划" });
  expect(within(root).getAllByRole("heading", { level: 3 })).toHaveLength(7);
  expect(within(root).getAllByText(/等待真实价格依据/)).toHaveLength(6);
  for (const summary of within(root).getAllByText("综合依据与后续观察")) {
    expect(summary.closest("details")).not.toHaveAttribute("open");
  }
});
