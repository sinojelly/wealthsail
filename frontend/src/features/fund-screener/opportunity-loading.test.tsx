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
import type {
  DailyResearch,
  Direction,
  OpportunityContext,
  ResearchIndex,
} from "../../api/investment";
import { HttpError } from "../../api/client";
import FundScreenerPage from "./FundScreenerPage";

const api = vi.hoisted(() => ({
  directions: vi.fn(),
  indices: vi.fn(),
  market: vi.fn(),
  opportunity: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("../../api/investment", async (original) => ({
  ...(await original<typeof import("../../api/investment")>()),
  getDirections: api.directions,
  getIndices: api.indices,
  getMarketContext: api.market,
  getOpportunityContext: api.opportunity,
  screenFunds: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 }),
  getAnalysis: vi.fn().mockResolvedValue({ state: "NOT_COLLECTED" }),
}));
vi.mock("../../api/watchlist", () => ({ list: vi.fn().mockResolvedValue([]) }));
vi.mock("../../api/study-api", async (original) => ({
  ...(await original<typeof import("../../api/study-api")>()),
  getStudyTracks: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));
vi.mock("../../api/daily-research-debug", () => ({
  getResearchRefresh: api.refresh,
  getResearchRefreshStatus: vi.fn(),
  startResearchRefresh: vi.fn(),
}));
vi.mock("../../api/topic-fund-recommendations", () => ({
  getTopicFundRecommendations: vi
    .fn()
    .mockRejectedValue(new HttpError("missing", 404)),
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

const previousId = "6283bff5-bd75-4dd4-8444-e44756225cf0";
const currentId = "d7188c05-d288-4a4c-973e-a8b47549cc74";
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

function facts(
  value = 50.2,
  dailyResearch?: DailyResearch,
): OpportunityContext {
  return {
    state: "READY",
    items: [
      {
        code: "PMI",
        label: "制造业PMI",
        value,
        asOf: currentAt,
        referencePeriod: "2026-10",
        publishedAt: currentAt,
        source: "官方数据",
        state: "READY",
      },
    ],
    events: [],
    dailyResearch,
  };
}

function daily(overrides: Partial<DailyResearch> = {}): DailyResearch {
  return {
    methodVersion: "daily-research-v1",
    state: "READY",
    runId: previousId,
    reportRunId: previousId,
    reportCompletedAt: reportAt,
    completedAt: reportAt,
    startedAt: reportAt,
    scheduledFor: reportAt,
    asOf: reportAt,
    nextRunAt: null,
    report: {
      asOf: reportAt,
      summary: "上一份有效报告的稳定宏观研判",
      macroCycle: "上一份有效报告的稳定宏观研判",
      valueDirections: [],
      coverage: [],
      sources: [],
      events: [],
      upcoming: [],
      energy: [],
    },
    ...overrides,
  };
}

function observation(
  researchId = previousId,
): NonNullable<DailyResearch["fundObservation"]> {
  return {
    version: "fund-observation-v1",
    researchId,
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
      <MemoryRouter initialEntries={["/fund-screener?researchTab=macro"]}>
        <FundScreenerPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

function refetchOpportunity(client: QueryClient) {
  return client.invalidateQueries({
    predicate: (query) => query.queryKey.includes("opportunity-context"),
  });
}

async function openFullResearch() {
  const summary = await screen.findByText("展开查看完整研究");
  if (!summary.closest("details")?.open) await userEvent.click(summary);
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.values(api).forEach((mock) => mock.mockReset());
  api.directions.mockResolvedValue([]);
  api.indices.mockResolvedValue([]);
  api.market.mockResolvedValue(facts(49.1));
  api.opportunity.mockResolvedValue(facts());
  api.refresh.mockResolvedValue({ enabled: false, request: null });
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

it("waits for the selected opportunity report, including direction/index dependencies, without flashing the faster macro fallback", async () => {
  const directions = deferred<Direction[]>();
  const indices = deferred<ResearchIndex[]>();
  const report = deferred<OpportunityContext>();
  api.directions.mockReturnValue(directions.promise);
  api.indices.mockReturnValue(indices.promise);
  api.opportunity.mockReturnValue(report.promise);
  mountPage();

  await waitFor(() => expect(api.directions).toHaveBeenCalledTimes(1));
  expect(screen.getByText("正在加载展示报告…")).toHaveAttribute(
    "role",
    "status",
  );
  expect(
    screen.queryByRole("heading", { name: "当前宏观事实" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("49.1")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "暂无有效日报" }),
  ).not.toBeInTheDocument();
  expect(api.opportunity).not.toHaveBeenCalled();

  await act(async () =>
    directions.resolve([
      {
        id: "broad",
        marketId: "a-share",
        marketLabel: "A股",
        styleId: "broad",
        label: "宽基",
        framework: "研究盈利与风险",
      },
    ]),
  );
  await waitFor(() => expect(api.indices).toHaveBeenCalledTimes(1));
  expect(screen.getByText("正在加载展示报告…")).toBeVisible();
  expect(api.opportunity).not.toHaveBeenCalled();
  await act(async () =>
    indices.resolve([
      {
        id: "hs300",
        name: "沪深300",
        code: "000300",
        market: "SSE",
        exposure: "宽基",
        fundCount: 1,
        state: "READY",
      },
    ]),
  );
  await waitFor(() => expect(api.opportunity).toHaveBeenCalledTimes(1));
  expect(screen.getByText("正在加载展示报告…")).toBeVisible();
  expect(
    screen.queryByRole("region", { name: "今日宏观总纲" }),
  ).not.toBeInTheDocument();

  await act(async () =>
    report.resolve(facts(50.2, daily({ fundObservation: observation() }))),
  );
  const macro = await screen.findByRole("region", { name: "今日宏观总纲" });
  expect(
    within(macro).getByRole("heading", { name: "当前宏观事实" }),
  ).toBeVisible();
  expect(within(macro).getByText("50.2")).toBeVisible();
  await openFullResearch();
  expect(
    screen.getByRole("region", { name: "服务器代表池基金观察" }),
  ).toBeVisible();
  expect(screen.queryByText("正在加载展示报告…")).not.toBeInTheDocument();
  expect(screen.queryByText("49.1")).not.toBeInTheDocument();
});

it("preserves the current macro section before fund observations when a later refetch adds the representative pool", async () => {
  const next = deferred<OpportunityContext>();
  api.opportunity
    .mockResolvedValueOnce(facts())
    .mockReturnValueOnce(next.promise);
  const { client } = mountPage();
  const macro = await screen.findByRole("region", { name: "今日宏观总纲" });
  const heading = within(macro).getByRole("heading", { name: "当前宏观事实" });
  const pmi = within(macro).getByText("50.2");
  await openFullResearch();

  let refreshing!: Promise<void>;
  act(() => {
    refreshing = refetchOpportunity(client);
  });
  await waitFor(() => expect(api.opportunity).toHaveBeenCalledTimes(2));
  expect(
    screen.getByText("正在更新展示报告，继续显示上一份已加载内容。"),
  ).toHaveAttribute("role", "status");
  expect(heading).toBeVisible();
  expect(pmi).toBeVisible();
  await act(async () => {
    next.resolve(
      facts(
        50.2,
        daily({
          state: "NOT_RUN",
          runId: currentId,
          reportRunId: null,
          reportCompletedAt: null,
          report: null,
          fundObservation: observation(currentId),
        }),
      ),
    );
    await refreshing;
  });

  const funds = await screen.findByRole("region", {
    name: "服务器代表池基金观察",
  });
  expect(screen.getByRole("region", { name: "今日宏观总纲" })).toBe(macro);
  expect(within(macro).getByRole("heading", { name: "当前宏观事实" })).toBe(
    heading,
  );
  expect(within(macro).getByText("50.2")).toBe(pmi);
  expect(
    macro.compareDocumentPosition(funds) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    screen.queryByText("正在更新展示报告，继续显示上一份已加载内容。"),
  ).not.toBeInTheDocument();
});

it("keeps an already loaded valid report and its macro facts throughout background refresh and an HTTP failure", async () => {
  const next = deferred<OpportunityContext>();
  api.opportunity
    .mockResolvedValueOnce(
      facts(50.2, daily({ fundObservation: observation() })),
    )
    .mockReturnValueOnce(next.promise);
  const { client } = mountPage();
  const macro = await screen.findByRole("region", { name: "今日宏观总纲" });
  const summary = within(macro).getByText("上一份有效报告的稳定宏观研判");
  await openFullResearch();
  const funds = screen.getByRole("region", { name: "服务器代表池基金观察" });
  const source = screen.getByLabelText("展示报告来源");
  expect(screen.getByText(`报告ID：${previousId} · 只读`)).toBeInTheDocument();
  expect(
    within(macro).getByRole("heading", { name: "当前宏观事实" }),
  ).toBeVisible();

  let refreshing!: Promise<void>;
  act(() => {
    refreshing = refetchOpportunity(client);
  });
  await waitFor(() => expect(api.opportunity).toHaveBeenCalledTimes(2));
  expect(
    screen.getByText("正在更新展示报告，继续显示上一份已加载内容。"),
  ).toHaveAttribute("role", "status");
  expect(summary).toBeVisible();
  expect(funds).toBeVisible();
  expect(source).toBeVisible();
  await act(async () => {
    next.reject(new HttpError("timeout", 504));
    await refreshing;
  });

  expect(
    await screen.findByText("展示报告刷新失败，继续显示上一份已加载内容。"),
  ).toHaveAttribute("role", "alert");
  expect(screen.getByRole("region", { name: "今日宏观总纲" })).toBe(macro);
  expect(summary).toBeVisible();
  expect(screen.getByRole("region", { name: "服务器代表池基金观察" })).toBe(
    funds,
  );
  expect(screen.getByLabelText("展示报告来源")).toBe(source);
  expect(
    screen.queryByText("展示报告读取失败，请稍后重试。"),
  ).not.toBeInTheDocument();
});

it("separates a timed-out current execution from the previous valid report and keeps both macro facts and fund observations", async () => {
  const retained = daily({
    state: "UNCERTAIN",
    runId: currentId,
    completedAt: currentAt,
    failureStage: "MODEL_EXECUTION",
    failureReasonCode: "TIMEOUT",
    errorClass: "TimeoutError",
    fundObservation: observation(),
  });
  api.opportunity.mockResolvedValue(facts(50.2, retained));
  mountPage();

  const source = await screen.findByLabelText("展示报告来源");
  expect(source).toHaveTextContent("最近有效报告 · 历史 · 只读");
  expect(screen.getByText(`报告ID：${previousId} · 只读`)).toBeInTheDocument();
  expect(source).toHaveTextContent("完成：2026年11月6日 08:00");
  expect(source).not.toHaveTextContent(currentId);
  await userEvent.click(screen.getByText("执行与调度详情"));
  const execution = screen.getByRole("group", { name: "本次执行状态" });
  expect(execution).toHaveTextContent(currentId);
  expect(execution).toHaveTextContent("执行异常 · 待核实");
  const macro = screen.getByRole("region", { name: "今日宏观总纲" });
  expect(
    within(macro).getByRole("heading", { name: "历史宏观研判" }),
  ).toBeVisible();
  expect(
    within(macro).getByRole("heading", { name: "当前宏观事实" }),
  ).toBeVisible();
  expect(within(macro).getByText("上一份有效报告的稳定宏观研判")).toBeVisible();
  expect(within(macro).getByText("50.2")).toBeVisible();
  await openFullResearch();
  expect(
    screen.getByRole("region", { name: "服务器代表池基金观察" }),
  ).toBeVisible();
  await userEvent.click(screen.getByText("执行与报告状态"));
  const diagnosis = within(
    screen.getByText("执行与报告状态").closest("details")!,
  ).getByRole("group", { name: "研究失败诊断" });
  expect(diagnosis).toHaveTextContent("执行超时");
  expect(diagnosis).toHaveTextContent("原因代码：TIMEOUT");
  expect(screen.getByText(`报告ID：${previousId} · 只读`)).toBeInTheDocument();
});

it("shows a loading state on a full page remount until the same selected report returns, with no temporary fallback version", async () => {
  const payload = facts(50.2, daily({ fundObservation: observation() }));
  const next = deferred<OpportunityContext>();
  api.opportunity
    .mockResolvedValueOnce(payload)
    .mockReturnValueOnce(next.promise);
  const first = mountPage();
  const original = await screen.findByRole("region", { name: "今日宏观总纲" });
  const originalText = original.textContent;
  first.unmount();
  first.client.clear();
  mountPage();

  await waitFor(() => expect(api.opportunity).toHaveBeenCalledTimes(2));
  expect(screen.getByText("正在加载展示报告…")).toBeVisible();
  expect(
    screen.queryByRole("region", { name: "今日宏观总纲" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "服务器代表池基金观察" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("49.1")).not.toBeInTheDocument();
  await act(async () => next.resolve(payload));

  expect(
    (await screen.findByRole("region", { name: "今日宏观总纲" })).textContent,
  ).toBe(originalText);
  await openFullResearch();
  expect(
    screen.getByRole("region", { name: "服务器代表池基金观察" }),
  ).toBeVisible();
  expect(screen.queryByText("49.1")).not.toBeInTheDocument();
});

it("reports an initial report request failure without presenting the unrelated successful macro fallback", async () => {
  api.opportunity.mockRejectedValue(new HttpError("unavailable", 503));
  mountPage();
  expect(
    await screen.findByText("展示报告读取失败，请稍后重试。"),
  ).toHaveAttribute("role", "alert");
  expect(
    screen.queryByRole("region", { name: "今日宏观总纲" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "暂无有效日报" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("49.1")).not.toBeInTheDocument();
});

it.each(["directions", "indices"] as const)(
  "ends report loading when the initial %s dependency fails without showing macro fallback",
  async (dependency) => {
    const directions: Direction[] = [
      {
        id: "broad",
        marketId: "a-share",
        marketLabel: "A股",
        styleId: "broad",
        label: "宽基",
        framework: "研究盈利与风险",
      },
    ];
    api.directions.mockResolvedValue(directions);
    api[dependency].mockRejectedValue(new HttpError("unavailable", 503));
    mountPage();

    expect(
      await screen.findByText("展示报告读取失败，请稍后重试。"),
    ).toHaveAttribute("role", "alert");
    expect(screen.queryByText("正在加载展示报告…")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "今日宏观总纲" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "服务器代表池基金观察" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("49.1")).not.toBeInTheDocument();
    expect(api.opportunity).not.toHaveBeenCalled();
  },
);
