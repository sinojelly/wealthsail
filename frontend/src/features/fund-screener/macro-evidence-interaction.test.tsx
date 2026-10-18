import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import type {
  DailyResearch,
  Direction,
  MarketEvidence,
  OpportunityContext,
  OpportunitySeries,
} from "../../api/investment";
import MacroContextStrip from "./components/MacroContextStrip";

const macro = (
  code: string,
  value = 5,
): OpportunityContext["items"][number] => ({
  code,
  label: code === "PMI" ? "制造业 PMI" : code === "PPI_YOY" ? "PPI同比" : code,
  value,
  unit: code === "PMI" ? "" : "%",
  state: "READY",
  valueVerification: "VERIFIED",
  asOf: "2026-09-01",
  referencePeriod: "2026-09",
  publishedAt: null,
  source: "国家统计局",
  sourceUri: "https://www.stats.gov.cn/",
  periodChange: 1.2,
  benchmark: { value: 0, label: "同比涨跌参照" },
  interpretation: {
    level: "OBSERVED",
    momentum: "OBSERVED",
    summary: "已采集价格事实，需结合行业成本与利润研究。",
    limitations: [],
    methodVersion: "macro-research-v1",
  },
  history: [
    {
      asOf: "2026-08-01",
      value: value - 1.2,
      unit: code === "PMI" ? "" : "%",
      source: "国家统计局",
    },
    {
      asOf: "2026-09-01",
      value,
      unit: code === "PMI" ? "" : "%",
      source: "国家统计局",
    },
  ],
});
const series = (code: string): OpportunitySeries => ({
  code,
  label: code,
  value: 1.9,
  unit: "%",
  state: "READY",
  frequency: "DAILY",
  asOf: "2026-10-16",
  sourceName: "实际来源",
  sourceUri: "https://example.org/rates",
  publishedAt: null,
  retrievedAt: "2026-10-16T00:00:00Z",
  availableAt: "2026-10-16T00:00:00Z",
  timeBasis: "RETRIEVAL_ONLY",
  methodVersion: "actual-v1",
  changes: {
    previousValue: 1.8,
    change5Samples: null,
    change20Samples: null,
    changeUnit: "bp",
    median: null,
    percentile: null,
    sampleCount: 2,
  },
  history: [
    { date: "2026-10-15", value: 1.8, state: "READY" },
    { date: "2026-10-16", value: 1.9, state: "READY" },
  ],
});
const direction = (id: string): Direction => ({
  id,
  marketId: id.split(":")[0],
  marketLabel: "A股",
  styleId: id.split(":")[1],
  label: "研究方向",
  framework: "研究",
});
const energy: NonNullable<DailyResearch["report"]>["energy"][number] = {
  id: "energy:brent",
  code: "DCOILBRENTEU",
  label: "布伦特原油",
  value: 72.2,
  unit: "USD/barrel",
  frequency: "DAILY",
  asOf: "2026-10-16",
  sourceUri: "https://www.eia.gov/",
  retrievedAt: "2026-10-16T12:00:00Z",
  publishedAt: null,
  timeBasis: "OBSERVATION_ONLY",
  state: "READY",
  previousValue: 70.1,
  previousDate: "2026-10-15",
  change: 2.1,
  changePct: 0.02996,
  sampleCount: 100,
};
function withEnergy(row = energy): DailyResearch {
  return {
    methodVersion: "daily-v1",
    state: "READY",
    runId: "report-1",
    scheduledFor: null,
    startedAt: null,
    completedAt: null,
    asOf: null,
    nextRunAt: null,
    report: {
      summary: "实际日报",
      coverage: [],
      sources: [],
      events: [],
      upcoming: [],
      energy: [row],
    },
  };
}
function show(
  data: OpportunityContext,
  directionId?: string,
  selectedIndexId?: string,
  months = 36,
) {
  const element = (
    next = data,
    nextDirection = directionId,
    nextIndex = selectedIndexId,
    nextMonths = months,
  ) => (
    <MemoryRouter initialEntries={["/?researchOpen=1&researchTab=macro"]}>
      <MacroContextStrip
        data={next}
        error={false}
        selectedDirection={nextDirection ? direction(nextDirection) : undefined}
        selectedIndexId={nextIndex}
        months={nextMonths}
      />
    </MemoryRouter>
  );
  const view = render(element());
  return (
    next = data,
    nextDirection = directionId,
    nextIndex = selectedIndexId,
    nextMonths = months,
  ) => view.rerender(element(next, nextDirection, nextIndex, nextMonths));
}
const open = (name: string) =>
  userEvent.setup().click(screen.getByRole("button", { name }));
const full = (label: string) => open(`查看${label}完整证据`);

it("keeps the real PPI graph next to its core grid with matching accessible controls", async () => {
  show({ state: "READY", items: [macro("PPI_YOY")] });
  await full("PPI同比");
  const graph = screen.getByRole("img", { name: "PPI同比 历史走势" });
  expect(graph.querySelectorAll("circle")).toHaveLength(2);
  expect(graph).toHaveAttribute("height", "280");
  const detail = screen.getByRole("region", { name: "PPI同比证据详情" });
  expect(
    within(screen.getByRole("region", { name: "核心指标" })).getByRole("img"),
  ).toBe(graph);
  expect(detail.closest(".op-compact-metrics")).toBeNull();
  expect(
    screen.getByRole("button", { name: "收起PPI同比完整证据" }),
  ).toHaveAttribute("aria-controls", detail.id);
  expect(
    within(detail).getByText("连续月变化：+1.20 个百分点"),
  ).toBeInTheDocument();
});

it("allows all remaining evidence groups and pending evidence to stay expanded independently", async () => {
  show({
    state: "READY",
    items: [macro("PMI"), macro("CPI_YOY"), macro("M2_YOY"), macro("PPI_YOY")],
    series: [
      series("CN_T10"),
      series("M1_YOY"),
      series("TSF_STOCK_YOY"),
      series("SHIBOR_7D"),
      series("HOUSE_HAIKOU_NEW_MOM"),
      series("DXY"),
      { ...series("DR007"), value: null, state: "NOT_COLLECTED" },
    ],
  });
  const labels = ["通胀与成本", "利率与信用", "四城地产", "全球风险与市场"];
  for (const [index, label] of labels.entries()) {
    await open(label);
    for (const expanded of labels.slice(0, index + 1)) {
      const button = screen.getByRole("button", { name: expanded });
      expect(button).toHaveAttribute("aria-expanded", "true");
      expect(
        document.getElementById(button.getAttribute("aria-controls")!),
      ).toBeVisible();
    }
  }
  await open("待补充证据");
  await open("利率与信用");
  expect(screen.getByRole("button", { name: "利率与信用" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  for (const label of [
    "通胀与成本",
    "四城地产",
    "全球风险与市场",
    "待补充证据",
  ]) {
    expect(screen.getByRole("button", { name: label })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  }
  await open("利率与信用");
  expect(screen.getByRole("button", { name: "利率与信用" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
});

it("keeps grouped PPI detail open when other groups toggle and clears it when its own group collapses", async () => {
  show({
    state: "READY",
    items: [macro("PMI"), macro("CPI_YOY"), macro("M2_YOY"), macro("PPI_YOY")],
    series: [
      series("CN_T10"),
      series("M1_YOY"),
      series("TSF_STOCK_YOY"),
      series("DXY"),
    ],
  });
  await open("通胀与成本");
  await full("PPI同比");
  const inflation = screen.getByRole("region", { name: "通胀与成本证据" });
  expect(
    within(inflation).getByRole("img", { name: "PPI同比 历史走势" }),
  ).toBeInTheDocument();
  await open("全球风险与市场");
  expect(
    screen.getByRole("img", { name: "PPI同比 历史走势" }),
  ).toBeInTheDocument();
  await open("全球风险与市场");
  expect(
    screen.getByRole("img", { name: "PPI同比 历史走势" }),
  ).toBeInTheDocument();
  await open("通胀与成本");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await open("通胀与成本");
  expect(
    screen.getByRole("button", { name: "查看PPI同比完整证据" }),
  ).toHaveAttribute("aria-expanded", "false");
  await full("PPI同比");
  await open("通胀与成本");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("uses distinct indicator identities across macro and market series sharing a code", async () => {
  show({
    state: "READY",
    items: [macro("PPI_YOY")],
    series: [{ ...series("PPI_YOY"), label: "另一个来源序列" }],
  });
  await userEvent.click(screen.getByText("其他来源与口径 · PPI同比"));
  expect(
    screen.getByRole("img", { name: "另一个来源序列 历史观察" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("img", { name: "PPI同比 历史走势" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "查看PPI同比完整证据" }),
  ).toHaveAttribute("aria-expanded", "false");
});

it("clears selected evidence when direction or observation window changes", async () => {
  const data: OpportunityContext = {
    state: "READY",
    items: [macro("PPI_YOY")],
  };
  const rerender = show(data, "a-share:broad");
  await full("PPI同比");
  rerender(data, "a-share:cyclical");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await full("PPI同比");
  rerender(data, "a-share:cyclical", undefined, 12);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("does not revive a removed selection when the indicator returns", async () => {
  const data: OpportunityContext = {
    state: "READY",
    items: [macro("PPI_YOY"), macro("PMI")],
  };
  const rerender = show(data);
  await full("PPI同比");
  rerender({ ...data, items: [data.items[1]] });
  rerender(data);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("preserves zero and old observations while merging missing, conflicted and unverified evidence", async () => {
  show({
    state: "READY",
    items: [
      macro("CPI_YOY", 0),
      { ...macro("PMI", 50), state: "STALE" },
      { ...macro("PPI_YOY"), state: "CONFLICT", valueVerification: "CONFLICT" },
      { ...macro("M2_YOY"), valueVerification: "PENDING", state: "UNVERIFIED" },
      {
        ...macro("UNKNOWN", Number.NaN),
        valueVerification: "UNAVAILABLE",
        state: "UNAVAILABLE",
      },
    ],
    series: [{ ...series("CN_T10"), value: null, state: "NOT_COLLECTED" }],
  });
  const core = screen.getByRole("region", { name: "核心指标" });
  expect(within(core).getAllByRole("article")).toHaveLength(2);
  expect(within(core).getByText("0 %")).toBeInTheDocument();
  expect(within(core).getByText(/2026年9月.*旧期证据/)).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "查看PPI同比完整证据" }),
  ).not.toBeInTheDocument();
  await open("待补充证据");
  const pending = screen.getByRole("region", { name: "待补充证据清单" });
  expect(within(pending).getByText(/PPI同比.*证据冲突/)).toBeInTheDocument();
  expect(within(pending).getByText(/M2_YOY.*待核对/)).toBeInTheDocument();
  await full("PPI同比");
  expect(
    within(pending).getByRole("img", { name: "PPI同比 历史走势" }),
  ).toBeInTheDocument();
});

it("does not force unavailable observations into six primary cards", () => {
  show({
    state: "UNAVAILABLE",
    items: [
      {
        ...macro("PMI"),
        value: null,
        state: "UNAVAILABLE",
        valueVerification: "UNAVAILABLE",
      },
    ],
  });
  expect(
    within(screen.getByRole("region", { name: "核心指标" })).queryByRole(
      "article",
    ),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/尚无已采集的核心指标/)).toBeInTheDocument();
});

it("prioritizes PPI and actual energy for the existing cyclical direction", async () => {
  show(
    {
      state: "READY",
      items: [
        macro("PMI"),
        macro("CPI_YOY"),
        macro("M2_YOY"),
        macro("PPI_YOY"),
      ],
      dailyResearch: withEnergy(),
    },
    "a-share:cyclical",
  );
  const cards = within(
    screen.getByRole("region", { name: "核心指标" }),
  ).getAllByRole("article");
  expect(cards[0]).toHaveAccessibleName("PPI同比");
  expect(cards[1]).toHaveAccessibleName("布伦特原油");
  await full("布伦特原油");
  const graph = screen.getByRole("img", { name: "布伦特原油 两次实际观察" });
  expect(graph.querySelectorAll("circle")).toHaveLength(2);
  expect(screen.getByText(/仅展示前次与当前两次实际观察/)).toBeInTheDocument();
  expect(screen.queryByText(/100 个历史样本/)).not.toBeInTheDocument();
  expect(
    within(
      screen.getByRole("region", { name: "布伦特原油证据详情" }),
    ).getByText("相邻变化：+2.100 美元/桶"),
  ).toBeInTheDocument();
});

it.each([
  [null, "2026-10-15", /前次观察缺少有效数值/],
  [70.1, "bad-date", /观察日期无效/],
  [70.1, "2026-10-17", /前次观察应早于当前观察/],
] as const)(
  "explains why energy with previous value %s and date %s cannot form a chart",
  async (previousValue, previousDate, reason) => {
    show(
      {
        state: "READY",
        items: [],
        dailyResearch: withEnergy({ ...energy, previousValue, previousDate }),
      },
      "a-share:cyclical",
    );
    await full("布伦特原油");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();
  },
);

const market = (indexId: string, label: string): MarketEvidence => ({
  indexId,
  label,
  state: "PARTIAL",
  asOf: "2026-10-16",
  historyQuality: "CONFLICT",
  trendWindow: { start: "2025-10-01", end: "2026-10-16", sampleCount: 250 },
  valuation: {
    state: "PARTIAL",
    pe: {
      state: "INSUFFICIENT",
      latest: 13.2,
      sampleCount: 1,
      sourceUri: "https://example.org/pe",
    },
  },
  trend: {
    state: "READY",
    ma200: 3900,
    distanceMA200: -0.01,
    momentum63: -0.02,
  },
  risk: { state: "UNKNOWN" },
  earnings: { state: "UNKNOWN" },
  missingEvidence: ["企业盈利"],
  sourceUri: "https://example.org/index",
});
it("puts only the selected broad index trend and independent PE ahead of macro evidence", async () => {
  show(
    {
      state: "READY",
      items: [macro("PMI")],
      marketEvidence: [market("hs300", "沪深300"), market("csi500", "中证500")],
    },
    "a-share:broad",
    "csi500",
  );
  const core = screen.getByRole("region", { name: "核心指标" });
  expect(within(core).getAllByRole("article")[0]).toHaveAccessibleName(
    "中证500价格趋势",
  );
  expect(within(core).getAllByRole("article")[1]).toHaveAccessibleName(
    "中证500PE",
  );
  expect(within(core).queryByText(/沪深300/)).not.toBeInTheDocument();
  expect(within(core).getByText(/相对MA200：-1%/)).toBeInTheDocument();
  expect(within(core).getByText(/PE倍数为独立事实/)).toBeInTheDocument();
  await full("中证500价格趋势");
  expect(screen.getByText(/250 个实际样本/)).toBeInTheDocument();
  expect(screen.getByText(/当前窗口不代表完整历史已核验/)).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("explains genuinely missing PPI history instead of mounting an empty chart", async () => {
  show({ state: "READY", items: [{ ...macro("PPI_YOY"), history: [] }] });
  await full("PPI同比");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.getByText(/历史序列尚未采集/)).toBeInTheDocument();
});

it("never promotes stale but conflicted or unverified values into collected primary evidence", async () => {
  show({
    state: "STALE",
    items: [
      { ...macro("PPI_YOY"), state: "STALE", valueVerification: "CONFLICT" },
      { ...macro("CPI_YOY"), state: "STALE", valueVerification: "PENDING" },
    ],
  });
  expect(
    within(screen.getByRole("region", { name: "核心指标" })).queryByRole(
      "article",
    ),
  ).not.toBeInTheDocument();
  await open("待补充证据");
  expect(screen.getByText(/PPI同比.*证据冲突/)).toBeInTheDocument();
  expect(screen.getByText(/CPI_YOY.*待核对/)).toBeInTheDocument();
});

it("keeps the selected indicator identity when input ordering changes", async () => {
  const first = macro("PPI_YOY"),
    second = macro("PMI");
  const data: OpportunityContext = { state: "READY", items: [first, second] };
  const rerender = show(data);
  await full("PPI同比");
  rerender({ ...data, items: [second, first] });
  expect(
    screen.getByRole("img", { name: "PPI同比 历史走势" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "查看制造业 PMI完整证据" }),
  ).toHaveAttribute("aria-expanded", "false");
});

it("keeps the actual permission reason beside an unavailable series", async () => {
  show({
    state: "READY",
    items: [],
    series: [{ ...series("DXY"), value: null, state: "PERMISSION_REQUIRED" }],
  });
  await open("待补充证据");
  expect(screen.getByText(/DXY.*需要来源授权/)).toBeInTheDocument();
});

it.each([
  [[{ asOf: "bad-date", value: 3.8 }], /观察日期无效/],
  [[{ asOf: "2026-08-01", value: Number.NaN }], /观察数值无效/],
  [[{ asOf: "2026-08-01", value: 3.8, state: "CONFLICT" }], /冲突或不可用观察/],
] as const)(
  "explains unusable PPI history without misreporting it as uncollected",
  async (history, reason) => {
    show({
      state: "READY",
      items: [{ ...macro("PPI_YOY"), history: [...history] }],
    });
    await full("PPI同比");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();
  },
);
