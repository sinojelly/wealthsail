import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MacroCycleOverview from "./components/MacroCycleOverview";
import {
  buildCycleOverview,
  cycleTrend,
  vixBand,
} from "./macro-cycle-overview";
import type { OpportunityContext } from "../../api/investment";

function context() {
  return {
    state: "READY",
    items: [],
    dailyResearch: {
      methodVersion: "daily-research-v1",
      runId: "cycle-run",
      scheduledFor: "2026-11-13T07:01:00+08:00",
      startedAt: "2026-11-13T07:01:00+08:00",
      completedAt: "2026-11-13T07:08:00+08:00",
      nextRunAt: null,
      state: "READY",
      reportRunId: "cycle-run",
      asOf: "2026-11-13T07:08:00+08:00",
      report: {
        summary: "关注盈利与资金传导",
        macroCycle: "政策托底，市场结构分化。",
        coverage: [],
        sources: [],
        events: [],
        upcoming: [],
        energy: [],
        cycleInsights: {
          version: "macro-cycle-v1",
          globalLiquidity: {
            stage: "高利率平台期",
            summary: "市场预期与正式决议分开",
            evidenceKind: "MARKET_EXPECTATION",
            asOf: "2026-11-12",
            sourceIds: ["fed"],
          },
          domesticCycle: {
            stage: "信用修复观察期",
            summary: "看社融与政策传导",
            evidenceKind: "MODEL_ASSESSMENT",
            sourceIds: [],
          },
          marketRegime: {
            stage: "结构分化",
            summary: "盈利与估值分化",
            action: "接近买区再分批观察",
            evidenceKind: "MODEL_ASSESSMENT",
            sourceIds: [],
          },
          keyFactors: [
            {
              title: "议息预期变化",
              summary: "尚非已公布决议",
              evidenceKind: "MARKET_EXPECTATION",
              sourceIds: [],
            },
          ],
          industries: [
            {
              industryId: "MEMORY",
              label: "存储",
              stage: "景气扩张",
              driver: "订单变化",
              evidenceKind: "MODEL_ASSESSMENT",
              sourceIds: [],
            },
          ],
        },
      },
    },
  } as OpportunityContext;
}

it.each([
  [14.99, 0],
  [15, 1],
  [25, 2],
  [35, 3],
])("classifies VIX %s at the published display boundary", (value, expected) => {
  expect(vixBand(value)).toBe(expected);
});

it("shows a VIX close and daily history apart from the liquidity gauge", () => {
  const data = context();
  data.series = [
    {
      code: "VIX",
      label: "VIX",
      frequency: "DAILY",
      unit: "点",
      value: 35,
      state: "READY",
      asOf: "2026-11-12",
      sourceUri: "https://fred.stlouisfed.org/series/VIXCLS",
      sourceName: "Cboe",
      publishedAt: null,
      retrievedAt: "2026-11-13T06:00:00+08:00",
      availableAt: null,
      timeBasis: "RETRIEVAL_ONLY",
      methodVersion: null,
      changes: {
        previousValue: null,
        change5Samples: null,
        change20Samples: null,
        changeUnit: "点",
        median: null,
        percentile: null,
        sampleCount: 4,
      },
      history: [9, 10, 11, 12].map((day) => ({
        date: `2026-11-${String(day).padStart(2, "0")}`,
        value: day === 12 ? 35 : 22,
        state: "READY",
      })),
    },
  ];
  render(<MacroCycleOverview data={data} />);
  const volatility = screen.getByRole("region", { name: "VIX 市场波动" });
  expect(
    within(volatility).getByRole("img", { name: "VIX 近两年收盘走势" }),
  ).toBeVisible();
  expect(within(volatility).getByText("极度恐慌")).toHaveAttribute(
    "data-active",
    "true",
  );
  expect(
    within(volatility).getByRole("link", { name: "Cboe / FRED" }),
  ).toHaveAttribute("href", "https://fred.stlouisfed.org/series/VIXCLS");
  expect(
    screen.getByRole("region", { name: "流动性松紧" }),
  ).not.toHaveTextContent("极度恐慌");
});

it("shows separate global, domestic, market and key-factor rows without inventing odds", () => {
  const data = context();
  data.dailyResearch!.report!.sources = [
    {
      id: "fed",
      url: "https://www.federalreserve.gov/monetarypolicy/openmarket.htm",
      title: "政策决议",
      quote: "决议正文",
      publishedDate: "2026-11-12",
      publishedAt: null,
      retrievedAt: null,
      verification: "SOURCE_CHECKED",
    },
  ];
  const { rerender } = render(<MacroCycleOverview data={data} />);
  const board = screen.getByRole("region", { name: "今日宏观总纲" });
  expect(
    within(board).getByRole("heading", { name: "宏观周期与今日总纲" }),
  ).toBeVisible();
  expect(board).toHaveTextContent("全球流动性压力与风险走势图");
  expect(board).toHaveTextContent("VIX 市场波动");
  expect(board).not.toHaveTextContent("联储表态与机构预测");
  expect(board).toHaveTextContent("接近买区再分批观察");
  rerender(<MacroCycleOverview data={data} factView />);
  expect(board).toHaveTextContent("全球利率与流动性");
  expect(board).toHaveTextContent("国内货币、财政与信用");
  expect(board).toHaveTextContent("市场阶段与操作总纲");
  expect(board).toHaveTextContent("今日关键影响因素");
  expect(board).toHaveTextContent("接近买区再分批观察");
  expect(board).not.toHaveTextContent("100%");
  const global = within(board).getByRole("group", { name: "全球利率与流动性" });
  expect(global).toHaveTextContent("市场预期");
  expect(global).toHaveTextContent("2026年11月12日");
  expect(
    within(global).getByRole("link", { name: "政策决议" }),
  ).toHaveAttribute("href", expect.stringContaining("federalreserve.gov"));
});

it("keeps all industry rows behind a compact, accessible disclosure", async () => {
  const data = context();
  render(<MacroCycleOverview data={data} factView />);
  const summary = screen.getByText("核心行业周期与趋势 · 1 项");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(summary);
  expect(
    screen.getByRole("table", { name: "核心行业周期与趋势" }),
  ).toBeVisible();
  expect(screen.getByText("景气扩张")).toBeVisible();
});

it("plots dated local macro history and excludes observations collected after the report cutoff", () => {
  const data = context();
  data.dailyResearch!.report!.asOf = "2026-11-13T07:08:00+08:00";
  data.items = [
    {
      code: "PMI",
      label: "制造业PMI",
      value: 55,
      asOf: "2026-11-01",
      publishedAt: "2026-11-30T09:30:00+08:00",
      source: "统计局",
      state: "READY",
      history: [
        { asOf: "2023-11-01", value: 49.4, publishedAt: "2023-11-30" },
        { asOf: "2026-05-01", value: 50.4, publishedAt: "2026-05-31" },
        { asOf: "2026-06-01", value: 50.3, publishedAt: "2026-06-30" },
        {
          asOf: "2026-09-01",
          value: 50.1,
          publishedAt: "2026-09-30T09:30:00+08:00",
        },
        {
          asOf: "2026-10-01",
          value: 49.3,
          publishedAt: "2026-10-31T09:30:00+08:00",
        },
        {
          asOf: "2026-11-01",
          value: 55,
          publishedAt: "2026-11-30T09:30:00+08:00",
        },
      ],
    },
  ];
  const cycle = data.dailyResearch!.report!.cycleInsights!;
  cycle.policySignals = [
    {
      text: "官员表示仍须观察价格压力",
      title: "隔夜要闻",
      sourceUri: "https://example.test/fed",
      asOf: "2026-11-12",
      kind: "CENTRAL_BANK_REMARK",
    },
  ];
  expect(
    cycleTrend(data, "PMI", data.dailyResearch!.report!.asOf).map(
      (row) => row.value,
    ),
  ).toEqual([49.4, 50.4, 50.3, 50.1, 49.3]);
  data.items.push({
    code: "CPI_YOY",
    label: "CPI",
    value: 0.7,
    asOf: "2026-10-01",
    publishedAt: "2026-11-09",
    source: "统计局",
    state: "READY",
    history: [
      { asOf: "2026-09-01", value: 0.5, publishedAt: "2026-10-10" },
      { asOf: "2026-10-01", value: 0.7, publishedAt: "2026-11-09" },
    ],
  });
  const { container } = render(<MacroCycleOverview data={data} historical />);
  expect(screen.getByRole("img", { name: "景气与货币活化趋势" })).toBeVisible();
  expect(screen.getByRole("img", { name: "上下游通胀剪刀差" })).toBeVisible();
  expect(
    container.querySelector('[data-macro-series="CPI_YOY"] path'),
  ).toHaveAttribute("d", expect.stringContaining(" L"));
  expect(screen.getByText("2023-11")).toBeInTheDocument();
  expect(container.querySelector('[data-macro-gap="PMI"]')).toBeInTheDocument();
  expect(screen.getByText("0.70%")).toBeVisible();
  expect(
    screen.getByText("官员表示仍须观察价格压力", { exact: false }),
  ).toBeVisible();
  expect(screen.getByText("偏冷收缩", { exact: false })).toHaveAttribute(
    "data-active",
    "true",
  );
  expect(screen.queryByText("55.0")).not.toBeInTheDocument();
});

it("highlights sourced CME distributions and moves audit text into a closed panel", () => {
  const data = context();
  const cycle = data.dailyResearch!.report!.cycleInsights!;
  cycle.fedRateExpectations = [
    {
      meetingMonth: "2026-12",
      cumulative: false,
      complete: true,
      hikeProbability: 100,
      cutProbability: 0,
      holdProbability: 0,
      expectedChangeBp: 28.8,
      outcomes: [
        { changeBp: 25, probability: 84.7 },
        { changeBp: 50, probability: 15.3 },
      ],
      sourceUri: "https://www.cmegroup.com/fedwatch",
    },
  ];
  cycle.auditNotes = ["统计口径调整处断开"];
  render(<MacroCycleOverview data={data} />);
  const pricing = screen.getByLabelText("CME 利率期货定价");
  expect(pricing).toHaveTextContent("加息总概率 100%");
  expect(pricing).toHaveTextContent("+25bp 84.7% | +50bp 15.3% · 期望 +28.8bp");
  expect(within(pricing).getByRole("link", { name: "来源" })).toBeVisible();
  const disclosure = screen
    .getByText("ⓘ 数据口径与审计说明（1项）")
    .closest("details")!;
  expect(disclosure).not.toHaveAttribute("open");
  expect(
    within(disclosure).getByText("统计口径调整处断开"),
  ).toBeInTheDocument();
});

it("labels legacy prose and industry evidence without fabricating a structured rate cycle", () => {
  const data = context();
  delete data.dailyResearch!.report!.cycleInsights;
  data.dailyResearch!.report!.valueDirections = [
    {
      key: "ai",
      title: "AI硬件",
      thesis: "订单仍需兑现",
      industryCycle: {
        stage: "POLICY_CATALYST",
        evidenceSummary: "政策与产业事件催化",
      },
      sourceIds: [],
      macroCodes: [],
      watchFor: [],
      plans: [],
      signal: "WATCH",
    },
  ];
  const result = buildCycleOverview(data);
  expect(result.global).toBeNull();
  expect(result.market?.summary).toBe("政策托底，市场结构分化。");
  expect(result.market?.origin).toBe("原报告总纲");
  expect(result.industries[0]).toMatchObject({
    label: "AI硬件",
    stage: "政策/事件催化",
    origin: "原报告方向",
  });
  render(<MacroCycleOverview data={data} factView />);
  expect(screen.getByText("本报告未单列全球利率周期结论。")).toBeVisible();
  expect(screen.queryByText("高利率平台期")).not.toBeInTheDocument();
});

it("rejects empty/conflicting observations while preserving real zero and its data date", () => {
  const data = context();
  data.items = [
    {
      code: "PMI",
      label: "制造业PMI",
      value: 0,
      unit: "指数点",
      asOf: "2026-10-01",
      referencePeriod: "2026-10",
      publishedAt: null,
      source: null,
      state: "READY",
    },
    {
      code: "CPI_YOY",
      label: "CPI",
      value: null,
      unit: "%",
      asOf: "2026-10-01",
      publishedAt: null,
      source: null,
      state: "UNAVAILABLE",
    },
  ];
  data.alphaPublic = {
    observations: [
      {
        code: "CN_T10",
        value: "1.8",
        state: "CONFLICT",
        referenceDate: "2026-11-12",
      },
    ],
  } as OpportunityContext["alphaPublic"];
  render(<MacroCycleOverview data={data} factView />);
  expect(screen.getByText("0.0")).toBeVisible();
  expect(screen.getByText("2026年10月")).toBeVisible();
  expect(screen.queryByText("1.80")).not.toBeInTheDocument();
});

it("keeps a complete legacy thesis available and resets expansion for another report", async () => {
  const data = context();
  delete data.dailyResearch!.report!.cycleInsights;
  data.dailyResearch!.report!.macroCycle =
    "市场阶段仍需结合实际盈利判断。".repeat(30) + "最后风险边界。";
  const { rerender } = render(<MacroCycleOverview data={data} />);
  expect(screen.queryByText(/最后风险边界/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "展开今日总纲" }));
  expect(screen.getByText(/最后风险边界/)).toBeVisible();
  rerender(
    <MacroCycleOverview
      data={{
        ...data,
        dailyResearch: { ...data.dailyResearch!, reportRunId: "another-run" },
      }}
    />,
  );
  expect(screen.queryByText(/最后风险边界/)).not.toBeInTheDocument();
});
