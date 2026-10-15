import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { OpportunityContext } from "../../api/investment";
import MacroContextStrip from "./components/MacroContextStrip";

const action = (
  type = "VIEW_EVIDENCE",
  target: string | null = "research-macro",
) => ({
  type,
  target,
  label: "查看研究依据",
  href: null,
  enabled: true,
  disabledReason: null,
});
const gap = {
  id: "gap-m1",
  key: "M1_YOY",
  category: "DATA_GAP",
  severity: "LOW",
  confidence: "UNKNOWN",
  tone: "GRAY",
  summary: "M1同比尚无可用证据",
  impact: "无法完整评价信用条件。",
  evidenceIds: ["M1_YOY"],
  selectionRelevance: "CURRENT_SELECTION",
  holdingRelevance: "UNKNOWN",
  affectedAssets: { directionId: "a-share:broad", indexId: "hs300" },
  owner: "MACRO_WORKER",
  actionability: "SYSTEM",
  nextAction: action("VIEW_DATA", "data"),
  firstSeenAt: null,
  lastChangedAt: null,
  status: "OPEN",
  blocker: {
    code: "M1_YOY",
    state: "NOT_COLLECTED",
    owner: "MACRO_WORKER",
    reason: "尚未取得同口径数据",
    affectedJudgment: "信用条件",
    userFixable: false,
    lastAttemptAt: null,
    nextCheckAt: null,
    sourceKey: "PBOC",
  },
};
const opportunity = {
  ...gap,
  id: "trend",
  key: "INDEX_TREND",
  category: "OPPORTUNITY",
  severity: "MEDIUM",
  confidence: "VERIFIED",
  tone: "GREEN",
  summary: "当前指数符合趋势研究条件",
  impact: "仅为研究线索，估值与策略验证仍须独立核查。",
  evidenceIds: ["INDEX_TREND"],
  owner: "RESEARCH",
  actionability: "USER",
  nextAction: action("VIEW_CANDIDATES", "op-research-targets"),
  blocker: undefined,
};
const risk = {
  ...opportunity,
  id: "budget",
  key: "PORTFOLIO_BUDGET",
  category: "RISK",
  severity: "HIGH",
  tone: "RED",
  holdingRelevance: "HELD",
  summary: "已确认持仓超过风险限额",
  impact: "复核当前组合集中度。",
  nextAction: {
    ...action("VIEW_HOLDINGS", "holdings"),
    label: "查看我的持仓",
    href: "/dashboard?scope=MINE",
  },
};
const board = {
  methodVersion: "research-board-v1",
  asOf: "2026-10-15T01:00:00Z",
  items: [opportunity, gap],
  summaries: [
    {
      id: "HOLDINGS",
      label: "我的持仓风险",
      state: "UNKNOWN",
      summary: "持仓风险尚未评估",
      tone: "GRAY",
      itemIds: [],
      nextAction: {
        ...action("VIEW_HOLDINGS", "holdings"),
        label: "查看我的持仓",
        href: "/dashboard?scope=MINE",
      },
    },
    {
      id: "OPPORTUNITIES",
      label: "机会候选",
      state: "PRIORITY_RESEARCH",
      summary: "当前研究范围内有1条有限线索",
      tone: "GREEN",
      itemIds: ["trend"],
      nextAction: {
        ...action("VIEW_CANDIDATES", "op-research-targets"),
        label: "查看指数和基金",
      },
    },
    {
      id: "CHANGES",
      label: "重要新变化",
      state: "UNKNOWN",
      summary: "暂无可确认的重要新变化",
      tone: "GRAY",
      itemIds: [],
      nextAction: { ...action(), label: "查看变化依据" },
    },
  ],
  direction: {
    label: "A股 · 宽基",
    researchState: "PRIORITY_RESEARCH",
    qualificationState: "INCOMPLETE",
    supports: [
      {
        label: "趋势",
        summary: "趋势事实可独立研究",
        evidenceIds: ["INDEX_TREND"],
        state: "READY",
      },
    ],
    counterFactors: [],
    recommendedChecks: ["继续核对盈利结构"],
    itemIds: ["trend", "gap-m1"],
  },
  dataStatus: {
    itemIds: ["gap-m1"],
    collectedAt: "2026-10-15T00:00:00Z",
    nextCheckAt: null,
    sourceResults: {
      PBOC: { status: "FAILED", errorClass: "HTTPStatusError" },
    },
    methodVersions: ["research-board-v1", "opportunity-radar-v2"],
  },
};
const data = {
  state: "UNVERIFIED",
  researchBoard: board,
  items: [
    {
      code: "PMI",
      label: "制造业 PMI",
      value: 50.1,
      state: "UNVERIFIED",
      unit: "指数点",
      asOf: "2026-09-01",
      publishedAt: null,
      source: "国家统计局",
      history: [
        {
          asOf: "2026-08-01",
          value: 49.8,
          unit: "指数点",
          source: "国家统计局",
        },
        {
          asOf: "2026-09-01",
          value: 50.1,
          unit: "指数点",
          source: "国家统计局",
        },
      ],
    },
  ],
  radar: {
    methodVersion: "legacy",
    asOf: null,
    headline: {
      id: "legacy",
      priority: "CRITICAL",
      kind: "GAP",
      summary: "旧版重复四问摘要",
      impact: "旧版缺口不应标红",
      evidenceIds: [],
      relatedDirectionIds: [],
      timeScale: "月度",
    },
    questions: [],
    changes: [],
    sections: [],
  },
  decision: {
    state: "WAIT",
    marketState: "WAIT",
    personalState: "UNKNOWN",
    summary: "等待完整建仓证据",
    gates: [],
    nextChecks: [],
    validation: {
      state: "UNAVAILABLE",
      summary: "真实时点策略尚未验证",
      methodVersion: "pit-v1",
    },
  },
  portfolio: {
    state: "UNKNOWN",
    heldSelected: null,
    selectedWeight: null,
    sameIndexWeight: null,
    cashRatio: null,
    asOf: null,
    summary: "持仓未知",
    missingEvidence: [],
  },
  events: [],
  notices: [],
} as unknown as OpportunityContext;

function show(context = data, operations = false, callbacks = {}) {
  return render(
    <MemoryRouter>
      <MacroContextStrip
        data={context}
        error={false}
        canViewOperations={operations}
        {...callbacks}
      />
    </MemoryRouter>,
  );
}
async function open(label: string) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: label }));
  return user;
}

it("replaces repeated four questions with three compact research summaries", () => {
  show();
  expect(
    screen.getByRole("heading", { name: "市场环境与研究重点" }),
  ).toBeInTheDocument();
  for (const label of ["我的持仓风险", "机会候选", "重要新变化"])
    expect(screen.getByRole("region", { name: label })).toBeInTheDocument();
  expect(screen.getByText("持仓风险尚未评估")).toBeInTheDocument();
  expect(screen.queryByText("旧版重复四问摘要")).not.toBeInTheDocument();
  expect(screen.queryByText(opportunity.summary)).not.toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.queryByText(gap.summary)).not.toBeInTheDocument();
  expect(document.querySelector(".op-critical-impact")).not.toBeInTheDocument();
});

it("mounts only the requested independent evidence topic", async () => {
  show();
  await open("查看研究依据");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await open("宏观与流动性");
  expect(
    screen.getByRole("img", { name: "制造业 PMI 历史走势" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("真实时点策略尚未验证")).not.toBeInTheDocument();
  await open("策略与持仓");
  expect(screen.getByText("真实时点策略尚未验证")).toBeInTheDocument();
  await open("宏观与流动性");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.getByText("真实时点策略尚未验证")).toBeInTheDocument();
});

it("keeps one issue body and moves evidence gaps and source failures to data status", async () => {
  show();
  await open("查看机会候选事项");
  expect(screen.getAllByText(opportunity.summary)).toHaveLength(1);
  expect(screen.queryByText(gap.summary)).not.toBeInTheDocument();
  await open("数据与运行状态");
  expect(screen.getAllByText(gap.summary)).toHaveLength(1);
  expect(screen.getAllByText(opportunity.summary)).toHaveLength(1);
  expect(screen.getByText("尚未取得同口径数据")).toBeInTheDocument();
  expect(screen.getByText(/HTTPStatusError/)).toBeInTheDocument();
  expect(screen.getByText("下次处理时间：待确认")).toBeInTheDocument();
  expect(screen.queryByText(/正在采集|已经排队/)).not.toBeInTheDocument();
});

it("requires confirmed held budget evidence for red and provides real holding links", async () => {
  show({
    ...data,
    researchBoard: {
      ...board,
      items: [risk],
      summaries: [
        {
          ...board.summaries[0],
          state: "POSITION_REVIEW",
          summary: "1项已确认预算风险",
          tone: "RED",
          itemIds: [risk.id],
        },
      ],
    },
    decision: { ...data.decision!, state: "POSITION_REVIEW" },
    portfolio: {
      ...data.portfolio!,
      state: "READY",
      heldSelected: true,
      budgetViolation: true,
      budgetState: "BLOCKED",
    },
  } as OpportunityContext);
  expect(screen.getByText("1项已确认预算风险")).toHaveClass(
    "op-critical-impact",
  );
  await open("查看我的持仓风险事项");
  expect(screen.getByText(risk.impact)).toHaveClass("op-critical-impact");
  expect(
    screen.getAllByRole("link", { name: "查看我的持仓" })[0],
  ).toHaveAttribute("href", "/dashboard?scope=MINE");
});

it.each(["gap", "unverified", "holding-unknown"])(
  "does not promote %s into a red risk",
  async (invalid) => {
    const invalidIssue =
      invalid === "gap"
        ? { ...risk, category: "DATA_GAP" }
        : invalid === "unverified"
          ? { ...risk, confidence: "UNKNOWN" }
          : risk;
    show({
      ...data,
      researchBoard: {
        ...board,
        items: [invalidIssue],
        summaries: [{ ...board.summaries[0], tone: "RED", itemIds: [risk.id] }],
        dataStatus: {
          ...board.dataStatus,
          itemIds: invalid === "gap" ? [risk.id] : [],
        },
      },
      decision: { ...data.decision!, state: "POSITION_REVIEW" },
      portfolio: {
        ...data.portfolio!,
        state: "READY",
        heldSelected: invalid === "holding-unknown" ? null : true,
        budgetViolation: true,
        budgetState: "BLOCKED",
      },
    } as OpportunityContext);
    await open(invalid === "gap" ? "数据与运行状态" : "查看我的持仓风险事项");
    expect(
      document.querySelector(".op-critical-impact"),
    ).not.toBeInTheDocument();
  },
);

it("guards operations actions by role and keeps unsupported source actions explicit", async () => {
  const ops = {
    ...gap,
    nextAction: {
      ...action("VIEW_OPERATIONS", "operations"),
      label: "查看运行情况",
      href: "/operations",
    },
  };
  const context = {
    ...data,
    researchBoard: { ...board, items: [ops] },
  } as OpportunityContext;
  const view = show(context);
  await open("数据与运行状态");
  expect(
    screen.queryByRole("link", { name: "查看运行情况" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/仅管理员可查看运行情况/)).toBeInTheDocument();
  view.unmount();
  show(context, true);
  await open("数据与运行状态");
  expect(screen.getByRole("link", { name: "查看运行情况" })).toHaveAttribute(
    "href",
    "/operations",
  );
  expect(
    screen.queryByRole("link", { name: /授权配置/ }),
  ).not.toBeInTheDocument();
});

it("opens local data actions without fake links and preserves explicit tracking and acknowledgement", async () => {
  const monitor = vi.fn(),
    ack = vi.fn();
  show(
    {
      ...data,
      notices: [
        {
          id: "notice",
          title: "条件变化",
          summary: "研究条件有变",
          severity: "INFO",
          createdAt: "2026-10-15T01:00:00Z",
          readAt: null,
        },
      ],
    },
    false,
    { onMonitor: monitor, onNoticeAck: ack },
  );
  expect(monitor).not.toHaveBeenCalled();
  expect(ack).not.toHaveBeenCalled();
  await open("查看研究事项与缺口");
  expect(screen.getByText(gap.summary)).toBeInTheDocument();
  await open("研究条件跟踪与通知");
  const user = await open("跟踪研究条件");
  expect(monitor).toHaveBeenCalledWith(true);
  await user.click(screen.getByRole("button", { name: "标记条件变化为已读" }));
  expect(ack).toHaveBeenCalledWith("notice");
  expect(
    within(screen.getByRole("region", { name: "当前研究方向" })).getByText(
      /研究资格：证据不完整/,
    ),
  ).toBeInTheDocument();
});

it("does not let a legacy critical gap create red styling", () => {
  show({ ...data, researchBoard: undefined } as OpportunityContext);
  expect(screen.getByText("旧版缺口不应标红")).not.toHaveClass(
    "op-critical-impact",
  );
});

it("does not disable an existing monitor through an action labelled as starting tracking", async () => {
  const monitor = vi.fn();
  show(
    {
      ...data,
      researchBoard: {
        ...board,
        summaries: [
          {
            ...board.summaries[2],
            nextAction: { ...action("TRACK", null), label: "跟踪当前方向条件" },
          },
        ],
      },
    } as OpportunityContext,
    false,
    { onMonitor: monitor, monitorEnabled: true },
  );
  expect(
    screen.getByRole("button", { name: "跟踪当前方向条件" }),
  ).toBeDisabled();
  await userEvent.click(
    screen.getByRole("button", { name: "跟踪当前方向条件" }),
  );
  expect(monitor).not.toHaveBeenCalled();
});

it("opens the local topic supplied by the action without mounting unrelated charts", async () => {
  show({
    ...data,
    researchBoard: {
      ...board,
      summaries: [
        {
          ...board.summaries[2],
          nextAction: {
            ...action("VIEW_EVIDENCE", "research-strategy"),
            label: "查看策略验证",
          },
        },
      ],
    },
  } as OpportunityContext);
  await open("查看策略验证");
  expect(screen.getByText("真实时点策略尚未验证")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "宏观与流动性" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
});

it("keeps a confirmed risk visible when its condition notice is read", async () => {
  const context = {
    ...data,
    researchBoard: {
      ...board,
      items: [risk],
      summaries: [
        {
          ...board.summaries[0],
          state: "POSITION_REVIEW",
          summary: "1项已确认预算风险",
          tone: "RED",
          itemIds: [risk.id],
        },
      ],
    },
    decision: { ...data.decision!, state: "POSITION_REVIEW" },
    portfolio: {
      ...data.portfolio!,
      state: "READY",
      heldSelected: true,
      budgetViolation: true,
      budgetState: "BLOCKED",
    },
    notices: [
      {
        id: "read-risk",
        title: "风险条件变化",
        summary: "预算条件有变",
        severity: "HIGH",
        createdAt: "2026-10-15T01:00:00Z",
        readAt: "2026-10-15T02:00:00Z",
      },
    ],
  } as OpportunityContext;
  show(context);
  await open("查看我的持仓风险事项");
  await open("研究条件跟踪与通知");
  expect(screen.getByText(risk.summary)).toBeInTheDocument();
  expect(screen.getByText("已读")).toBeInTheDocument();
  expect(screen.getByText(risk.impact)).toHaveClass("op-critical-impact");
});

it("shows supported personal actions in strategy details", async () => {
  show({
    ...data,
    researchBoard: {
      ...board,
      items: [{ ...gap, impact: gap.blocker.reason }],
    },
    decision: {
      ...data.decision!,
      actions: [
        {
          ...action("VIEW_POLICY", null),
          label: "查看风险政策",
          href: "/risk-policy",
        },
        {
          ...action("UPDATE_HOLDINGS", null),
          label: "补充持仓数据",
          href: "/onboarding",
        },
      ],
    },
  } as OpportunityContext);
  await open("查看研究依据");
  await open("策略与持仓");
  expect(screen.getByRole("link", { name: "查看风险政策" })).toHaveAttribute(
    "href",
    "/risk-policy",
  );
  expect(screen.getByRole("link", { name: "补充持仓数据" })).toHaveAttribute(
    "href",
    "/onboarding",
  );
});

it("does not repeat a blocker reason already used as its impact", async () => {
  show({
    ...data,
    researchBoard: {
      ...board,
      items: [{ ...gap, impact: gap.blocker.reason }],
    },
  } as OpportunityContext);
  await open("数据与运行状态");
  expect(screen.getAllByText(gap.blocker.reason)).toHaveLength(1);
});

it("puts domestic credit ahead of external yields for A-share research and retains both histories", async () => {
  const base = {
    frequency: "DAILY",
    unit: "%",
    value: 2,
    asOf: "2026-10-14",
    state: "READY",
    sourceUri: null,
    sourceName: "官方来源",
    publishedAt: null,
    retrievedAt: null,
    availableAt: null,
    timeBasis: "RETRIEVAL_ONLY",
    methodVersion: "test-v1",
    changes: {
      previousValue: null,
      change5Samples: null,
      change20Samples: null,
      changeUnit: null,
      median: null,
      percentile: null,
      sampleCount: 0,
    },
    history: [],
  };
  show(
    {
      ...data,
      series: [
        { ...base, code: "US_T10_REAL", label: "美国实际利率" },
        {
          ...base,
          frequency: "YTD",
          code: "CORP_MEDLONG_LOANS_YTD",
          label: "企业中长期贷款累计新增",
          unit: "万亿元",
        },
      ],
    } as OpportunityContext,
    false,
    {
      selectedDirection: {
        id: "a-share:broad",
        marketId: "a-share",
        marketLabel: "A股",
        label: "宽基",
        styleId: "broad",
        framework: "盈利与风险",
      },
    },
  );
  await open("查看研究依据");
  await open("宏观与流动性");
  const core = screen.getByRole("region", { name: "关键变量证据" });
  expect(
    within(core).getByRole("heading", { name: "企业中长期贷款累计新增" }),
  ).toBeInTheDocument();
  expect(
    within(core).queryByRole("heading", { name: "美国实际利率" }),
  ).not.toBeInTheDocument();
  await open("全球市场与事件");
  expect(
    screen.getByRole("heading", { name: "美国实际利率" }),
  ).toBeInTheDocument();
});

it("separates known holdings from an unknown personal risk assessment", async () => {
  show({
    ...data,
    decision: {
      ...data.decision!,
      assessment: {
        holdingState: "HELD",
        riskState: "UNKNOWN",
        marketResearchState: "PRIORITY_RESEARCH",
      },
    },
    portfolio: { ...data.portfolio!, heldSelected: true },
  });
  await open("查看研究依据");
  await open("策略与持仓");
  expect(
    screen.getByText(/持仓识别：已持有 · 风险评估：尚未评估/),
  ).toBeInTheDocument();
  expect(screen.queryByText(/风险已排除|没有风险/)).not.toBeInTheDocument();
});

it("identifies the selected market when the backend direction label is only a style", () => {
  show(
    {
      ...data,
      researchBoard: {
        ...board,
        direction: { ...board.direction, label: "宽基" },
      },
    } as OpportunityContext,
    false,
    {
      selectedDirection: {
        id: "a-share:broad",
        marketId: "a-share",
        marketLabel: "A股",
        label: "宽基",
        styleId: "broad",
        framework: "盈利与风险",
      },
    },
  );
  expect(
    within(screen.getByRole("region", { name: "当前研究方向" })).getByRole(
      "heading",
      { name: "A股 · 宽基" },
    ),
  ).toBeInTheDocument();
});
