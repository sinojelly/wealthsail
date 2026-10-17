import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import type { DailyResearch, OpportunityContext } from "../../api/investment";
import MacroContextStrip from "./components/MacroContextStrip";

const data = {
  state: "PARTIAL",
  items: [
    {
      code: "PMI",
      label: "制造业PMI",
      value: 50.1,
      unit: "指数点",
      state: "READY",
      source: "国家统计局",
      asOf: "2026-09-01",
      publishedAt: null,
      history: [
        { asOf: "2026-08-01", value: 49.8, unit: "指数点" },
        { asOf: "2026-09-01", value: 50.1, unit: "指数点" },
      ],
    },
  ],
  marketEvidence: [
    {
      indexId: "hs300",
      label: "沪深300",
      state: "PARTIAL",
      asOf: "2026-10-15",
      sourceUri: null,
      valuation: { state: "PARTIAL", pe: { state: "PARTIAL", latest: 12.87 } },
      trend: { state: "READY", ma200: 4000, momentum63: 0.03 },
      risk: { state: "PARTIAL" },
      earnings: { state: "UNKNOWN" },
      missingEvidence: [],
    },
  ],
  researchOverview: {
    methodVersion: "daily-overview-v1",
    asOf: "2026-10-16T00:30:00Z",
    summary: "趋势条件仍待核验，独立PE只提供估值观察。",
    highlights: Array.from({ length: 4 }, (_, i) => ({
      id: `h${i}`,
      kind: i === 0 ? "INDEX_TREND" : i === 1 ? "INDEX_PE" : "OBSERVATION",
      title:
        i === 0
          ? "沪深300趋势观察"
          : i === 1
            ? "沪深300独立PE"
            : `具体观察${i + 1}`,
      summary:
        i === 0
          ? "MA200：4000.00；动量：3.00%；趋势条件待核验。"
          : i === 1
            ? "PE：12.87；仅供估值观察，不能认定便宜。"
            : `核验事实${i + 1}`,
      impact: "仍需独立评估盈利与估值。",
      tone: "BLUE",
      targetTab: "direction",
      indexId: "hs300",
      directionIds: ["a-share:broad"],
      sourceIds: [],
    })),
  },
  researchBoard: {
    methodVersion: "research-board-v2",
    asOf: "2026-10-16T00:30:00Z",
    summaries: [],
    items: [],
    direction: {
      label: "A股宽基",
      researchState: "WAIT_CONDITIONS",
      qualificationState: "INCOMPLETE",
      summary: "当前趋势事实可观察，资格仍未齐备。",
      nextStep: "继续核对盈利。",
      supports: [
        {
          label: "制造业",
          summary: "制造业景气待盈利验证",
          evidenceIds: ["PMI"],
          state: "PARTIAL",
        },
      ],
      counterFactors: [
        {
          label: "估值",
          summary: "价格可能已反映部分预期",
          evidenceIds: [],
          state: "UNKNOWN",
        },
      ],
      relatedIndexIds: ["hs300"],
      failedConditions: [],
      recommendedChecks: [],
      blockerIds: [],
      itemIds: [],
    },
    dataStatus: {
      itemIds: [],
      groups: [],
      collectedAt: null,
      nextCheckAt: null,
      sourceResults: {},
      methodVersions: [],
    },
  },
  notices: [],
} as unknown as OpportunityContext;
function Navigation() {
  const location = useLocation(),
    navigate = useNavigate();
  return (
    <>
      <output aria-label="研究URL">{location.search}</output>
      <button onClick={() => navigate(-1)}>研究后退</button>
    </>
  );
}
function show(value = data, path = "/fund-screener", props = {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MacroContextStrip
        data={value}
        error={false}
        scopeKey="actor:hs300"
        {...props}
      />
      <Navigation />
    </MemoryRouter>,
  );
}
async function open() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /今日投资观察/ }));
  return user;
}
it("starts with one concise collapsed judgment and defers the observation cards and research controls", () => {
  show();
  expect(screen.getByRole("button", { name: /今日投资观察/ })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  expect(
    screen.getByText("趋势条件仍待核验，独立PE只提供估值观察。"),
  ).toBeVisible();
  expect(
    screen.queryByRole("list", { name: "今日投资重点" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("沪深300趋势观察")).not.toBeInTheDocument();
  expect(screen.queryByText("具体观察4")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /跟踪/ }),
  ).not.toBeInTheDocument();
});
it("opens three direct categories and mounts requested evidence while keeping trend and independent PE together in the current observation", async () => {
  show();
  const user = await open();
  expect(screen.getAllByRole("tab")).toHaveLength(3);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "市场证据" }));
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "查看制造业PMI完整证据" }),
  );
  expect(
    screen.getByRole("img", { name: "制造业PMI 历史走势" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("制造业景气待盈利验证")).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "今日重点" }));
  await user.click(screen.getByText("沪深300趋势观察"));
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.queryByText("制造业景气待盈利验证")).not.toBeInTheDocument();
  expect(screen.getByText(/MA200：4000.00/)).toBeInTheDocument();
  expect(screen.getByText(/PE：12.87/)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "市场证据" }));
  await user.click(screen.getByRole("button", { name: "数据状态" }));
  expect(screen.getAllByRole("row")).toHaveLength(6);
  expect(screen.queryByText("制造业景气待盈利验证")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("restores a query deep link and preserves the full fund identity, filters and history navigation", async () => {
  show(
    data,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-id&fundMarket=SSE&macroMonths=60&keyword=沪深&page=2&researchOpen=1&researchTab=direction",
  );
  expect(screen.getByRole("tab", { name: "今日重点" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "市场证据" }));
  const query = new URLSearchParams(
    screen.getByLabelText("研究URL").textContent!,
  );
  expect(query.get("fundMarket")).toBe("SSE");
  expect(query.get("instrument")).toBe("fund-id");
  expect(query.get("macroMonths")).toBe("60");
  expect(query.get("keyword")).toBe("沪深");
  expect(query.get("page")).toBe("2");
  expect(query.get("researchTab")).toBe("macro");
  await user.click(screen.getByRole("button", { name: "研究后退" }));
  expect(screen.getByRole("tab", { name: "今日重点" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});
it("does not fill empty modules or describe unknown holdings as safe when the daily report is absent", async () => {
  const legacy = { ...data, researchOverview: undefined };
  show(legacy);
  expect(screen.queryByText("持仓风险")).not.toBeInTheDocument();
  expect(screen.queryByText(/无风险|可以买入/)).not.toBeInTheDocument();
  await open();
  await userEvent.click(screen.getByRole("tab", { name: "市场证据" }));
  expect(screen.getByRole("region", { name: "核心指标" })).toHaveTextContent(
    "50.1 指数点",
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

const daily: DailyResearch = {
  methodVersion: "daily-research-v1",
  state: "FAILED",
  runId: "today-run",
  reportRunId: "ready-old",
  reportCompletedAt: "2026-10-15T01:00:00Z",
  scheduledFor: "2026-10-16T00:30:00Z",
  startedAt: "2026-10-16T00:35:00Z",
  completedAt: "2026-10-16T00:36:00Z",
  asOf: "2026-10-15T01:00:00Z",
  nextRunAt: "2026-10-17T00:30:00Z",
  errorClass: "ResearchUnavailable",
  report: {
    summary: "历史报告保留可核查线索",
    coverage: [
      {
        topic: "ENERGY_SHIPPING",
        status: "NOT_FOUND",
        summary: "尚未找到运输实物证据。",
      },
    ],
    sources: [
      {
        id: "s1",
        title: "来源原文",
        url: "https://ofac.treasury.gov/policy",
        quote: "政策原文的短引文",
        publishedDate: "2026-10-14",
        publishedAt: null,
        retrievedAt: "2026-10-15T01:00:00Z",
        verification: "SOURCE_CHECKED",
      },
    ],
    events: [
      {
        id: "e1",
        title: "某能源政策已宣布",
        summary: "<img src=x onerror=alert(1)>",
        phase: "ANNOUNCED",
        importance: "HIGH",
        sourceIds: ["s1"],
        channels: ["供应变化→运输成本"],
        affectedDirectionIds: [],
        scenarios: [
          {
            condition: "实际到货增加",
            impact: "成本压力可能缓解",
            counter: "运输瓶颈仍可能限制供给",
          },
        ],
        watchFor: ["实际运输量"],
        unknowns: ["到货规模未确认"],
        marketReaction: "UNVERIFIED",
        reasoningType: "MODEL_CONDITIONAL",
      },
    ],
    upcoming: [
      {
        id: "u1",
        title: "未来公布日程",
        phase: "PLANNED",
        scheduledDate: "2026-10-19",
        scheduledAt: null,
        sourceIds: ["s1"],
        affectedDirectionIds: [],
        watchFor: ["实际公布值"],
      },
    ],
    energy: [],
  },
};
it("separates failed daily execution, historical evidence, checked quotations and conditional impact without executing model HTML", async () => {
  show({ ...data, dailyResearch: daily });
  await open();
  const report = screen.getByRole("region", { name: "每日事件研究" });
  expect(
    within(report).getByRole("heading", { name: "重要事件" }),
  ).toBeInTheDocument();
  expect(
    within(report).getByText(/报告证据时间：2026年10月15日 09:00/),
  ).toBeInTheDocument();
  expect(
    within(report).getByText(/历史报告.*不代表今日已完成/),
  ).toBeInTheDocument();
  const user = userEvent.setup();
  await user.click(within(report).getByText("执行与报告状态"));
  expect(
    within(report).getByText(/本次任务：today-run.*今日研究失败/),
  ).toBeVisible();
  expect(within(report).getByText(/当前有效报告：ready-old/)).toBeVisible();
  const eventDetails = within(report)
    .getByText("查看事件依据与情景")
    .closest("details")!;
  expect(eventDetails).not.toHaveAttribute("open");
  await user.click(within(eventDetails).getByText("查看事件依据与情景"));
  expect(within(eventDetails).getByText("已宣布")).toBeVisible();
  expect(
    within(report).getByRole("heading", { name: "可能传导 · 模型条件推演" }),
  ).toBeInTheDocument();
  expect(
    within(eventDetails).getByText(/原文已核对.*不代表影响判断已核实/),
  ).toBeInTheDocument();
  expect(within(report).getByText("到货规模未确认")).toBeInTheDocument();
  expect(within(report).getByText(/市场反应.*未核验/)).toBeInTheDocument();
  expect(within(report).getByText(/2026年10月19日.*计划/)).toBeInTheDocument();
  expect(
    within(eventDetails).getByRole("link", { name: "来源原文" }),
  ).toHaveAttribute("href", "https://ofac.treasury.gov/policy");
  expect(
    within(report).getByText("<img src=x onerror=alert(1)>"),
  ).toBeInTheDocument();
  expect(report.querySelector("img")).toBeNull();
  expect(document.querySelector(".op-critical-impact")).toBeNull();
});

it("keeps weekly diesel distinct from daily oil and rounds supplied changes without inventing publication time", async () => {
  show({
    ...data,
    dailyResearch: {
      ...daily,
      report: {
        ...daily.report!,
        energy: [
          {
            id: "GASDESW",
            code: "GASDESW",
            label: "美国零售柴油",
            value: 3.769999999999996,
            unit: "USD/gallon",
            frequency: "WEEKLY",
            state: "READY",
            asOf: "2026-10-12",
            previousDate: "2026-10-05",
            previousValue: 3,
            change: 0.769999999999996,
            changePct: 25.666,
            sampleCount: 2,
            sourceUri: "https://fred.stlouisfed.org/series/GASDESW",
            retrievedAt: "2026-10-16T00:35:00Z",
            publishedAt: null,
            timeBasis: "OBSERVATION_ONLY",
          },
        ],
      },
    },
  });
  await open();
  expect(
    screen.queryByRole("region", { name: "能源事实" }),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "市场证据" }));
  await userEvent.click(screen.getByRole("button", { name: "通胀与成本" }));
  const energy = screen.getByRole("region", { name: "能源事实" });
  expect(within(energy).getByText("3.770 美元/加仑")).toBeInTheDocument();
  expect(
    within(energy).getByText(/相邻变化：0.770 美元\/加仑/),
  ).toBeInTheDocument();
  expect(within(energy).getByText(/周度实际观察/)).toBeInTheDocument();
  expect(
    within(energy).getByText(/观察日期不代表已核实的公布时刻/),
  ).toBeInTheDocument();
  expect(screen.getAllByRole("region", { name: "能源事实" })).toHaveLength(1);
  expect(
    screen.queryByRole("region", { name: "每日事件研究" }),
  ).not.toBeInTheDocument();
});

it("labels the effective report separately from each observation and rejects unsubstantiated red highlights", async () => {
  show({
    ...data,
    researchOverview: {
      ...data.researchOverview!,
      highlights: [
        {
          ...data.researchOverview!.highlights[0],
          tone: "RED",
          asOf: "2026-09",
        },
      ],
    },
  });
  expect(screen.getByText(/暂无有效日报/)).toBeInTheDocument();
  await open();
  await userEvent.click(screen.getByText("沪深300趋势观察"));
  expect(screen.getByText("依据期：2026年9月")).toBeInTheDocument();
  expect(screen.getByText("沪深300趋势观察")).not.toHaveClass("op-tone-red");
  expect(document.querySelector(".op-critical-impact")).toBeNull();
});
