import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MacroCycleOverview from "./components/MacroCycleOverview";
import { buildCycleOverview } from "./macro-cycle-overview";
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
  render(<MacroCycleOverview data={data} />);
  const board = screen.getByRole("region", { name: "今日宏观总纲" });
  expect(
    within(board).getByRole("heading", { name: "宏观周期与今日总纲" }),
  ).toBeVisible();
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
  render(<MacroCycleOverview data={data} />);
  const summary = screen.getByText("核心行业周期与趋势 · 1 项");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(summary);
  expect(
    screen.getByRole("table", { name: "核心行业周期与趋势" }),
  ).toBeVisible();
  expect(screen.getByText("景气扩张")).toBeVisible();
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
  render(<MacroCycleOverview data={data} />);
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
  render(<MacroCycleOverview data={data} />);
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
