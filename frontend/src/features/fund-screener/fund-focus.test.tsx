import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { DailyResearch, OpportunityContext } from "../../api/investment";
import type { StudyTrack } from "../../api/study-api";
import type { TopicFundRecommendationsPage } from "../../api/topic-fund-recommendations";
import { HttpError } from "../../api/client";
import FundScreenerPage from "./FundScreenerPage";

const api = vi.hoisted(() => ({
  directions: vi.fn(),
  opportunity: vi.fn(),
  tracks: vi.fn(),
  recommendations: vi.fn(),
}));
const session = vi.hoisted(() => ({
  tenantId: "fund-focus-tenant",
  tenantName: "投资家庭",
  userId: "fund-focus-reader",
  displayName: "研究读者",
  email: null,
  role: "MEMBER" as const,
  platformRoles: [],
  accessToken: "test-session-token",
  expiresIn: 3600,
}));
vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => session,
}));
vi.mock("../../api/investment", async (original) => ({
  ...(await original<typeof import("../../api/investment")>()),
  getDirections: api.directions,
  getOpportunityContext: api.opportunity,
  screenFunds: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 }),
}));
vi.mock("../../api/watchlist", () => ({ list: vi.fn().mockResolvedValue([]) }));
vi.mock("../../api/study-api", async (original) => ({
  ...(await original<typeof import("../../api/study-api")>()),
  getStudyTracks: api.tracks,
}));
vi.mock("../../api/funds", async (original) => ({
  ...(await original<typeof import("../../api/funds")>()),
  getLatestFundQuotes: vi.fn().mockResolvedValue({ items: [] }),
}));
vi.mock("../../api/topic-fund-recommendations", () => ({
  getTopicFundRecommendations: api.recommendations,
}));
vi.mock("../../api/daily-research-debug", () => ({
  getResearchRefresh: vi
    .fn()
    .mockResolvedValue({ enabled: false, request: null }),
  getResearchRefreshStatus: vi.fn(),
  startResearchRefresh: vi.fn(),
}));
vi.mock("../../api/research-acceptance", () => ({
  getResearchAcceptance: vi
    .fn()
    .mockRejectedValue(new HttpError("missing", 404)),
}));
vi.mock("../../api/research-topic-registry", () => ({
  getResearchTopicRegistry: vi
    .fn()
    .mockRejectedValue(new HttpError("missing", 404)),
}));
vi.mock("../../api/history-repairs", () => ({
  listHistoricalRepairs: vi.fn().mockResolvedValue({ items: [] }),
}));

const reportId = "ecbfb663-1111-4111-8111-111111111111";
const currentId = "c9ac567a-2222-4222-8222-222222222222";
const reportAt = "2026-11-06T08:00:00+08:00";
const currentAt = "2026-11-07T08:00:00+08:00";
const clients: QueryClient[] = [];

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function daily(
  state: "READY" | "RUNNING" | "FAILED" | "UNCERTAIN" = "UNCERTAIN",
): DailyResearch {
  return {
    methodVersion: "daily-research-v1",
    state,
    runId: state === "READY" ? reportId : currentId,
    reportRunId: reportId,
    reportCompletedAt: reportAt,
    completedAt: state === "RUNNING" ? null : currentAt,
    startedAt: currentAt,
    scheduledFor: currentAt,
    asOf: currentAt,
    nextRunAt: null,
    report: {
      asOf: reportAt,
      summary: "历史有效报告的宏观研判",
      macroCycle: "历史有效报告的宏观研判",
      valueDirections: [],
      coverage: [],
      sources: [],
      events: [],
      upcoming: [],
      energy: [],
    },
  };
}

function context(research = daily()): OpportunityContext {
  return {
    state: "READY",
    items: [
      {
        code: "PMI",
        label: "制造业PMI",
        value: 50.2,
        asOf: currentAt,
        publishedAt: currentAt,
        source: "官方数据",
        state: "READY",
      },
    ],
    events: [],
    dailyResearch: research,
  };
}

function trackedFund(): StudyTrack {
  return {
    id: "tracked-ai",
    title: "人工智能冻结研究",
    enabled: true,
    generation: 1,
    status: "OBSERVING",
    createdAt: reportAt,
    updatedAt: currentAt,
    unreadCount: 0,
    scope: { fundCode: "012733", fundMarket: "FUND_CN" },
    priceWatch: {
      frozenPlan: {
        methodVersion: "actionable-zones-v3",
        subjectKind: "nav",
        subjectId: "012733",
        fundCode: "012733",
        label: "个人人工智能联接基金",
        priceBasis: "UNIT_NAV",
        unit: "CNY/份",
        asOf: reportAt,
        sourceUri: null,
        currentPrice: "1.0000",
        indicators: {},
        selectedTrack: "LEFT_VALUE",
        tracks: [
          {
            track: "LEFT_VALUE",
            state: "CANDIDATE",
            priceState: "READY",
            entryZoneMin: "0.9800",
            entryZoneMax: "1.0200",
            stopLossPrice: "0.9000",
            takeProfitT1: "1.1200",
            takeProfitT2: "1.2000",
            reasons: [],
          },
        ],
      },
      confirmedNav: { value: "1.0100", asOf: reportAt, usable: true },
      estimate1430: null,
      holding: { state: "NONE" },
      state: "ENTRY_ZONE",
      basis: "CONFIRMED_NAV",
      asOf: reportAt,
      reason: "确认净值进入冻结买入区间",
    },
  };
}

function recommendations(id = reportId): TopicFundRecommendationsPage {
  return {
    researchId: id,
    validatedAt: reportAt,
    evaluatedAt: reportAt,
    status: "AVAILABLE",
    archiveState: "ARCHIVED",
    actionable: false,
    coverage: { topicCount: 1, matchedTopicCount: 1, recommendedFundCount: 1 },
    items: [
      {
        topicId: "historical-robot",
        key: "robot",
        title: "历史机器人订单机会",
        thesis: "订单增长可能传导至产业链盈利。",
        sourceIds: ["report-source"],
        riskStatus: "UNVERIFIED_RISK",
        riskScorePenalty: 0,
        riskWarnings: ["风险资料仍需核验"],
        recommendationStatus: "MATCHED",
        missingEvidence: [],
        actionable: false,
        recommendedFunds: [
          {
            fundCode: "000001",
            name: "历史归档机器人基金",
            matchLevel: "DYNAMIC_MATCH",
            matchReason: "跟踪指数覆盖机器人产业链",
            subscription: { status: "UNKNOWN", restricted: null },
            quantitativeScore: null,
            missingEvidence: [],
          },
        ],
      },
    ],
  };
}

function mountPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, refetchOnWindowFocus: false },
    },
  });
  clients.push(client);
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/fund-screener"]}>
        <FundScreenerPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.directions.mockResolvedValue([]);
  api.opportunity.mockResolvedValue(context());
  api.tracks.mockResolvedValue({ items: [trackedFund()], total: 1 });
  api.recommendations.mockImplementation((id: string) =>
    id === reportId
      ? Promise.resolve(recommendations())
      : Promise.reject(new HttpError("matching record absent", 404)),
  );
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

it("loads personal tracked funds while the display report is still pending and preserves the table when it arrives", async () => {
  const report = deferred<OpportunityContext>();
  api.opportunity.mockReturnValue(report.promise);
  mountPage();
  expect(screen.getByText("正在加载展示报告…")).toBeVisible();
  const tracking = await screen.findByRole("region", {
    name: "我的持续跟踪基金",
  });
  const fund = await within(tracking).findByText(/个人人工智能联接基金/);
  expect(fund).toBeVisible();
  expect(tracking).toHaveTextContent("冻结买入区间：0.9800–1.0200");
  const table = within(tracking).getByRole("table", {
    name: "持续跟踪与持仓基金",
  });
  await act(async () => report.resolve(context()));
  const macro = await screen.findByRole("region", { name: "今日宏观总纲" });
  expect(screen.getByRole("region", { name: "我的持续跟踪基金" })).toBe(
    tracking,
  );
  expect(
    within(tracking).getByRole("table", { name: "持续跟踪与持仓基金" }),
  ).toBe(table);
  expect(within(tracking).getByText(/个人人工智能联接基金/)).toBe(fund);
  expect(macro.nextElementSibling).toBe(tracking);
});

it("keeps personal funds readable after an initial report HTTP failure", async () => {
  api.opportunity.mockRejectedValue(new HttpError("report unavailable", 503));
  mountPage();
  expect(
    await screen.findByText("展示报告读取失败，请稍后重试。"),
  ).toHaveAttribute("role", "alert");
  const tracking = await screen.findByRole("region", {
    name: "我的持续跟踪基金",
  });
  expect(
    await within(tracking).findByText(/个人人工智能联接基金/),
  ).toBeVisible();
});

it.each(["RUNNING", "FAILED", "UNCERTAIN"] as const)(
  "uses the displayed READY report's matching while the current research is %s",
  async (state) => {
    api.opportunity.mockResolvedValue(context(daily(state)));
    mountPage();
    const opportunities = await screen.findByRole("region", {
      name: "高价值机会",
    });
    expect(
      await within(opportunities).findByText(/000001.*历史归档机器人基金/),
    ).toBeVisible();
    expect(opportunities).toHaveTextContent(reportId);
    expect(opportunities).not.toHaveTextContent(currentId);
    expect(opportunities).toHaveTextContent(/历史.*只读|只读.*历史/);
    const tracking = screen.getByRole("region", { name: "我的持续跟踪基金" });
    expect(
      await within(tracking).findByText(/个人人工智能联接基金/),
    ).toBeVisible();
    expect(
      screen.getByRole("region", { name: "今日宏观总纲" }).nextElementSibling,
    ).toBe(tracking);
    expect(
      tracking.compareDocumentPosition(opportunities) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      screen.getByRole("group", { name: "本次执行状态" }),
    ).toHaveTextContent(currentId);
    const current = screen.getByRole("region", { name: "本次研究基金观察" });
    expect(current).toHaveTextContent(currentId);
    if (state === "RUNNING") {
      expect(current).toHaveTextContent(
        "研究尚未完成，基金匹配等待研究结果归档。",
      );
      expect(api.recommendations.mock.calls.map(([id]) => id)).toEqual([
        reportId,
      ]);
    } else {
      await waitFor(() =>
        expect(api.recommendations).toHaveBeenCalledWith(
          currentId,
          expect.anything(),
        ),
      );
      expect(
        await within(current).findByText(
          "未找到该研究的基金匹配归档记录；缺少记录不代表没有基金机会。",
        ),
      ).toBeVisible();
      expect(current).not.toHaveTextContent("历史归档机器人基金");
    }
  },
);

it("distinguishes missing historical matching from an archived zero result in the current research without hiding either module", async () => {
  const empty = recommendations(currentId);
  empty.items = [];
  empty.coverage = {
    topicCount: 0,
    matchedTopicCount: 0,
    recommendedFundCount: 0,
  };
  api.recommendations.mockImplementation((id: string) =>
    id === reportId
      ? Promise.reject(new HttpError("matching record absent", 404))
      : Promise.resolve(empty),
  );
  mountPage();
  const historical = await screen.findByRole("region", { name: "高价值机会" });
  expect(
    await within(historical).findByText(
      "未找到该研究的基金匹配归档记录；缺少记录不代表没有基金机会。",
    ),
  ).toBeVisible();
  expect(historical).toHaveTextContent(reportId);
  expect(historical).not.toHaveTextContent("已归档的匹配结果为零");
  const current = screen.getByRole("region", { name: "本次研究基金观察" });
  expect(
    await within(current).findByText(
      "已归档的匹配结果为零；这仅表示该研究未匹配到基金，不代表市场没有机会。",
    ),
  ).toBeVisible();
  expect(current).toHaveTextContent(currentId);
  expect(current).not.toHaveTextContent("未找到该研究的基金匹配归档记录");
  expect(
    screen.getByRole("region", { name: "我的持续跟踪基金" }),
  ).toBeVisible();
});

it("keeps long research collapsed while personal funds and archived opportunity summaries stay visible", async () => {
  const research = daily();
  research.fundObservation = {
    version: "fund-observation-v1",
    researchId: reportId,
    researchAsOf: reportAt,
    evaluatedAt: currentAt,
    status: "PARTIAL",
    actionable: false,
    investmentEligible: false,
    coverage: {
      catalogFundCount: 46,
      scannedFundCount: 45,
      confirmedNavFundCount: 40,
      pricedFundCount: 35,
      observableFundCount: 35,
      unpricedFundCount: 11,
      highlightCount: 0,
      selectedScopeCount: 5,
      deferredScopeCount: 4,
      executionVerifiedCheckCount: 0,
    },
    highlights: [],
    items: [],
    unresolvedRiskNotice: null,
    unresolvedRiskScopes: [],
  };
  api.opportunity.mockResolvedValue(context(research));
  mountPage();
  expect(await screen.findByText(/000001.*历史归档机器人基金/)).toBeVisible();
  const disclosure = screen.getByText("展开查看完整研究").closest("details");
  expect(disclosure).not.toHaveAttribute("open");
  expect(
    screen.getByRole("region", { name: "我的持续跟踪基金" }),
  ).toBeVisible();
  expect(screen.getByRole("region", { name: "高价值机会" })).toBeVisible();
  const representative = screen.getByText("服务器代表池基金观察 · 只读");
  expect(representative).not.toBeVisible();
  expect(disclosure).toContainElement(representative);
  expect(disclosure).toContainElement(screen.getByText("完整研究主题目录"));
  expect(disclosure).toContainElement(screen.getByText("最近一次成功研究"));
  await userEvent.click(screen.getByText("展开查看完整研究"));
  expect(representative).toBeVisible();
  expect(
    screen.getByRole("region", { name: "我的持续跟踪基金" }),
  ).toBeVisible();
  expect(screen.getByRole("region", { name: "高价值机会" })).toBeVisible();
});

it("preserves displayed report opportunities and personal fund nodes across a current timeout and an HTTP refetch failure", async () => {
  const running = deferred<OpportunityContext>();
  const timeout = deferred<OpportunityContext>();
  api.opportunity
    .mockResolvedValueOnce(context(daily("RUNNING")))
    .mockReturnValueOnce(running.promise)
    .mockReturnValueOnce(timeout.promise);
  const { client } = mountPage();
  const opportunities = await screen.findByRole("region", {
    name: "高价值机会",
  });
  const fund =
    await within(opportunities).findByText(/000001.*历史归档机器人基金/);
  const tracking = screen.getByRole("region", { name: "我的持续跟踪基金" });
  const ownFund = await within(tracking).findByText(/个人人工智能联接基金/);
  const invalidate = () =>
    client.invalidateQueries({
      predicate: (query) => query.queryKey.includes("opportunity-context"),
    });
  let first!: Promise<void>;
  act(() => {
    first = invalidate();
  });
  expect(
    await screen.findByText("正在更新展示报告，继续显示上一份已加载内容。"),
  ).toBeVisible();
  expect(fund).toBeVisible();
  expect(ownFund).toBeVisible();
  await act(async () => {
    running.resolve(context(daily("UNCERTAIN")));
    await first;
  });
  await waitFor(() =>
    expect(
      screen.getByRole("group", { name: "本次执行状态" }),
    ).toHaveTextContent("执行异常 · 待核实"),
  );
  expect(screen.getByRole("region", { name: "高价值机会" })).toBe(
    opportunities,
  );
  expect(within(opportunities).getByText(/000001.*历史归档机器人基金/)).toBe(
    fund,
  );
  expect(screen.getByRole("region", { name: "我的持续跟踪基金" })).toBe(
    tracking,
  );
  expect(within(tracking).getByText(/个人人工智能联接基金/)).toBe(ownFund);
  expect(screen.getByRole("group", { name: "本次执行状态" })).toHaveTextContent(
    "执行异常 · 待核实",
  );
  let second!: Promise<void>;
  act(() => {
    second = invalidate();
  });
  await waitFor(() => expect(api.opportunity).toHaveBeenCalledTimes(3));
  await act(async () => {
    timeout.reject(new HttpError("timeout", 504));
    await second;
  });
  expect(
    await screen.findByText("展示报告刷新失败，继续显示上一份已加载内容。"),
  ).toHaveAttribute("role", "alert");
  expect(screen.getByRole("region", { name: "高价值机会" })).toBe(
    opportunities,
  );
  expect(fund).toBeVisible();
  expect(screen.getByRole("region", { name: "我的持续跟踪基金" })).toBe(
    tracking,
  );
  expect(ownFund).toBeVisible();
  expect(screen.getByLabelText("展示报告来源")).toHaveTextContent(reportId);
});
