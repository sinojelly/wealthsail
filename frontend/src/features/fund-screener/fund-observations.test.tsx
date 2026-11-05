import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  DailyResearch,
  FundObservationItem,
  OpportunityContext,
} from "../../api/investment";
import type { TopicRegistryPage } from "../../api/research-topic-registry";
import { HttpError } from "../../api/client";
import { renderWithSession } from "../../test/render-with-session";
import MacroContextStrip from "./components/MacroContextStrip";
import { getResearchTopicRegistry } from "../../api/research-topic-registry";
import serverItems from "../../test/fund-observation-server-items.json";

vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => ({
    tenantId: "reader-tenant",
    userId: "reader",
    role: "MEMBER",
  }),
}));
vi.mock("../../api/research-topic-registry", () => ({
  getResearchTopicRegistry: vi.fn(),
}));
vi.mock("../../api/research-acceptance", () => ({
  getResearchAcceptance: vi
    .fn()
    .mockRejectedValue(new HttpError("missing", 404)),
}));
vi.mock("../../api/history-repairs", () => ({
  listHistoricalRepairs: vi.fn().mockResolvedValue({ items: [] }),
}));

const run = "ecdf9dce-54e4-4b26-98ab-e1af6f4e1ffb";
const old = "abbc3057-104b-4546-a6b2-ed560f6049f7";
const at = "2026-11-06T08:00:00+08:00";
const fund = (index: number) => ({
  fundCode: String(5000 + index).padStart(6, "0"),
  name: `观察基金${index}`,
  subjectKind: "nav",
  subjectId: String(5000 + index).padStart(6, "0"),
  identityVerified: true,
  identityProof: {
    scope: "FROZEN_SERVER_CATALOG",
    sourceUri: "https://example.org/catalog",
    verifiedAt: at,
  },
  confirmedNav: {
    value: "1.2345",
    asOf: "2026-11-05",
    retrievedAt: at,
    sourceUri: "https://example.org/nav",
  },
  referenceTracks: [
    {
      track: "LEFT_VALUE",
      priceState: "READY",
      technicalState: "CANDIDATE",
      entryZoneMin: "1.2",
      entryZoneMax: "1.25",
      stopLossPrice: "1.1",
      takeProfitT1: "1.4",
      takeProfitT2: "1.5",
      distanceToEntryPct: 0,
      inEntryZone: true,
      observationStatus: "TECHNICAL_IN_ZONE",
      missingEvidence: [],
      metrics: {
        rsi14: 72,
        ma20: "1.24",
        ma60: "1.3",
        ma200: null,
        excessReturn20d: -2,
        excessReturn60d: -8,
        drawdown60d: 12,
      },
    },
  ],
  observationStatus: "OBSERVABLE",
  subscription: { status: "UNKNOWN", restricted: null },
  industry: {
    industryIds: ["ROBOT"],
    unresolvedSector: null,
    auditStatus: "PENDING_PROOF",
    missingChecks: ["FINANCING_COOLING", "PRODUCTION_DELAY"],
  },
  risk: {
    current: [
      {
        industry: "ROBOT",
        researchedAt: at,
        stage: "EARLY_COMMERCIALIZATION",
        riskLevel: "CONFIRMED",
        riskFlags: ["FINANCING_COOLING"],
        evidenceGaps: ["NO_CURRENT_EARNINGS_PROOF"],
        evidenceSummary: "融资降温与订单兑现仍需警惕。",
      },
    ],
    historical: [
      {
        industry: "ROBOT",
        researchedAt: "2026-11-01T08:00:00+08:00",
        active: true,
        stage: "UNKNOWN",
        riskLevel: "SUSPECTED",
        evidenceSummary: "历史量产延期风险尚待解除。",
      },
    ],
  },
  topicAssociations: [
    {
      topicId: "topic-robot",
      key: "robot",
      title: "机器人商业化观察",
      relation: "DECLARED_RELATED_ONLY",
      sourceIds: ["s1"],
    },
  ],
  missingEvidence: [],
  actionable: false,
  investmentEligible: false,
});
function observation() {
  const items = Array.from({ length: 46 }, (_, index) => fund(index));
  return {
    version: "fund-observation-v1",
    researchId: run,
    researchAsOf: at,
    evaluatedAt: at,
    status: "PARTIAL",
    actionable: false,
    investmentEligible: false,
    coverage: {
      catalogFundCount: 46,
      scannedFundCount: 45,
      confirmedNavFundCount: 45,
      pricedFundCount: 39,
      observableFundCount: 39,
      unpricedFundCount: 7,
      highlightCount: 10,
      selectedScopeCount: 5,
      deferredScopeCount: 4,
      executionVerifiedCheckCount: 0,
    },
    highlights: items.slice(0, 10),
    items,
    unresolvedRiskNotice:
      "行业反方证据尚未完整核验，技术位置不能替代投资资格。",
    unresolvedRiskScopes: ["ROBOT"],
  };
}
function daily(): DailyResearch {
  return {
    state: "READY",
    runId: run,
    reportRunId: run,
    completedAt: at,
    reportCompletedAt: at,
    fundObservation: observation(),
    report: {
      summary: "最近成功报告摘要",
      asOf: at,
      valueDirections: [],
      coverage: [],
      sources: [],
      events: [],
      upcoming: [],
      energy: [],
    },
  } as unknown as DailyResearch;
}
function registry(researchId = run): TopicRegistryPage {
  return {
    researchId,
    snapshotId: "snapshot-1",
    validatedAt: at,
    revision: "a".repeat(64),
    actionable: false,
    items: [
      {
        topicId: "topic-robot",
        key: "robot",
        title: "机器人商业化观察",
        summary: "有价值的研究主题仍保留。",
        status: "PENDING_REVIEW",
        reasonCode: "AUDIT_BUDGET_PENDING",
        reason: "行业反方凭证待补齐。",
        impact: "利润传导尚未证实。",
        measures: ["保留主题观察。"],
        pendingTasks: [],
        sourceIds: ["s1"],
        match: {
          status: "NEW_TOPIC",
          industryIds: [],
          relatedIndustryIds: ["ROBOT"],
          evidence: [],
          registryVersion: "registry-v2",
        },
        investmentEligible: false,
      },
    ],
    totalCount: 1,
    nextCursor: null,
    coverage: {
      totalCount: 1,
      plannedScopeCount: 5,
      reviewedScopeCount: 0,
      pendingScopeCount: 5,
      searchExecutionVerified: false,
    },
  };
}
function renderDaily(data = daily()) {
  return renderWithSession(
    <MacroContextStrip
      error={false}
      data={
        {
          state: "READY",
          items: [],
          dailyResearch: data,
        } as unknown as OpportunityContext
      }
    />,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getResearchTopicRegistry).mockImplementation(async (id) =>
    registry(id),
  );
});

it("leads with all saved AI topics, independent top-ten fund observations and strict plans before the retained success summary", async () => {
  renderDaily();
  const topics = await screen.findByRole("region", {
    name: "值得关注的 AI 投资主题",
  });
  const funds = screen.getByRole("region", { name: "服务器代表池基金观察" });
  const plans = screen.getByRole("region", { name: "今日价值方向与买卖计划" });
  const retained = screen.getByRole("region", { name: "最近一次成功研究" });
  expect(within(topics).getByText("机器人商业化观察")).toBeVisible();
  expect(topics).toHaveTextContent("来源核验状态未提供");
  expect(topics).toHaveTextContent("行业范围关联尚未核实");
  expect(
    topics.compareDocumentPosition(funds) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    funds.compareDocumentPosition(plans) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    plans.compareDocumentPosition(retained) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(plans).toHaveTextContent("暂无通过完整风控的正式计划");
  expect(
    screen.queryByText("当前证据尚未形成有充分依据的价值方向。"),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("region", { name: "每日事件研究" }),
  ).not.toHaveTextContent("正式报告主题目录");
  expect(getResearchTopicRegistry).toHaveBeenCalledOnce();
});

it("keeps all three levels with no holdings, no formal report and no formal directions", async () => {
  const data = daily();
  Object.assign(data, { state: "FAILED", report: null, reportRunId: null });
  renderDaily(data);
  expect(await screen.findByText("有价值的研究主题仍保留。")).toBeVisible();
  const funds = screen.getByRole("region", { name: "服务器代表池基金观察" });
  expect(funds).toHaveTextContent("观察基金0");
  expect(screen.getByText("暂无通过完整风控的正式计划")).toBeVisible();
  expect(
    within(funds).queryByRole("button", { name: /跟踪|买入|推送|研究/ }),
  ).not.toBeInTheDocument();
});

it("shows reference prices, missing tracks, high-level exits and unresolved risks without turning a technical hit into a recommendation", async () => {
  renderDaily();
  const funds = await screen.findByRole("region", {
    name: "服务器代表池基金观察",
  });
  expect(funds).toHaveTextContent("45");
  expect(funds).toHaveTextContent("39");
  const first = within(funds).getByRole("article", {
    name: "观察基金0 005000",
  });
  for (const text of [
    "1.2345",
    "2026年11月5日",
    "1.2000–1.2500",
    "0.00%",
    "1.1000",
    "1.4000 / 1.5000",
    "右侧参考轨道缺失",
    "申购状态未知",
    "行业反方审核凭证待补齐",
    "融资降温与订单兑现仍需警惕。",
    "历史量产延期风险尚待解除。",
    "仅声明相关，行业范围关联未核实",
  ])
    expect(first).toHaveTextContent(text);
  expect(first).toHaveTextContent("技术位置在参考区内（非买入许可）");
  expect(first).not.toHaveTextContent("建议买入");
  expect(first).not.toHaveTextContent("已入左侧低吸买区");
  expect(within(first).queryByRole("button")).not.toBeInTheDocument();
  expect(within(first).getByRole("link", { name: "净值来源" })).toHaveAttribute(
    "href",
    "https://example.org/nav",
  );
});

it("defaults to at most ten highlights and makes the complete 46-fund inventory readable without another request", async () => {
  renderDaily();
  const funds = await screen.findByRole("region", {
    name: "服务器代表池基金观察",
  });
  expect(within(funds).getAllByRole("article")).toHaveLength(10);
  expect(within(funds).queryByText("观察基金45")).not.toBeInTheDocument();
  await userEvent.click(within(funds).getByText("查看完整代表池 · 46只基金"));
  expect(await within(funds).findByText(/观察基金45/)).toBeVisible();
  expect(getResearchTopicRegistry).toHaveBeenCalledOnce();
});

it("rejects an unrelated observation identity and preserves the explicit retained success report and its date", async () => {
  const data = daily();
  Object.assign(data, {
    state: "FAILED",
    reportRunId: old,
    reportCompletedAt: "2026-11-05T08:00:00+08:00",
  });
  Object.assign(data.fundObservation!, {
    researchId: "00000000-0000-0000-0000-000000000001",
  });
  renderDaily(data);
  const funds = await screen.findByRole("region", {
    name: "服务器代表池基金观察",
  });
  expect(funds).toHaveTextContent("基金观察身份不一致，暂不展示内容。");
  expect(funds).not.toHaveTextContent("观察基金0");
  const retained = screen.getByRole("region", { name: "最近一次成功研究" });
  expect(retained).toHaveTextContent("2026年11月5日 08:00");
  expect(retained).toHaveTextContent("今日尚无新的正式研究");
});

it("keeps server high-exit guards and original historical risk dates visible even inside a technical reference zone", async () => {
  const data = daily();
  Object.assign(data.fundObservation!.highlights[0].referenceTracks[0], {
    entryBlocked: true,
    inSellZone: true,
    entryStatusTone: "rose",
    entryStatus: "服务器已确认高位退出观察条件，当前暂缓入场。",
    entryBlockReason: "IN_SELL_ZONE",
  });
  Object.assign(data.fundObservation!.highlights[0].risk.historical[0], {
    riskFlags: [],
    persistedRiskFlags: ["PRODUCTION_DELAY"],
    confirmedRiskAt: { PRODUCTION_DELAY: "2026-10-30T08:00:00+08:00" },
  });
  renderDaily(data);
  const first = await screen.findByRole("article", {
    name: "观察基金0 005000",
  });
  expect(first).toHaveTextContent(
    "服务器已确认高位退出观察条件，当前暂缓入场。",
  );
  expect(first).toHaveTextContent(
    "历史风险原始确认：量产推迟 · 2026年10月30日 08:00",
  );
  expect(within(first).queryByRole("button")).not.toBeInTheDocument();
});

it("allows explicitly retained observations while preserving the old READY topic directory separately from this run", async () => {
  const data = daily();
  Object.assign(data, {
    state: "FAILED",
    reportRunId: old,
    reportCompletedAt: "2026-11-05T08:00:00+08:00",
  });
  Object.assign(data.fundObservation!, {
    researchId: old,
    researchAsOf: "2026-11-05T08:00:00+08:00",
  });
  renderDaily(data);
  const funds = await screen.findByRole("region", {
    name: "服务器代表池基金观察",
  });
  expect(funds).toHaveTextContent("此为最近成功报告对应的保留观察");
  const current = await screen.findByRole("region", {
    name: "值得关注的 AI 投资主题",
  });
  const retained = await screen.findByRole("region", {
    name: "正式报告主题目录",
  });
  expect(current).toHaveTextContent(run);
  expect(retained).toHaveTextContent(old);
  expect(getResearchTopicRegistry).toHaveBeenCalledTimes(2);
});

it("renders actual server-projected NAV independently of incomplete reference tracks without synthesizing coordinates", async () => {
  const data = daily();
  const items = serverItems as unknown as FundObservationItem[];
  Object.assign(data.fundObservation!, {
    highlights: items.slice(0, 1),
    items,
    coverage: {
      catalogFundCount: 2,
      scannedFundCount: 2,
      confirmedNavFundCount: 2,
      pricedFundCount: 1,
      observableFundCount: 1,
      unpricedFundCount: 1,
      highlightCount: 1,
      selectedScopeCount: 10,
      deferredScopeCount: 52,
      executionVerifiedCheckCount: 0,
    },
  });
  renderDaily(data);
  const funds = await screen.findByRole("region", {
    name: "服务器代表池基金观察",
  });
  const first = within(funds).getByRole("article", {
    name: "广发中证军工ETF联接A 003017",
  });
  expect(first).toHaveTextContent("1.0293");
  expect(first).toHaveTextContent("0.9822–1.0336");
  expect(first).toHaveTextContent("技术位置在参考区内（非买入许可）");
  expect(first).toHaveTextContent(
    "基金与沪深300最新交易日期不同，未对旧基准补值。",
  );
  await userEvent.click(within(funds).getByText("查看完整代表池 · 2只基金"));
  const partialTitle = await within(funds).findByText(/富国中证消费50ETF联接A/);
  await userEvent.click(partialTitle);
  const partial = partialTitle.closest("details")!;
  expect(
    await within(partial).findByText(/完整参考价格轨道不足/),
  ).toBeVisible();
  expect(partial).toHaveTextContent("1.2151");
  expect(partial).toHaveTextContent("原参考区：未知–未知");
  expect(partial).toHaveTextContent("🚀 高于买区 5.91%·等待回落买区");
  expect(within(funds).queryByRole("button")).not.toBeInTheDocument();
});

it("keeps an unavailable frozen-observation receipt visible with its evidence gap instead of hiding the card", async () => {
  const data = daily();
  Object.assign(data.fundObservation!, {
    status: "UNAVAILABLE",
    highlights: [],
    items: [],
    coverage: {
      catalogFundCount: 0,
      scannedFundCount: 0,
      confirmedNavFundCount: 0,
      pricedFundCount: 0,
      observableFundCount: 0,
      unpricedFundCount: 0,
      highlightCount: 0,
      selectedScopeCount: 0,
      deferredScopeCount: 0,
      executionVerifiedCheckCount: 0,
    },
    unresolvedRiskNotice:
      "本轮基金观察冻结数据不完整，不影响其他研究成果；请核对原始资料。",
    missingEvidence: ["FROZEN_OBSERVATION_INVALID"],
  });
  renderDaily(data);
  const funds = await screen.findByRole("region", {
    name: "服务器代表池基金观察",
  });
  expect(funds).toHaveTextContent("基金观察资料不足");
  expect(funds).toHaveTextContent(
    "本轮基金观察冻结数据不完整，不影响其他研究成果",
  );
  expect(funds).toHaveTextContent("冻结基金观察数据未通过校验");
  expect(within(funds).queryByRole("article")).not.toBeInTheDocument();
});

it("explains unverified NAV provenance without promoting a reference track to current price authority", async () => {
  const data = daily();
  Object.assign(data.fundObservation!.highlights[0], {
    missingEvidence: ["NAV_PROVENANCE_UNVERIFIED", "INVALID_SCAN_STRUCTURE"],
  });
  renderDaily(data);
  const first = await screen.findByRole("article", {
    name: "观察基金0 005000",
  });
  expect(first).toHaveTextContent("净值来源与证据时间尚未核实");
  expect(first).toHaveTextContent("冻结扫描结构未通过校验");
  expect(first).not.toHaveTextContent("NAV_PROVENANCE_UNVERIFIED");
  expect(within(first).queryByRole("button")).not.toBeInTheDocument();
});
