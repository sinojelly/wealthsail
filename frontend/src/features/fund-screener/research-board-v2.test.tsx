import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { OpportunityContext, ResearchAction } from "../../api/investment";
import MacroContextStrip from "./components/MacroContextStrip";

const action = (
  type: ResearchAction["type"],
  label: string,
  target: string | null = null,
  href: string | null = null,
) => ({ type, label, target, href, enabled: true, disabledReason: null });
const issue = {
  id: "pmi-change",
  key: "PMI_CROSS",
  category: "CHANGE",
  severity: "MEDIUM",
  confidence: "PARTIAL",
  factVerification: "VERIFIED",
  tone: "BLUE",
  summary: "PMI当前跨越扩张分界",
  impact: "仅为制造业观察，仍须核对企业盈利。",
  evidenceIds: ["PMI"],
  selectionRelevance: "CURRENT_SELECTION",
  holdingRelevance: "UNKNOWN",
  affectedAssets: { directionId: "a-share:broad", indexId: "hs300" },
  owner: "RESEARCH_SERVICE",
  actionability: "READ_ONLY",
  nextAction: action("VIEW_EVIDENCE", "查看PMI证据", "research-macro"),
  firstSeenAt: null,
  lastChangedAt: null,
  status: "OPEN",
};
const m1 = {
  ...issue,
  id: "m1-gap",
  key: "M1_YOY",
  category: "SYSTEM",
  confidence: "UNKNOWN",
  factVerification: "UNKNOWN",
  tone: "GRAY",
  summary: "M1数据获取失败",
  impact: "信用研究受限",
  evidenceIds: ["M1_YOY"],
  owner: "MACRO_WORKER",
  nextAction: action("VIEW_DATA", "查看研究限制", "research-data"),
  blocker: {
    code: "M1_YOY",
    state: "FAILED",
    owner: "MACRO_WORKER",
    reason: "该指标最近采集失败",
    affectedJudgment: "信用条件",
    userFixable: false,
    lastAttemptAt: "2026-10-15T01:00:00Z",
    nextCheckAt: "2026-10-15T02:00:00Z",
    sourceKey: "PBOC_M1",
  },
};
const tsf = {
  ...m1,
  id: "tsf-gap",
  key: "TSF_STOCK_YOY",
  summary: "社融数据获取失败",
  evidenceIds: ["TSF_STOCK_YOY"],
  blocker: {
    ...m1.blocker,
    code: "TSF_STOCK_YOY",
    sourceKey: "PBOC_TSF",
    lastAttemptAt: null,
    nextCheckAt: null,
  },
};
const groups = [
  {
    id: "MARKET",
    label: "指数与基金数据",
    summary: "暂无独立列出的受限项目",
    impact: "不代表完整资格已认证",
    owner: "RESEARCH_DATA",
    itemIds: [],
  },
  {
    id: "MACRO",
    label: "宏观数据",
    summary: "两项指标各自获取失败",
    impact: "国内信用条件尚待核查",
    owner: "MACRO_WORKER",
    itemIds: [m1.id, tsf.id],
  },
  {
    id: "PERMISSION",
    label: "来源许可",
    summary: "暂无独立列出的受限项目",
    impact: "依据实际许可状态核查",
    owner: "ADMIN",
    itemIds: [],
  },
  {
    id: "PORTFOLIO",
    label: "个人持仓",
    summary: "风险尚待评估",
    impact: "不能认定持仓安全",
    owner: "PORTFOLIO",
    itemIds: [],
  },
  {
    id: "STRATEGY",
    label: "策略验证",
    summary: "未列出已认证策略",
    impact: "不能认证建仓条件",
    owner: "RESEARCH_DATA",
    itemIds: [],
  },
];
const context = {
  state: "UNVERIFIED",
  months: 36,
  items: [
    {
      code: "PMI",
      label: "制造业 PMI",
      value: 50.1,
      state: "UNVERIFIED",
      valueVerification: "VERIFIED",
      unit: "指数点",
      source: "国家统计局",
      sourceUri: "https://www.stats.gov.cn/pmi",
      asOf: "2026-09-01",
      publishedAt: null,
      history: [
        {
          asOf: "2026-08-01",
          value: 49.8,
          source: "国家统计局",
          unit: "指数点",
        },
        {
          asOf: "2026-09-01",
          value: 50.1,
          source: "国家统计局",
          unit: "指数点",
        },
      ],
    },
  ],
  marketEvidence: [
    {
      indexId: "hs300",
      label: "沪深300",
      state: "PARTIAL",
      asOf: "2026-10-14",
      valuation: { state: "UNKNOWN" },
      trend: {
        state: "READY",
        ma200: 3900,
        momentum63: 0.02,
        distanceMA200: 0.01,
      },
      risk: { state: "READY", volatility20: 0.12, maxDrawdown: -0.2 },
      earnings: { state: "UNKNOWN" },
      sourceUri: null,
      missingEvidence: [],
    },
  ],
  decision: {
    state: "WAIT",
    marketState: "UNKNOWN",
    personalState: "UNKNOWN",
    summary: "仍待完整研究资格",
    gates: [],
    nextChecks: [],
    validation: {
      state: "UNAVAILABLE",
      summary: "尚无真实时点验证",
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
    summary: "个人持仓未知",
    missingEvidence: [],
  },
  events: [],
  notices: [],
  researchBoard: {
    methodVersion: "research-board-v2",
    asOf: "2026-10-15T01:00:00Z",
    items: [issue, m1, tsf],
    summaries: [
      {
        id: "HOLDINGS",
        label: "持仓风险",
        state: "UNKNOWN",
        tone: "GRAY",
        summary: "个人风险尚未完成评估",
        itemIds: [],
        nextAction: action(
          "VIEW_HOLDINGS",
          "查看我的持仓",
          null,
          "/dashboard?scope=MINE",
        ),
      },
      {
        id: "OPPORTUNITIES",
        label: "研究机会",
        state: "INSUFFICIENT",
        tone: "GRAY",
        summary: "完整机会资格暂不能判断",
        itemIds: [],
        nextAction: action(
          "VIEW_CANDIDATES",
          "查看指数与基金",
          "op-research-targets",
        ),
      },
      {
        id: "CHANGES",
        label: "重要变化",
        state: "OBSERVED",
        tone: "BLUE",
        summary: "一项已核查事实变化",
        itemIds: [issue.id],
        nextAction: action("VIEW_ISSUES", "查看重要变化", "CHANGES"),
      },
    ],
    direction: {
      label: "宽基",
      summary: "制造业事实可独立观察，当前研究资格不完整。",
      nextStep: "先完成当前指数的独立研究。",
      researchState: "WAIT_CONDITIONS",
      qualificationState: "INCOMPLETE",
      supports: [
        {
          label: "制造业观察",
          summary: "景气略高于分界，需核查盈利",
          evidenceIds: ["PMI"],
          state: "OBSERVED",
        },
      ],
      counterFactors: [
        {
          label: "估值反方",
          summary: "市场可能已经计价",
          evidenceIds: [],
          state: "UNKNOWN",
        },
      ],
      recommendedChecks: ["检查企业盈利结构"],
      failedConditions: [
        {
          id: "TREND",
          label: "趋势规则",
          reason: "当前尚未满足全部趋势条件",
          evidenceIds: ["hs300"],
        },
      ],
      relatedIndexIds: ["hs300"],
      itemIds: [issue.id],
      blockerIds: [m1.id, tsf.id],
    },
    dataStatus: {
      itemIds: [m1.id, tsf.id],
      groups,
      collectedAt: "2026-10-15T01:00:00Z",
      nextCheckAt: "2026-10-15T02:00:00Z",
      collectionState: "FINISHED",
      sourceResults: {
        PBOC_M1: {
          status: "FAILED",
          errorClass: "HTTPStatusError",
          httpStatus: 403,
        },
        PBOC_TSF: {
          status: "FAILED",
          errorClass: "HTTPStatusError",
          httpStatus: 404,
        },
      },
      methodVersions: ["research-board-v2"],
      adminAction: action(
        "VIEW_OPERATIONS",
        "管理员查看运行详情",
        null,
        "/operations",
      ),
    },
  },
} as unknown as OpportunityContext;
function show(data = context, callbacks = {}, scopeKey = "scope-a") {
  return render(
    <MemoryRouter>
      <MacroContextStrip
        data={data}
        error={false}
        scopeKey={scopeKey}
        {...callbacks}
      />
    </MemoryRouter>,
  );
}
async function click(name: string) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name }));
  return user;
}
async function tab(name: string) {
  const user = userEvent.setup();
  const toggle = screen.getByRole("button", { name: /今日投资观察/ });
  if (toggle.getAttribute("aria-expanded") !== "true") await user.click(toggle);
  await user.click(
    screen.getByRole("tab", {
      name: name === "数据质量" ? "市场证据" : name,
    }),
  );
  if (name === "数据质量")
    await user.click(screen.getByRole("button", { name: "数据状态" }));
  return screen.getByRole("tabpanel");
}
it("keeps legacy facts inside the expanded container and starts without cards or dialogs", async () => {
  show();
  expect(screen.getByRole("button", { name: /今日投资观察/ })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  expect(
    screen.queryByRole("list", { name: "今日投资重点" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(issue.summary)).not.toBeInTheDocument();
  expect(screen.queryByText(m1.summary)).not.toBeInTheDocument();
  const panel = await tab("今日重点");
  expect(
    within(panel).getByRole("heading", { name: issue.summary }),
  ).toBeInTheDocument();
  expect(within(panel).getByText("沪深300")).toBeInTheDocument();
  expect(
    within(panel).getByRole("link", { name: "查看制造业 PMI来源" }),
  ).toHaveAttribute("href", "https://www.stats.gov.cn/pmi");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
it("shows limited asset conditions without duplicating the direction workbench and selects its actual index", async () => {
  const select = vi.fn();
  const failed = context.researchBoard!.direction.failedConditions![0].reason;
  show(
    {
      ...context,
      marketEvidence: context.marketEvidence!.map((row) => ({
        ...row,
        trend: { ...row.trend, distanceMA200: -0.01, momentum63: -0.02 },
      })),
      researchOverview: {
        methodVersion: "research-overview-v2",
        asOf: "2026-10-15T01:00:00Z",
        summary: "沪深300预设趋势条件尚未通过，继续核查。",
        highlights: [
          {
            id: "trend:hs300",
            kind: "INDEX_TREND",
            title: "沪深300趋势条件",
            summary: failed,
            impact: "有限条件不构成完整资格。",
            tone: "ORANGE",
            targetTab: "direction",
            indexId: "hs300",
            directionIds: ["a-share:broad"],
            sourceIds: [],
            asOf: "2026-10-14",
            resultType: "INCONCLUSIVE",
            condition: "FAILED",
            counterConditions: ["市场可能已经计价"],
            nextValidation: ["检查企业盈利结构"],
          },
        ],
      },
    },
    { onIndexSelect: select },
  );
  const panel = await tab("今日重点");
  await userEvent.click(screen.getByText("沪深300趋势条件"));
  expect(within(panel).getByText("检查企业盈利结构")).toBeInTheDocument();
  expect(within(panel).getByText("市场可能已经计价")).toBeInTheDocument();
  expect(
    within(panel).getByText("当前尚未满足全部趋势条件"),
  ).toBeInTheDocument();
  expect(within(panel).getByText("-1.00%")).toBeVisible();
  expect(within(panel).getByText("-2.00%")).toBeVisible();
  expect(within(panel).queryByText(m1.summary)).not.toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  await click("研究沪深300 →");
  expect(select).toHaveBeenCalledWith("hs300");
  expect(screen.getByRole("button", { name: /今日投资观察/ })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  expect(
    screen.queryByRole("tab", { name: "投资方向与基金" }),
  ).not.toBeInTheDocument();
});
it("keeps five compact quality groups and mounts only selected children with actual times and HTTP status", async () => {
  show();
  const panel = await tab("数据质量");
  expect(within(panel).getAllByRole("row")).toHaveLength(6);
  expect(within(panel).queryByText(m1.summary)).not.toBeInTheDocument();
  await click("展开宏观数据");
  expect(within(panel).getByText(m1.summary)).toBeInTheDocument();
  expect(within(panel).getByText(tsf.summary)).toBeInTheDocument();
  expect(within(panel).getByText(/HTTP 403/)).toBeInTheDocument();
  expect(within(panel).getByText(/HTTP 404/)).toBeInTheDocument();
  expect(within(panel).getByText(/未检测到执行记录/)).toBeInTheDocument();
  expect(
    within(panel).queryByText(/自动修复|正在采集/),
  ).not.toBeInTheDocument();
});
it("keeps operations navigation separate and available only to administrators in quality", async () => {
  const view = show();
  await tab("数据质量");
  expect(
    screen.queryByRole("link", { name: "管理员查看运行详情" }),
  ).not.toBeInTheDocument();
  view.unmount();
  show(context, { canViewOperations: true });
  expect(
    screen.queryByRole("link", { name: "管理员查看运行详情" }),
  ).not.toBeInTheDocument();
  await tab("数据质量");
  expect(
    screen.getByRole("link", { name: "管理员查看运行详情" }),
  ).toHaveAttribute("href", "/operations");
  expect(screen.queryByText(m1.summary)).not.toBeInTheDocument();
});
it.each([
  ["FAILED", "采集失败"],
  ["RUNNING", "已登记执行租约"],
])(
  "identifies persisted index collection provenance for %s without claiming a live worker",
  async (status, label) => {
    show({
      ...context,
      researchBoard: {
        ...context.researchBoard!,
        items: [
          {
            ...m1,
            blocker: { ...m1.blocker, sourceKey: "INDEX_RESEARCH:hs300" },
          },
        ],
        dataStatus: {
          ...context.researchBoard!.dataStatus,
          sourceResults: {
            "INDEX_RESEARCH:hs300": {
              status,
              errorClass: "HTTPStatusError",
              httpStatus: 403,
            },
          },
        },
      },
    } as unknown as OpportunityContext);
    await tab("数据质量");
    await click("展开宏观数据");
    expect(
      screen.getByText(new RegExp("指数研究采集 · " + label)),
    ).toBeInTheDocument();
    expect(screen.getByText(/HTTP 403/)).toBeInTheDocument();
  },
);
it("mounts history only in macro, unmounts it on collapse and supports keyboard category navigation", async () => {
  show();
  await tab("今日重点");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await tab("市场证据");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await click("查看制造业 PMI完整证据");
  expect(
    screen.getByRole("img", { name: "制造业 PMI 历史走势" }),
  ).toBeInTheDocument();
  const user = userEvent.setup();
  screen.getByRole("tab", { name: "市场证据" }).focus();
  await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "我的跟踪与复盘" })).toHaveFocus();
  expect(screen.getByRole("tab", { name: "我的跟踪与复盘" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /今日投资观察/ }));
  expect(screen.queryByRole("tabpanel")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("opens an issue's actual evidence category directly without any intermediate dialog", async () => {
  show();
  await tab("今日重点");
  await click("查看PMI证据");
  expect(screen.getByRole("tab", { name: "市场证据" })).toHaveFocus();
  expect(screen.getByRole("tab", { name: "市场证据" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await click("查看制造业 PMI完整证据");
  expect(
    screen.getByRole("img", { name: "制造业 PMI 历史走势" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("discards selected quality children and old issue contents when the complete scope changes", async () => {
  const view = show();
  await tab("数据质量");
  await click("展开宏观数据");
  expect(screen.getByText(m1.summary)).toBeInTheDocument();
  view.rerender(
    <MemoryRouter>
      <MacroContextStrip
        data={{
          ...context,
          researchBoard: { ...context.researchBoard!, items: [] },
        }}
        error={false}
        scopeKey="scope-b"
      />
    </MemoryRouter>,
  );
  expect(screen.queryByText(m1.summary)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "宏观数据受限项目" }),
  ).not.toBeInTheDocument();
});
it("never exposes or activates tracking and acknowledgement from the observation strip", async () => {
  const track = vi.fn(),
    ack = vi.fn();
  show(
    {
      ...context,
      notices: [
        {
          id: "n",
          title: "研究条件变化",
          summary: "条件已更新",
          severity: "INFO",
          createdAt: "2026-10-15T01:00:00Z",
          readAt: null,
        },
      ],
    },
    { onMonitor: track, onNoticeAck: ack },
  );
  for (const name of ["今日重点", "市场证据", "我的跟踪与复盘", "数据质量"])
    await tab(name);
  expect(
    screen.queryByRole("button", { name: /跟踪|标记.*已读/ }),
  ).not.toBeInTheDocument();
  expect(track).not.toHaveBeenCalled();
  expect(ack).not.toHaveBeenCalled();
});
it.each(["VERIFIED", "UNKNOWN"] as const)(
  "requires verified facts plus confirmed budgets for %s risk emphasis",
  async (factVerification) => {
    const risk = {
      ...issue,
      id: "budget",
      category: "RISK",
      severity: "HIGH",
      confidence: "VERIFIED",
      factVerification,
      tone: "RED",
      holdingRelevance: "HELD",
      summary: "已确认持仓预算风险",
      impact: "复核已确认预算限额。",
      nextAction: action(
        "VIEW_HOLDINGS",
        "查看我的持仓",
        null,
        "/dashboard?scope=MINE",
      ),
    };
    show({
      ...context,
      researchBoard: {
        ...context.researchBoard!,
        items: [risk],
        summaries: [],
      },
      decision: { ...context.decision!, state: "POSITION_REVIEW" },
      portfolio: {
        ...context.portfolio!,
        state: "READY",
        heldSelected: true,
        budgetViolation: true,
        budgetState: "BLOCKED",
      },
    } as OpportunityContext);
    await tab("今日重点");
    if (factVerification === "VERIFIED")
      expect(screen.getByText(risk.impact)).toHaveClass("op-critical-impact");
    else
      expect(
        document.querySelector(".op-critical-impact"),
      ).not.toBeInTheDocument();
  },
);
it("shows the canonical matter once when the board and overview share its identity", async () => {
  const summary = context.researchBoard!.direction.supports[0].summary;
  show({
    ...context,
    researchBoard: {
      ...context.researchBoard!,
      direction: { ...context.researchBoard!.direction, summary },
    },
    researchOverview: {
      methodVersion: "research-overview-v2",
      asOf: "2026-10-15T01:00:00Z",
      highlights: [
        {
          id: issue.id,
          kind: "MACRO_CHANGE",
          title: issue.summary,
          summary,
          impact: issue.impact,
          tone: "BLUE",
          targetTab: "macro",
          indexId: null,
          directionIds: [],
          sourceIds: [],
          asOf: "2026-09",
          resultType: "LIMITED_CLUE",
          nextValidation: ["核查企业盈利"],
        },
      ],
    },
  });
  const panel = await tab("今日重点");
  expect(within(panel).getAllByText(summary)).toHaveLength(1);
  expect(within(panel).getAllByText(issue.summary)).toHaveLength(1);
});
it("disables unsupported destinations without creating fake links", async () => {
  show({
    ...context,
    researchBoard: {
      ...context.researchBoard!,
      items: [
        {
          ...issue,
          nextAction: action(
            "VIEW_HOLDINGS",
            "不支持的账户入口",
            null,
            "/settings",
          ),
        },
      ],
    },
  } as OpportunityContext);
  await tab("今日重点");
  expect(
    screen.getByRole("button", { name: "不支持的账户入口" }),
  ).toBeDisabled();
  expect(
    screen.queryByRole("link", { name: "不支持的账户入口" }),
  ).not.toBeInTheDocument();
});
