import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useNavigate } from "react-router-dom";
import FundScreenerPage from "./FundScreenerPage";
import { renderWithSession } from "../../test/render-with-session";
import * as study from "../../api/study-api";
import TrackedFundWatch from "./components/TrackedFundWatch";

vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  getStudyDetail: vi.fn(),
  createStudyTrack: vi.fn(),
  updateStudyVersion: vi.fn(),
  setStudyStatus: vi.fn(),
  checkStudy: vi.fn(),
  addStudyNote: vi.fn(),
  getEligibleStudyTrades: vi.fn(),
  linkStudyTrade: vi.fn(),
}));

it("queries and preserves the selected macro history window", async () => {
  const user = userEvent.setup();
  renderWithSession(<FundScreenerPage />);
  await user.click(await screen.findByRole("tab", { name: "市场证据" }));
  await user.selectOptions(screen.getByLabelText("宏观观察窗口"), "60");
  await waitFor(() =>
    expect(api.opportunity).toHaveBeenLastCalledWith(
      expect.objectContaining({ months: 60 }),
      expect.anything(),
    ),
  );
  expect(api.context).not.toHaveBeenCalled();
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
  vi.mocked(study.getStudyTracks).mockResolvedValue({ items: [], total: 0 });
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
  expect(screen.getByRole("combobox", { name: "研究指数" })).toHaveValue(
    "hs300",
  );
  expect(screen.getByLabelText("研究路径")).toHaveTextContent(
    "A股 / 宽基 / 沪深300 / 510330",
  );
  expect(
    screen.getByRole("complementary", { name: "当前标的研究" }),
  ).toBeInTheDocument();
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
  const diagnosis = screen.getByLabelText("当前标的研究");
  expect(
    within(diagnosis).getByText(
      "当前标的尚无可用双轨价格依据，仍可保存研究并跟踪。",
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
  await user.selectOptions(
    screen.getByRole("combobox", { name: "市场与方向" }),
    "hk:broad",
  );
  await waitFor(() =>
    expect(api.funds).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, analysisAsOf: undefined }),
      expect.anything(),
    ),
  );
});

function twoFundCandidates(
  second = {
    ...fund,
    instrumentId: "fund-b",
    fundCode: "510500",
    fundName: "待研究基金B",
  },
) {
  api.funds.mockResolvedValue({
    items: [fund, second],
    total: 2,
    page: 1,
    pageSize: 20,
  });
}

function WatchHistoryNavigation() {
  const navigate = useNavigate();
  return (
    <button
      onClick={() =>
        navigate(
          "/fund-screener?direction=a-share:broad&index=hs300&fund=510500&instrument=fund-b&fundMarket=SSE",
        )
      }
    >
      直接导航基金B
    </button>
  );
}

it("scopes successful watch state when browser navigation bypasses selection handlers", async () => {
  const user = userEvent.setup();
  twoFundCandidates();
  renderWithSession(
    <>
      <FundScreenerPage />
      <WatchHistoryNavigation />
    </>,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-id&fundMarket=SSE",
  );
  await user.click(await screen.findByRole("button", { name: "加入自选基金" }));
  await screen.findByText("已加入自选");
  await user.click(screen.getByRole("button", { name: "直接导航基金B" }));
  expect(screen.getByRole("button", { name: "加入自选基金" })).toBeEnabled();
  expect(screen.queryByText("已加入自选")).not.toBeInTheDocument();
});

it("does not carry a successful watch action into a different fund", async () => {
  const user = userEvent.setup();
  twoFundCandidates();
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-id&fundMarket=SSE",
  );
  await user.click(await screen.findByRole("button", { name: "加入自选基金" }));
  await screen.findByText("已加入自选");
  await user.click(screen.getByRole("button", { name: /待研究基金B/ }));
  expect(screen.getByRole("button", { name: "加入自选基金" })).toBeEnabled();
  expect(screen.queryByText("已加入自选")).not.toBeInTheDocument();
});

it("does not mistake an ETF watch code for an OTC fund with the same code", async () => {
  const user = userEvent.setup();
  twoFundCandidates({
    ...fund,
    instrumentId: "otc-same-code",
    market: "FUND_CN",
    tradeForm: "FUND",
    fundName: "同码场外基金",
  });
  api.list.mockResolvedValue([
    {
      symbol: "510330",
      assetType: "ETF",
      assetName: fund.fundName,
      addedAt: "2026-10-15T01:00:00Z",
    },
  ]);
  renderWithSession(<FundScreenerPage />);
  await user.click(await screen.findByRole("button", { name: /同码场外基金/ }));
  expect(screen.getByRole("button", { name: "加入自选基金" })).toBeEnabled();
  expect(screen.queryByText("已加入自选")).not.toBeInTheDocument();
});

it("describes a legacy code-only watch without inventing a verified market identity", async () => {
  api.list.mockResolvedValue([
    {
      symbol: "510330",
      assetType: "ETF",
      assetName: fund.fundName,
      addedAt: "2026-10-15T01:00:00Z",
    },
  ]);
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-id&fundMarket=SSE",
  );
  expect(
    await screen.findByText(/代码已有自选.*市场.*核验/),
  ).toBeInTheDocument();
  expect(screen.queryByText("已加入自选")).not.toBeInTheDocument();
});

it("keeps a pending and late successful watch scoped to its original fund", async () => {
  let complete!: (value: unknown) => void;
  api.add.mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const user = userEvent.setup();
  twoFundCandidates();
  renderWithSession(
    <>
      <FundScreenerPage />
      <WatchHistoryNavigation />
    </>,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-id&fundMarket=SSE",
  );
  await user.click(await screen.findByRole("button", { name: "加入自选基金" }));
  await waitFor(() => expect(api.add).toHaveBeenCalled());
  await user.click(screen.getByRole("button", { name: "直接导航基金B" }));
  expect(screen.getByRole("button", { name: "加入自选基金" })).toBeEnabled();
  await act(async () => complete({ symbol: "510330", assetType: "ETF" }));
  expect(screen.getByRole("button", { name: "加入自选基金" })).toBeEnabled();
  expect(screen.queryByText("已加入自选")).not.toBeInTheDocument();
});

it("does not display a late watch failure for the newly selected fund", async () => {
  let fail!: (reason: Error) => void;
  api.add.mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        fail = reject;
      }),
  );
  const user = userEvent.setup();
  twoFundCandidates();
  renderWithSession(
    <>
      <FundScreenerPage />
      <WatchHistoryNavigation />
    </>,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-id&fundMarket=SSE",
  );
  await user.click(await screen.findByRole("button", { name: "加入自选基金" }));
  await waitFor(() => expect(api.add).toHaveBeenCalled());
  await user.click(screen.getByRole("button", { name: "直接导航基金B" }));
  await act(async () => fail(new Error("watch-A-rejected")));
  expect(screen.queryByText("加入自选失败，请重试。")).not.toBeInTheDocument();
});

it("shows actual unit-NAV changes separately from unknown reinvested fund returns", async () => {
  api.analysis.mockResolvedValue({
    state: "READY",
    indicators: {},
    points: [],
    reasons: [],
    navChanges: {
      basis: "UNIT_NAV_CHANGE",
      unit: "decimal_fraction",
      change63: 0,
      change252: -0.1234,
      sampleCount: 253,
      missingWindows: [],
      reasons: [],
    },
    performance: {
      return63: null,
      return252: null,
      maxDrawdown: null,
      sampleCount: 253,
    },
  });
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?fund=000051&fundMarket=FUND_CN",
  );
  await userEvent.click(await screen.findByText("历史收益与风险"));
  expect(
    await screen.findByText(/63期单位净值变动：0%.*252期单位净值变动：-12.34%/),
  ).toBeVisible();
  expect(screen.getByText(/单位净值变动不含分红再投/)).toBeVisible();
  expect(screen.getByText(/63观察日收益：—%/)).toBeVisible();
});

function trackedFund(
  id: string,
  overrides: Partial<study.StudyTrack> = {},
): study.StudyTrack {
  return {
    id,
    title: `${id} 冻结研究`,
    enabled: true,
    generation: 1,
    status: "OBSERVING",
    createdAt: "2026-10-20",
    updatedAt: "2026-10-27",
    unreadCount: 0,
    scope: { fundCode: "012733", fundMarket: "FUND_CN" },
    priceWatch: {
      frozenPlan: {
        methodVersion: "actionable-zones-v3",
        subjectKind: "nav",
        subjectId: "012733",
        fundCode: "012733",
        label: "人工智能联接基金",
        priceBasis: "UNIT_NAV",
        unit: "CNY/份",
        asOf: "2026-10-20",
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
      confirmedNav: { value: "1.0100", asOf: "2026-10-26", usable: true },
      estimate1430: null,
      holding: { state: "NONE" },
      state: "ENTRY_ZONE",
      basis: "CONFIRMED_NAV",
      asOf: "2026-10-26",
      reason: "确认净值进入冻结买入区间",
    },
    ...overrides,
  };
}

it("keeps valid personal reminders in Today without a report and opens the independent fund workspace and review", async () => {
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [trackedFund("ai")],
    total: 1,
  });
  vi.mocked(study.getStudyDetail).mockRejectedValue(new Error("详情暂不可用"));
  renderWithSession(<FundScreenerPage />, "/fund-screener?macroMonths=60");
  const reminders = await screen.findByRole("region", { name: "我的行动提醒" });
  expect(await within(reminders).findByText(/已入买入击球区/)).toBeVisible();
  expect(
    screen.queryByRole("region", { name: "我的基金" }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    within(reminders).getByRole("button", { name: "查看我的基金" }),
  );
  expect(screen.getByRole("tab", { name: "我的基金" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const funds = await screen.findByRole("region", { name: "我的基金" });
  expect(await within(funds).findByText("原冻结买入区间：")).toBeVisible();
  expect(funds).toHaveTextContent("0.9800–1.0200");
  expect(funds).toHaveTextContent("1.0100");
  await userEvent.click(
    within(funds).getByRole("button", { name: "研究与复盘：ai 冻结研究" }),
  );
  await waitFor(() =>
    expect(study.getStudyDetail).toHaveBeenCalledWith(
      "ai",
      expect.anything(),
      0,
    ),
  );
  await userEvent.click(screen.getByRole("tab", { name: "今日重点" }));
  expect(
    await screen.findByRole("region", { name: "我的行动提醒" }),
  ).toHaveTextContent("ai 冻结研究");
});

it("groups repeated fund identities while keeping each active frozen plan and review state", async () => {
  const waiting = trackedFund("right", {
    status: "INVALIDATED",
    unreadCount: 2,
  });
  waiting.priceWatch!.frozenPlan!.tracks[0].state = "WATCH";
  waiting.priceWatch!.state = "OBSERVING";
  const tracks = [
    trackedFund("left"),
    waiting,
    trackedFund("stopped", { enabled: false, status: "ENDED" }),
    trackedFund("ended", { status: "ENDED" }),
    trackedFund("index", { scope: { indexId: "hs300" }, priceWatch: null }),
    trackedFund("missing", {
      scope: { instrumentId: "unpriced" },
      priceWatch: null,
    }),
  ];
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: tracks,
    total: tracks.length,
  });
  renderWithSession(<TrackedFundWatch actor="legacy-tracking-test" />);
  const region = await screen.findByRole("region", {
    name: "我的持续跟踪基金",
  });
  await within(region).findByRole("button", {
    name: "查看 left 冻结研究 与复盘",
  });
  expect(
    within(within(region).getByRole("table")).getAllByRole("row"),
  ).toHaveLength(3);
  expect(
    within(region).getByRole("button", { name: "查看 right 冻结研究 与复盘" }),
  ).toBeVisible();
  expect(region).toHaveTextContent("预设失效条件触发");
  expect(region).toHaveTextContent("2项未读变化");
  expect(region).toHaveTextContent("区间观察中");
  expect(region).toHaveTextContent("行情与冻结价位待核对");
  expect(
    within(region).queryByRole("button", { name: /stopped|ended|index/ }),
  ).not.toBeInTheDocument();
});

it("lets the legacy personal tracking table retry a failed list", async () => {
  vi.mocked(study.getStudyTracks)
    .mockRejectedValueOnce(new Error("暂不可用"))
    .mockResolvedValue({ items: [trackedFund("retry")], total: 1 });
  renderWithSession(<TrackedFundWatch actor="legacy-tracking-test" />);
  const region = await screen.findByRole("region", {
    name: "我的持续跟踪基金",
  });
  await userEvent.click(
    await within(region).findByRole("button", { name: "重试持续跟踪基金" }),
  );
  expect(
    await within(region).findByRole("button", {
      name: "查看 retry 冻结研究 与复盘",
    }),
  ).toBeVisible();
  expect(region).toBeVisible();
});

it("keeps the expiry day active until Shanghai midnight and hides expired fund studies", async () => {
  const clock = vi
    .spyOn(Date, "now")
    .mockReturnValue(Date.parse("2026-10-27T16:01:00Z"));
  try {
    const tracks = [
      trackedFund("expired", { expiresOn: "2026-10-27" }),
      trackedFund("today", { expiresOn: "2026-10-28" }),
      trackedFund("legacy", { expiresOn: null }),
    ];
    vi.mocked(study.getStudyTracks).mockResolvedValue({
      items: tracks,
      total: 3,
    });
    renderWithSession(<TrackedFundWatch actor="legacy-tracking-test" />);
    const region = await screen.findByRole("region", {
      name: "我的持续跟踪基金",
    });
    expect(
      await within(region).findByRole("button", {
        name: "查看 today 冻结研究 与复盘",
      }),
    ).toBeVisible();
    expect(
      within(region).getByRole("button", {
        name: "查看 legacy 冻结研究 与复盘",
      }),
    ).toBeVisible();
    expect(
      within(region).queryByRole("button", {
        name: "查看 expired 冻结研究 与复盘",
      }),
    ).not.toBeInTheDocument();
  } finally {
    clock.mockRestore();
  }
});

it("refreshes cached tracking independently and keeps unavailable current prices explicit", async () => {
  const active = trackedFund("refresh");
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [active],
    total: 1,
  });
  renderWithSession(<TrackedFundWatch actor="legacy-tracking-test" />);
  const region = await screen.findByRole("region", {
    name: "我的持续跟踪基金",
  });
  await within(region).findByText("🎯已入买入击球区");
  const refresh = await within(region).findByRole("button", {
    name: "刷新跟踪行情",
  });
  await waitFor(() => expect(refresh).toBeEnabled());
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [
      {
        ...active,
        priceWatch: {
          ...active.priceWatch!,
          state: "UNKNOWN",
          basis: null,
          confirmedNav: { value: "1.0100", asOf: "2026-10-20", usable: false },
          reason: "确认净值已过期，等待同口径当前行情",
        },
      },
    ],
    total: 1,
  });
  await userEvent.click(refresh);
  expect(
    await within(region).findByText("行情待更新", { selector: "strong" }),
  ).toBeVisible();
  expect(region).toHaveTextContent("仅保留历史记录，不用于当前触发。");
  expect(
    within(region).queryByText("🎯已入买入击球区"),
  ).not.toBeInTheDocument();
});

it("removes an expired fund at Shanghai midnight while the page stays open", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-27T15:59:59Z"));
  try {
    const tracks = [trackedFund("midnight", { expiresOn: "2026-10-27" })];
    vi.mocked(study.getStudyTracks).mockResolvedValue({
      items: tracks,
      total: 1,
    });
    renderWithSession(<TrackedFundWatch actor="midnight-test" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(
      screen.getByRole("button", { name: "查看 midnight 冻结研究 与复盘" }),
    ).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(
      screen.queryByRole("button", { name: "查看 midnight 冻结研究 与复盘" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "我的持续跟踪基金" }),
    ).toHaveTextContent("暂无持续跟踪基金");
  } finally {
    vi.useRealTimers();
  }
});
