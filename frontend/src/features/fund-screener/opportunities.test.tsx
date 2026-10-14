import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FundScreenerPage from "./FundScreenerPage";
import { renderWithSession } from "../../test/render-with-session";

it("filters evidence candidates through the server independently of technical score", async () => {
  const user = userEvent.setup();
  renderWithSession(<FundScreenerPage />);
  await user.click(await screen.findByRole("button", { name: "高级筛选" }));
  await user.selectOptions(screen.getByLabelText("研究资格"), "true");
  await waitFor(() =>
    expect(api.funds).toHaveBeenLastCalledWith(
      expect.objectContaining({ valueCandidateOnly: "true" }),
      expect.anything(),
    ),
  );
});

it("queries and preserves the selected macro history window", async () => {
  const user = userEvent.setup();
  renderWithSession(<FundScreenerPage />);
  await user.click(await screen.findByRole("button", { name: /市场环境概览/ }));
  await user.selectOptions(screen.getByLabelText("宏观观察窗口"), "60");
  await waitFor(() =>
    expect(api.context).toHaveBeenLastCalledWith(expect.anything(), 60),
  );
});

it("opens a macro direction through existing selection and clears the previous fund and page", async () => {
  const user = userEvent.setup();
  api.directions.mockResolvedValue([
    {
      id: "a-share:broad",
      marketId: "a-share",
      marketLabel: "A股",
      styleId: "broad",
      label: "宽基",
      framework: "盈利研究",
    },
    {
      id: "a-share:cyclical",
      marketId: "a-share",
      marketLabel: "A股",
      styleId: "cyclical",
      label: "周期",
      framework: "供需研究",
      macroResearch: {
        summary: "价格与成本渠道值得核查。",
        channels: [],
        missingEvidence: ["利润"],
        methodVersion: "macro-research-v1",
      },
    },
  ]);
  api.indices.mockImplementation(async (id) =>
    id === "a-share:cyclical"
      ? [{ id: "cyclical-index", name: "周期指数", code: "000901" }]
      : [{ id: "hs300", name: "沪深300", code: "000300" }],
  );
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-id&page=2&macroMonths=60",
  );
  await user.click(await screen.findByRole("button", { name: /市场环境概览/ }));
  await user.click(
    await screen.findByRole("button", { name: "研究 A股 · 周期" }),
  );
  await waitFor(() =>
    expect(api.funds).toHaveBeenLastCalledWith(
      expect.objectContaining({
        directionId: "a-share:cyclical",
        indexId: "cyclical-index",
        page: 1,
      }),
      expect.anything(),
    ),
  );
  expect(screen.getByLabelText("研究路径")).toHaveTextContent(
    "A股 / 周期 / 周期指数",
  );
  expect(screen.getByRole("button", { name: "关注指数" })).toBeDisabled();
  expect(screen.getByLabelText("宏观观察窗口")).toHaveValue("60");
});

it("refreshes the candidate list when research data is updated", async () => {
  const user = userEvent.setup();
  api.refresh.mockResolvedValue({ runId: "new-data", created: true });
  api.refreshStatus.mockResolvedValue({ status: "COMPLETED" });
  renderWithSession(<FundScreenerPage />);
  await screen.findByRole("heading", { name: "沪深300" });
  const button = await screen.findByRole("button", { name: "更新研究数据" });
  await waitFor(() => expect(button).toBeEnabled());
  const before = api.funds.mock.calls.length;
  await user.click(button);
  await waitFor(() =>
    expect(api.funds.mock.calls.length).toBeGreaterThan(before),
  );
});

it("polls a second refresh even when the backend reuses the completed run ID", async () => {
  const user = userEvent.setup();
  api.refresh.mockResolvedValue({ runId: "same-id", created: true });
  api.refreshStatus.mockResolvedValue({ status: "COMPLETED" });
  renderWithSession(<FundScreenerPage />);
  await screen.findByRole("heading", { name: "沪深300" });
  const update = await screen.findByRole("button", { name: "更新研究数据" });
  await waitFor(() => expect(update).toBeEnabled());
  await user.click(update);
  await screen.findByText("研究数据已更新。");
  const first = api.refreshStatus.mock.calls.length;
  await user.click(update);
  await waitFor(() =>
    expect(api.refreshStatus.mock.calls.length).toBeGreaterThan(first),
  );
});

it("resolves equal codes by the instrument identity supplied in the URL", async () => {
  const same = {
    ...fund,
    instrumentId: "other-id",
    fundName: "场外同码基金",
    tradeForm: "FUND",
    market: "FUND_CN",
  };
  api.funds.mockResolvedValue({
    items: [fund, same],
    total: 2,
    page: 1,
    pageSize: 20,
  });
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=other-id&fundMarket=FUND_CN",
  );
  await waitFor(() =>
    expect(api.analysis).toHaveBeenLastCalledWith(
      "fund",
      "other-id",
      expect.anything(),
    ),
  );
  expect(
    await screen.findByRole("heading", { name: "场外同码基金" }),
  ).toBeInTheDocument();
});

it("allows verified master-only OTC results to use watchlist auto-registration", async () => {
  const user = userEvent.setup();
  api.search.mockResolvedValue([
    {
      ...fund,
      instrumentId: null,
      kind: "fund",
      id: "FUND_CN:110022",
      fundCode: "110022",
      code: "110022",
      fundName: "场外基金",
      name: "场外基金",
      tradeForm: "FUND",
      market: "FUND_CN",
      indexId: null,
      directionId: null,
    },
  ]);
  renderWithSession(<FundScreenerPage />);
  await user.type(
    await screen.findByRole("combobox", { name: "全局搜索" }),
    "110022",
  );
  await user.click(await screen.findByRole("option", { name: /场外基金/ }));
  await user.click(screen.getByRole("button", { name: "加入自选基金" }));
  await waitFor(() =>
    expect(api.add).toHaveBeenCalledWith(
      expect.objectContaining({
        symbol: "110022",
        market: "FUND_CN",
        assetType: "FUND",
      }),
    ),
  );
});

it("shows collected ETF analysis and its score in the expanded list", async () => {
  const user = userEvent.setup();
  api.funds.mockResolvedValue({
    items: [
      {
        ...fund,
        dataState: "READY",
        technicalScore: 75,
        technicalState: "STRONG",
      },
    ],
    total: 1,
    page: 1,
    pageSize: 20,
  });
  renderWithSession(<FundScreenerPage />);
  await user.click(await screen.findByRole("button", { name: /展开更多列/ }));
  expect(await screen.findByText("已分析")).toBeInTheDocument();
  expect(screen.getByText("75 · 趋势较强")).toBeInTheDocument();
});

const api = vi.hoisted(() => ({
  directions: vi.fn(),
  indices: vi.fn(),
  context: vi.fn(),
  opportunity: vi.fn(),
  monitors: vi.fn(),
  setMonitor: vi.fn(),
  ack: vi.fn(),
  funds: vi.fn(),
  search: vi.fn(),
  analysis: vi.fn(),
  refresh: vi.fn(),
  refreshStatus: vi.fn(),
  add: vi.fn(),
  list: vi.fn(),
}));
vi.mock("../../api/investment", () => ({
  getDirections: api.directions,
  getIndices: api.indices,
  getMarketContext: api.context,
  getOpportunityContext: api.opportunity,
  getConditionMonitors: api.monitors,
  setConditionMonitor: api.setMonitor,
  ackConditionNotice: api.ack,
  screenFunds: api.funds,
  searchResearch: api.search,
  getAnalysis: api.analysis,
  refreshResearch: api.refresh,
  getRefreshStatus: api.refreshStatus,
}));
vi.mock("../../api/watchlist", () => ({ add: api.add, list: api.list }));

const fund = {
  instrumentId: "fund-id",
  fundCode: "510330",
  fundName: "华夏沪深300ETF",
  fundType: "ETF",
  tradeForm: "ETF",
  market: "SSE",
  currency: "CNY",
  dataState: "NOT_COLLECTED",
  technicalScore: null,
  navRangePosition: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.directions.mockResolvedValue([
    {
      id: "a-share:broad",
      marketId: "a-share",
      marketLabel: "A股",
      styleId: "broad",
      label: "宽基",
      framework: "研究盈利与风险",
    },
    {
      id: "hk:broad",
      marketId: "hk",
      marketLabel: "港股",
      styleId: "broad",
      label: "宽基",
      framework: "汇率风险",
    },
  ]);
  api.indices.mockImplementation(async (id) =>
    id === "hk:broad"
      ? [{ id: "hsi", name: "恒生指数", code: "HSI" }]
      : [
          { id: "hs300", name: "沪深300", code: "000300" },
          { id: "a500", name: "中证A500", code: "000510" },
        ],
  );
  api.context.mockResolvedValue({ state: "UNVERIFIED", items: [] });
  api.opportunity.mockResolvedValue({ state: "UNVERIFIED", items: [] });
  api.monitors.mockResolvedValue([]);
  api.setMonitor.mockImplementation(async (scope) => ({
    id: "monitor-1",
    enabled: scope.enabled,
  }));
  api.ack.mockResolvedValue({ id: "notice-1", readAt: "2026-10-14T04:00:00Z" });
  api.funds.mockResolvedValue({
    items: [fund],
    page: 1,
    pageSize: 20,
    total: 21,
  });
  api.analysis.mockResolvedValue({
    state: "NOT_COLLECTED",
    score: null,
    indicators: {},
    points: [],
    reasons: [],
  });
  api.list.mockResolvedValue([]);
  api.add.mockResolvedValue({ symbol: "510330" });
  api.search.mockResolvedValue([
    {
      ...fund,
      kind: "fund",
      id: "fund-id",
      name: fund.fundName,
      code: fund.fundCode,
      indexId: "hsi",
      directionId: "hk:broad",
    },
  ]);
});

it("queries the full private opportunity scope including fund market", async () => {
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-id&fundMarket=SSE&macroMonths=60",
  );
  await waitFor(() =>
    expect(api.opportunity).toHaveBeenLastCalledWith(
      {
        directionId: "a-share:broad",
        indexId: "hs300",
        instrumentId: "fund-id",
        fundCode: "510330",
        fundMarket: "SSE",
        months: 60,
      },
      expect.anything(),
    ),
  );
});

it("restores only the matching condition monitor and mutates it on an explicit click", async () => {
  const user = userEvent.setup();
  const scope = {
    directionId: "a-share:broad",
    indexId: "hs300",
    instrumentId: "fund-id",
    fundCode: "510330",
    fundMarket: "SSE",
  };
  api.monitors.mockResolvedValue([
    { ...scope, id: "wrong-market", fundMarket: "FUND_CN", enabled: true },
    { ...scope, id: "monitor-1", enabled: true },
  ]);
  api.opportunity.mockResolvedValue({
    state: "UNVERIFIED",
    items: [],
    radar: {
      methodVersion: "opportunity-radar-v1",
      asOf: null,
      headline: {
        id: "gap",
        priority: "INFO",
        kind: "GAP",
        summary: "等待证据",
        impact: "先核查证据",
        evidenceIds: [],
        timeScale: "月度",
        relatedDirectionIds: [],
      },
      questions: [],
      changes: [],
      sections: [],
    },
    notices: [
      {
        id: "notice-1",
        title: "研究条件变化",
        summary: "证据变化",
        severity: "INFO",
        createdAt: "2026-10-14T04:00:00Z",
        readAt: null,
      },
    ],
  });
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-id&fundMarket=SSE",
  );
  await user.click(await screen.findByRole("button", { name: /市场环境概览/ }));
  expect(api.setMonitor).not.toHaveBeenCalled();
  expect(api.ack).not.toHaveBeenCalled();
  await user.click(
    await screen.findByRole("button", { name: "停止跟踪研究条件" }),
  );
  await waitFor(() =>
    expect(api.setMonitor).toHaveBeenCalledWith({ ...scope, enabled: false }),
  );
  await user.click(
    screen.getByRole("button", { name: "标记研究条件变化为已读" }),
  );
  await waitFor(() => expect(api.ack).toHaveBeenCalledWith("notice-1"));
  expect(await screen.findByText("已读")).toBeInTheDocument();
});

function radarResponse(summary: string) {
  return {
    state: "UNVERIFIED",
    items: [],
    radar: {
      methodVersion: "opportunity-radar-v1",
      asOf: null,
      headline: {
        id: summary,
        priority: "CRITICAL",
        kind: "GAP",
        summary,
        impact: summary + "的关键影响",
        evidenceIds: [],
        timeScale: "每日盘后",
        relatedDirectionIds: [],
      },
      questions: [],
      changes: [],
      sections: [],
    },
  };
}

it("isolates a late opportunity response after changing the selected index", async () => {
  const user = userEvent.setup();
  let completeOld!: (data: unknown) => void;
  api.opportunity.mockImplementation((query) =>
    query.indexId === "hs300"
      ? new Promise((resolve) => {
          completeOld = resolve;
        })
      : Promise.resolve(radarResponse("A500当前证据")),
  );
  renderWithSession(<FundScreenerPage />);
  await waitFor(() => expect(api.opportunity).toHaveBeenCalled());
  await user.click(await screen.findByRole("button", { name: /中证A500/ }));
  await screen.findByText("A500当前证据的关键影响");
  await act(async () => {
    completeOld(radarResponse("沪深300旧证据"));
  });
  expect(screen.queryByText("沪深300旧证据的关键影响")).not.toBeInTheDocument();
  expect(api.opportunity).toHaveBeenLastCalledWith(
    expect.objectContaining({ indexId: "a500" }),
    expect.anything(),
  );
});

it("retains enabled tracking and unread notices when the server rejects either mutation", async () => {
  const user = userEvent.setup();
  api.monitors.mockResolvedValue([
    {
      id: "monitor-1",
      directionId: "a-share:broad",
      indexId: "hs300",
      enabled: true,
    },
  ]);
  api.opportunity.mockResolvedValue({
    ...radarResponse("当前研究"),
    notices: [
      {
        id: "notice-1",
        title: "新研究条件",
        summary: "证据覆盖变化",
        severity: "INFO",
        createdAt: "2026-10-14T04:00:00Z",
        readAt: null,
      },
    ],
  });
  api.setMonitor.mockRejectedValue(new Error("rejected"));
  api.ack.mockRejectedValue(new Error("rejected"));
  renderWithSession(<FundScreenerPage />);
  await screen.findByText("当前研究的关键影响");
  await user.click(screen.getByRole("button", { name: /市场环境概览/ }));
  await user.click(
    await screen.findByRole("button", { name: "停止跟踪研究条件" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "跟踪条件保存失败",
  );
  expect(
    screen.getByRole("button", { name: "停止跟踪研究条件" }),
  ).toBeEnabled();
  await user.click(
    screen.getByRole("button", { name: "标记新研究条件为已读" }),
  );
  expect(
    await screen.findByText("已读状态保存失败，可重试。"),
  ).toBeInTheDocument();
  expect(screen.getByText("未读")).toBeInTheDocument();
});

it("uses the v3.3 workspace and keeps parent selection when diagnosing a fund", async () => {
  const user = userEvent.setup();
  renderWithSession(<FundScreenerPage />);
  expect(
    await screen.findByRole("heading", { name: "投资机会中心" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("制造业 PMI")).not.toBeInTheDocument();
  await user.click(
    await screen.findByRole("button", { name: /华夏沪深300ETF/ }),
  );
  expect(
    screen.getByRole("button", { name: /沪深300 当前指数/ }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByLabelText("研究路径")).toHaveTextContent(
    "A股 / 宽基 / 沪深300 / 510330",
  );
  expect(screen.getByRole("tab", { name: "综合判断" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "加入自选基金" }));
  await waitFor(() =>
    expect(api.add).toHaveBeenCalledWith(
      expect.objectContaining({
        symbol: "510330",
        market: "SSE",
        assetType: "ETF",
      }),
    ),
  );
});

it("searches across markets and passes index selection to server-side screening", async () => {
  const user = userEvent.setup();
  renderWithSession(<FundScreenerPage />);
  await user.type(
    await screen.findByRole("combobox", { name: "全局搜索" }),
    "510330",
  );
  await user.click(
    await screen.findByRole("option", { name: /华夏沪深300ETF/ }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("研究路径")).toHaveTextContent(
      "港股 / 宽基 / 恒生指数 / 510330",
    ),
  );
  await waitFor(() =>
    expect(api.funds).toHaveBeenLastCalledWith(
      expect.objectContaining({ directionId: "hk:broad", indexId: "hsi" }),
      expect.anything(),
    ),
  );
});

it("keeps unknown conclusions empty and distinguishes index follow from fund watchlist", async () => {
  renderWithSession(<FundScreenerPage />);
  const button = await screen.findByRole("button", { name: "关注指数" });
  expect(button).toBeDisabled();
  const diagnosis = screen.getByLabelText("研究诊断");
  expect(
    within(diagnosis).getByText(
      "关键研究数据尚未齐备，暂不形成配置或买卖结论。",
    ),
  ).toBeInTheDocument();
  expect(api.add).not.toHaveBeenCalled();
});

it("paginates through the server and preserves filters", async () => {
  const user = userEvent.setup();
  renderWithSession(<FundScreenerPage />);
  const next = await screen.findByRole("button", { name: "下一页" });
  await waitFor(() => expect(next).toBeEnabled());
  await user.click(next);
  await waitFor(() =>
    expect(api.funds).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2, indexId: "hs300" }),
      expect.anything(),
    ),
  );
});

it("can still refresh an unmapped fund through the legacy NAV queue", async () => {
  const user = userEvent.setup();
  api.search.mockResolvedValue([
    {
      ...fund,
      instrumentId: null,
      kind: "fund",
      id: "FUND_CN:110022",
      code: "110022",
      name: "场外基金",
      indexId: null,
      directionId: null,
    },
  ]);
  api.refresh.mockResolvedValue({ runId: "nav-run", created: true });
  api.refreshStatus.mockResolvedValue({ status: "COMPLETED" });
  renderWithSession(<FundScreenerPage />);
  await user.type(
    await screen.findByRole("combobox", { name: "全局搜索" }),
    "110022",
  );
  await user.click(await screen.findByRole("option", { name: /场外基金/ }));
  await user.click(screen.getByRole("button", { name: "更新研究数据" }));
  await waitFor(() =>
    expect(api.refresh).toHaveBeenCalledWith("nav", "110022"),
  );
});

it("pins analysis cutoff when paging and resets it when changing direction", async () => {
  const user = userEvent.setup();
  api.funds.mockResolvedValue({
    items: [fund],
    total: 21,
    page: 1,
    pageSize: 20,
    analysisAsOf: "2026-10-12T10:00:00Z",
  });
  renderWithSession(<FundScreenerPage />);
  const next = await screen.findByRole("button", { name: "下一页" });
  await waitFor(() => expect(next).toBeEnabled());
  await user.click(next);
  await waitFor(() =>
    expect(api.funds).toHaveBeenLastCalledWith(
      expect.objectContaining({
        page: 2,
        analysisAsOf: "2026-10-12T10:00:00Z",
      }),
      expect.anything(),
    ),
  );
  await user.click(screen.getByRole("button", { name: "港股" }));
  await waitFor(() =>
    expect(api.funds).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, analysisAsOf: undefined }),
      expect.anything(),
    ),
  );
});
