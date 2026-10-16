import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import type { OpportunityContext, OverviewItem } from "../../api/investment";
import MacroContextStrip from "./components/MacroContextStrip";
import { RadarDetails } from "./components/OpportunityRadar";
import DailyResearchReport from "./components/DailyResearchReport";
const action = {
  type: "VIEW_DIRECTION" as const,
  label: "核查指数条件",
  target: "research-direction",
  href: null,
  enabled: true,
  disabledReason: null,
};
const item = (id: string): OverviewItem => ({
  id,
  kind: "OBSERVATION",
  title: `具体事项${id}`,
  summary: "原始事实需要条件核验。",
  impact: "并未形成买卖资格。",
  tone: "BLUE",
  targetTab: "direction",
  indexId: "hs300",
  directionIds: [],
  sourceIds: [],
  scope: "GLOBAL",
  stage: "EVENT_LEAD",
  asOf: "2026-10-16",
  nextAction: action,
});
const data = {
  state: "PARTIAL",
  items: ["PMI", "CPI_YOY", "PPI_YOY", "M2_YOY"].map((code) => ({
    code,
    label: code === "CPI_YOY" ? "居民消费价格同比" : code,
    value: code === "PMI" ? 50.10000000000001 : 0.9000000000001,
    unit: code === "PMI" ? "指数点" : "%",
    state: "READY",
    source: "公开来源",
    publishedAt: null,
    asOf: "2026-09-01",
    history: [
      { asOf: "2026-08-01", value: 0.8 },
      { asOf: "2026-09-01", value: 0.9 },
    ],
  })),
  series: ["US_T10_NOMINAL", "US_T10_REAL", "DXY"].map((code) => ({
    code,
    label: code,
    frequency: "DAILY",
    unit: "%",
    value: 3.10000000000001,
    state: "READY",
    asOf: "2026-10-16",
    sourceUri: null,
    changes: { sampleCount: 0 },
    history: [],
  })),
  researchOverview: {
    methodVersion: "research-overview-v2",
    asOf: "2026-10-17T00:00:00Z",
    summary: "存在全球事件线索，指数条件仍须分别核查。",
    evidenceIds: [],
    highlights: [],
    groups: [
      {
        id: "RISKS",
        label: "重要风险",
        state: "READY",
        summary: "全球条件性风险线索",
        nextAction: null,
        items: [item("global-1"), item("global-2"), item("global-3")],
      },
      {
        id: "OPPORTUNITIES",
        label: "研究机会",
        state: "READY",
        summary: "当前存在有限指数线索",
        nextAction: null,
        items: [item("index-1")],
      },
      {
        id: "HOLDINGS",
        label: "我的持仓",
        state: "UNKNOWN",
        summary: "持仓风险尚未核验，不能认定安全。",
        nextAction: {
          type: "VIEW_HOLDINGS",
          label: "核对我的资产",
          target: null,
          href: "/dashboard?scope=MINE",
          enabled: true,
          disabledReason: null,
        },
        items: [],
      },
      {
        id: "UPCOMING",
        label: "未来事件",
        state: "EMPTY",
        summary: "暂无可核验近期日程",
        nextAction: null,
        items: [],
      },
    ],
  },
  dailyResearch: {
    state: "FAILED",
    runId: "failed-today",
    completedAt: "2026-10-17T00:02:00Z",
    reportRunId: "ready-yesterday",
    reportCompletedAt: "2026-10-16T00:34:00Z",
    reportSlotDate: "2026-10-16",
    asOf: "2026-10-16T00:34:00Z",
    nextRunAt: "2026-10-17T00:30:00Z",
    report: null,
  },
  researchBoard: {
    methodVersion: "research-board-v2",
    asOf: null,
    items: [],
    summaries: [],
    direction: {
      label: "宽基",
      supports: [],
      counterFactors: [],
      recommendedChecks: [],
      itemIds: [],
      researchState: "WAIT_CONDITIONS",
      qualificationState: "INCOMPLETE",
    },
    dataStatus: {
      itemIds: [],
      groups: [],
      sourceResults: {},
      methodVersions: [],
      collectedAt: null,
      nextCheckAt: null,
    },
  },
} as unknown as OpportunityContext;
function URLState() {
  return <output aria-label="当前URL">{useLocation().search}</output>;
}
function show(path = "/fund-screener") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MacroContextStrip data={data} error={false} scopeKey="actor:hs300" />
      <URLState />
    </MemoryRouter>,
  );
}
async function openTab(name: string) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /今日投资观察/ }));
  await user.click(screen.getByRole("tab", { name }));
  return user;
}
it("shows four business groups, caps each independently and identifies the effective READY report rather than the failed run", () => {
  show();
  expect(screen.getByText(data.researchOverview!.summary!)).toBeInTheDocument();
  expect(
    within(screen.getByRole("region", { name: "重要风险" })).getAllByRole(
      "listitem",
    ),
  ).toHaveLength(2);
  expect(screen.queryByText("具体事项global-3")).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "研究机会" })).toHaveTextContent(
    "具体事项index-1",
  );
  expect(screen.getByRole("region", { name: "我的持仓" })).toHaveTextContent(
    "不能认定安全",
  );
  expect(screen.getByRole("link", { name: "核对我的资产" })).toHaveAttribute(
    "href",
    "/dashboard?scope=MINE",
  );
  expect(
    screen.getByText(/最近有效研究：2026年10月16日 08:34/),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/概览生成时间|2026年10月17日 08:02/),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("maps an old quality deep link into evidence with an internal data panel and preserves the selected identity", () => {
  show(
    "/fund-screener?researchOpen=1&researchTab=quality&fund=510330&instrument=abc&fundMarket=SSE&macroMonths=60",
  );
  expect(screen.getByRole("tab", { name: "宏观与市场证据" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    screen.queryByRole("tab", { name: "数据质量" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "数据状态" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  expect(screen.getAllByRole("row")).toHaveLength(6);
  expect(screen.getByLabelText("当前URL")).toHaveTextContent("fundMarket=SSE");
});
it("starts with four to six compact evidence metrics, opens only a requested chart and formats raw macro precision", async () => {
  show();
  const user = await openTab("宏观与市场证据");
  const core = screen.getByRole("region", { name: "核心指标" });
  expect(within(core).getAllByRole("article").length).toBeGreaterThanOrEqual(4);
  expect(within(core).getAllByRole("article").length).toBeLessThanOrEqual(6);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(within(core).getAllByText("0.9 %")[0]).toBeInTheDocument();
  expect(core.textContent).not.toContain("0000000000");
  await user.click(
    screen.getByRole("button", { name: "查看居民消费价格同比完整证据" }),
  );
  expect(screen.getAllByRole("img")).toHaveLength(1);
});
it("separates passed, failed and unverified strategy gates without promoting factual observations to execution qualification", () => {
  render(
    <RadarDetails
      data={{
        ...data,
        decision: {
          state: "WAIT",
          marketState: "WAIT",
          personalState: "UNKNOWN",
          summary: "执行资格不完整",
          gates: [
            {
              id: "trend",
              label: "客观趋势",
              state: "PASS",
              reason: "当前趋势条件成立",
            },
            {
              id: "value",
              label: "估值条件",
              state: "BLOCKED",
              reason: "估值条件未满足",
            },
            {
              id: "product",
              label: "产品质量",
              state: "UNKNOWN",
              reason: "缺产品证据",
            },
          ],
          validation: {
            state: "UNAVAILABLE",
            summary: "无真实PIT证明",
            methodVersion: "pit-v1",
          },
          nextChecks: [],
        },
      }}
      section="strategy"
    />,
  );
  expect(
    screen.getByRole("region", { name: "已满足的条件" }),
  ).toHaveTextContent("客观趋势");
  expect(
    screen.getByRole("region", { name: "确实未满足的条件" }),
  ).toHaveTextContent("估值条件");
  expect(
    screen.getByRole("region", { name: "无法核验的条件" }),
  ).toHaveTextContent("产品质量");
  expect(screen.getByText("无真实PIT证明")).toBeInTheDocument();
});

it("offers event tracking only for the displayed frozen report identity, never the failed latest run", async () => {
  const onTrack = vi.fn();
  const report = {
    asOf: "2026-10-16T00:34:00Z",
    summary: "昨日有效研究",
    events: [
      {
        id: "event-1",
        title: "能源事件",
        summary: "条件性研究",
        phase: "REPORTED",
        sourceIds: [],
        channels: [],
        scenarios: [],
        watchFor: [],
        unknowns: [],
        affectedDirectionIds: [],
      },
    ],
    upcoming: [],
    sources: [],
    coverage: [],
    energy: [],
  };
  const view = render(
    <DailyResearchReport
      data={{ ...data.dailyResearch!, report } as never}
      onTrack={onTrack}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "跟踪能源事件" }));
  expect(onTrack).toHaveBeenCalledWith("ready-yesterday", "event-1");
  view.rerender(
    <DailyResearchReport
      data={{ ...data.dailyResearch!, report, reportRunId: null } as never}
      onTrack={onTrack}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "跟踪能源事件" }),
  ).not.toBeInTheDocument();
});

it("prioritizes actual and nominal US rates for gold and presents PMI with one decimal", async () => {
  const gold = {
    id: "gold:gold",
    marketId: "gold",
    marketLabel: "黄金",
    styleId: "gold",
    label: "黄金",
    framework: "核对利率",
  };
  render(
    <MemoryRouter
      initialEntries={["/fund-screener?researchOpen=1&researchTab=macro"]}
    >
      <MacroContextStrip
        data={{
          ...data,
          items: data.items.map((row) =>
            row.code === "PMI" ? { ...row, value: 50.16 } : row,
          ),
        }}
        selectedDirection={gold}
        error={false}
      />
    </MemoryRouter>,
  );
  const metrics = within(
    screen.getByRole("region", { name: "核心指标" }),
  ).getAllByRole("article");
  expect(metrics[0]).toHaveAccessibleName("US_T10_REAL");
  expect(metrics[1]).toHaveAccessibleName("US_T10_NOMINAL");
  expect(screen.getByRole("article", { name: "PMI" })).toHaveTextContent(
    "50.2 指数点",
  );
});

it("does not conceal an old evidence period behind verified values", () => {
  render(
    <MemoryRouter
      initialEntries={["/fund-screener?researchOpen=1&researchTab=macro"]}
    >
      <MacroContextStrip
        data={{
          ...data,
          items: [
            { ...data.items[0], state: "STALE", valueVerification: "VERIFIED" },
          ],
        }}
        error={false}
      />
    </MemoryRouter>,
  );
  expect(screen.getByRole("article", { name: "PMI" })).toHaveTextContent(
    "旧期证据",
  );
});

it("keeps collapsed business observations concise, translates event phases and omits repeated noncritical impacts", () => {
  const groups = data.researchOverview!.groups!.map((group) => ({
    ...group,
    summary: group.items.length ? "有2项有限观察需核查" : group.summary,
    items: group.items.map((row) => ({ ...row, phase: "REPORTED" })),
  }));
  render(
    <MemoryRouter>
      <MacroContextStrip
        data={{
          ...data,
          researchOverview: { ...data.researchOverview!, groups },
        }}
        error={false}
      />
    </MemoryRouter>,
  );
  expect(screen.queryByText("并未形成买卖资格。")).not.toBeInTheDocument();
  expect(screen.queryByText("有2项有限观察需核查")).not.toBeInTheDocument();
  expect(screen.getAllByText(/报道线索/).length).toBeGreaterThan(0);
  expect(screen.queryByText(/REPORTED/)).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "我的持仓" })).toHaveTextContent(
    "不能认定安全",
  );
});

it("retains complete confirmed personal-risk text while compacting ordinary overview items", async () => {
  const risk = {
    id: "budget",
    key: "budget",
    category: "RISK",
    severity: "HIGH",
    confidence: "VERIFIED",
    factVerification: "VERIFIED",
    tone: "RED",
    holdingRelevance: "HELD",
    summary: "持仓预算风险",
    impact: "已确认真实风险影响必须完整展示",
    affectedAssets: {},
    evidenceIds: [],
    nextAction: action,
  };
  const highlight = {
    ...item("budget"),
    title: risk.summary,
    summary: "已核实风险完整说明",
    tone: "RED" as const,
    impact: risk.impact,
    scope: "HOLDINGS" as const,
  };
  render(
    <MemoryRouter>
      <MacroContextStrip
        data={
          {
            ...data,
            decision: { state: "POSITION_REVIEW" },
            portfolio: {
              state: "READY",
              heldSelected: true,
              budgetViolation: true,
              budgetState: "BLOCKED",
            },
            researchBoard: { ...data.researchBoard!, items: [risk] },
            researchOverview: {
              ...data.researchOverview!,
              groups: [
                {
                  id: "RISKS",
                  label: "重要风险",
                  state: "READY",
                  summary: "真实风险",
                  nextAction: null,
                  items: [highlight],
                },
              ],
            },
          } as never
        }
        error={false}
      />
    </MemoryRouter>,
  );
  expect(screen.getByText(risk.impact)).toHaveClass("op-critical-impact");
  expect(screen.getByText(highlight.summary)).not.toHaveClass(
    "op-observation-summary",
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /今日投资观察/ }));
  await user.click(screen.getByRole("tab", { name: "跟踪与复盘" }));
  expect(
    screen.queryByRole("region", { name: "重要风险" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(risk.impact)).toHaveClass("op-critical-impact");
});

it("shows expanded categories immediately without collapsed groups or compatibility highlights", () => {
  const path =
    "/fund-screener?researchOpen=1&researchTab=tracking&studyTrack=track-1";
  function content(value: OpportunityContext) {
    return (
      <MemoryRouter initialEntries={[path]}>
        <MacroContextStrip data={value} error={false} />
      </MemoryRouter>
    );
  }
  const view = render(content(data));
  expect(screen.getByRole("tab", { name: "跟踪与复盘" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    screen.queryByRole("region", { name: "重要风险" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("具体事项global-1")).not.toBeInTheDocument();
  expect(screen.getByText(data.researchOverview!.summary!)).toBeInTheDocument();
  expect(screen.getByText(/最近有效研究/)).toBeInTheDocument();
  view.rerender(
    content({
      ...data,
      researchOverview: {
        ...data.researchOverview!,
        groups: undefined,
        highlights: [item("legacy")],
      },
    }),
  );
  expect(
    screen.queryByRole("list", { name: "今日投资重点" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("具体事项legacy")).not.toBeInTheDocument();
  view.rerender(content({ ...data, researchOverview: undefined }));
  expect(
    screen.queryByText(/当前暂无足够证据列出投资重点/),
  ).not.toBeInTheDocument();
});
