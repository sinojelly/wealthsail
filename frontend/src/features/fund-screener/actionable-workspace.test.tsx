import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FundScreenerPage from "./FundScreenerPage";
import { renderWithSession } from "../../test/render-with-session";
import * as study from "../../api/study-api";

const mocks = vi.hoisted(() => ({
  context: vi.fn(),
  analysis: vi.fn(),
  funds: vi.fn(),
}));
vi.mock("../../api/investment", () => ({
  getDirections: vi.fn().mockResolvedValue([
    {
      id: "a-share:broad",
      label: "宽基",
      marketLabel: "A股",
      marketId: "a-share",
    },
  ]),
  getIndices: vi
    .fn()
    .mockResolvedValue([{ id: "hs300", code: "000300", name: "沪深300" }]),
  getMarketContext: vi.fn().mockResolvedValue({ state: "UNKNOWN", items: [] }),
  getOpportunityContext: mocks.context,
  getConditionMonitors: vi.fn().mockResolvedValue([]),
  screenFunds: mocks.funds,
  searchResearch: vi.fn().mockResolvedValue([]),
  getAnalysis: mocks.analysis,
  refreshResearch: vi.fn(),
  getRefreshStatus: vi.fn(),
}));
vi.mock("../../api/watchlist", () => ({
  list: vi.fn().mockResolvedValue([]),
  add: vi.fn(),
}));
vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  getStudyDetail: vi.fn().mockRejectedValue(new Error("详情不可用")),
  createStudyTrack: vi.fn(),
}));

const zones = {
  methodVersion: "actionable-zones-v1",
  subjectKind: "index",
  subjectId: "hs300",
  label: "沪深300",
  priceBasis: "INDEX_PRICE",
  unit: "指数点",
  asOf: "2026-10-16",
  sourceUri: "https://example.com/index",
  currentPrice: 3850,
  indicators: { ma20: 3800, ma60: 3900, ma200: null, rsi14: 28, atr14: 50 },
  tracks: [
    {
      track: "LEFT_VALUE",
      state: "CANDIDATE",
      entryZoneMin: 3750,
      entryZoneMax: 3800,
      stopLossPrice: 3650,
      takeProfitT1: 3950,
      takeProfitT2: 4100,
      reasons: ["低位观察"],
      priceState: "IN_ZONE",
    },
    {
      track: "RIGHT_TREND",
      state: "WATCH",
      entryZoneMin: 3900,
      entryZoneMax: 3950,
      stopLossPrice: 3800,
      takeProfitT1: 4100,
      takeProfitT2: 4250,
      reasons: ["等待突破"],
      priceState: "BELOW_ZONE",
    },
  ],
};
beforeEach(() => {
  vi.clearAllMocks();
  mocks.funds.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
  mocks.context.mockResolvedValue({
    state: "PARTIAL",
    items: [],
    actionableZones: [zones],
  });
  mocks.analysis.mockResolvedValue({
    state: "INSUFFICIENT",
    score: null,
    indicators: {},
    points: [],
    reasons: [],
    actionableZones: zones,
  });
});

it("ignores the retired all-valuation filter from old URLs when discovering funds", async () => {
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?valueCandidateOnly=true",
  );
  await waitFor(() =>
    expect(mocks.funds).toHaveBeenCalledWith(
      expect.not.objectContaining({ valueCandidateOnly: "true" }),
      expect.anything(),
    ),
  );
  await userEvent.click(screen.getByRole("button", { name: "高级筛选" }));
  expect(screen.queryByLabelText("研究资格")).not.toBeInTheDocument();
});

it.each([true, false])(
  "keeps dual-track candidates visible while independently presenting a confirmed budget breach (%s)",
  async (confirmed) => {
    mocks.context.mockResolvedValue({
      state: "PARTIAL",
      items: [],
      actionableZones: [zones],
      portfolio: {
        state: confirmed ? "READY" : "UNKNOWN",
        heldSelected: true,
        budgetState: "BLOCKED",
        budgetViolation: true,
      },
      decision: {
        personalState: "POSITION_REVIEW",
        assessment: { riskState: "BREACH" },
      },
    });
    renderWithSession(<FundScreenerPage />);
    expect(
      await within(screen.getByRole("tabpanel")).findByText(
        /3750\.00.*3800\.00/,
      ),
    ).toBeInTheDocument();
    if (confirmed)
      expect(screen.getByRole("alert")).toHaveTextContent(
        "已确认持仓风险预算超限",
      );
    else expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("tabpanel")).getByText("候选条件成立"),
    ).toBeInTheDocument();
  },
);

it("shows three categories and independent price coordinates without a legacy board or PB/PIT gate", async () => {
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?researchOpen=1&researchTab=events",
  );
  expect(
    await screen.findByRole("tab", { name: "今日重点" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "市场证据" })).toBeInTheDocument();
  expect(
    screen.getByRole("tab", { name: "我的跟踪与复盘" }),
  ).toBeInTheDocument();
  const today = screen.getByRole("tabpanel");
  expect(
    await within(today).findByText(/3750\.00.*3800\.00/),
  ).toBeInTheDocument();
  expect(within(today).getByText(/3900\.00.*3950\.00/)).toBeInTheDocument();
  expect(within(today).getByText(/指数点.*不可.*挂单/)).toBeInTheDocument();
});

it("confirms a selected track then saves the exact identity and horizon without client supplied prices", async () => {
  mocks.analysis.mockResolvedValue({
    state: "INSUFFICIENT",
    score: null,
    indicators: {},
    points: [],
    reasons: [],
    actionableZones: {
      ...zones,
      subjectKind: "fund",
      subjectId: "fund-1",
      priceBasis: "ETF_HFQ",
      unit: "元",
    },
  });
  vi.mocked(study.createStudyTrack).mockResolvedValue({
    track: { id: "saved" },
  } as never);
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&instrument=fund-1&fund=510330&fundMarket=SSE",
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "跟踪左侧低吸计划" }),
  );
  expect(study.createStudyTrack).not.toHaveBeenCalled();
  expect(
    screen.getByRole("region", { name: "确认研究假设" }),
  ).toHaveTextContent("3750.000");
  await userEvent.clear(screen.getByLabelText("观察天数"));
  await userEvent.type(screen.getByLabelText("观察天数"), "60");
  await userEvent.click(screen.getByRole("button", { name: "确认并保存跟踪" }));
  await waitFor(() =>
    expect(study.createStudyTrack).toHaveBeenCalledWith({
      originType: "SELECTION",
      scope: {
        directionId: "a-share:broad",
        indexId: "hs300",
        instrumentId: "fund-1",
        fundCode: "510330",
        fundMarket: "SSE",
      },
      planTrack: "LEFT_VALUE",
      horizonDays: 60,
    }),
  );
});

it("previews and freezes the fund from an instrument-only deep link while retaining its index context", async () => {
  const instrumentId = "12345678-1234-1234-1234-123456789012";
  mocks.analysis.mockImplementation(async (kind: string) => ({
    state: "READY",
    score: null,
    indicators: {},
    points: [],
    reasons: [],
    actionableZones:
      kind === "fund"
        ? {
            ...zones,
            subjectKind: "fund",
            subjectId: instrumentId,
            label: "深链基金",
            priceBasis: "ETF_HFQ",
            unit: "CNY",
          }
        : zones,
  }));
  vi.mocked(study.createStudyTrack).mockRejectedValue(
    new Error("保留确认以核对请求"),
  );
  renderWithSession(
    <FundScreenerPage />,
    `/fund-screener?instrument=${instrumentId}&index=hs300&fundMarket=SSE`,
  );
  await waitFor(() =>
    expect(mocks.analysis).toHaveBeenLastCalledWith(
      "fund",
      instrumentId,
      expect.anything(),
    ),
  );
  const workbench = screen.getByRole("complementary", { name: "当前标的研究" });
  expect(
    await within(workbench).findByRole("region", { name: "深链基金双轨计划" }),
  ).toBeInTheDocument();
  expect(
    within(workbench).getByRole("heading", { name: instrumentId }),
  ).toBeInTheDocument();
  expect(
    within(workbench).queryByRole("region", { name: "沪深300双轨计划" }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    within(workbench).getByRole("button", { name: "跟踪左侧低吸计划" }),
  );
  expect(
    screen.getByRole("region", { name: "确认研究假设" }),
  ).toHaveTextContent("深链基金");
  await userEvent.click(screen.getByRole("button", { name: "确认并保存跟踪" }));
  await waitFor(() =>
    expect(study.createStudyTrack).toHaveBeenCalledWith({
      originType: "SELECTION",
      planTrack: "LEFT_VALUE",
      horizonDays: 30,
      scope: {
        directionId: "a-share:broad",
        indexId: "hs300",
        instrumentId,
        fundCode: undefined,
        fundMarket: "SSE",
      },
    }),
  );
});

it("keeps missing prices empty and labels NAV coordinates without implying a live tradable quote", async () => {
  const nav = {
    ...zones,
    subjectKind: "nav",
    subjectId: "000001",
    priceBasis: "UNIT_NAV",
    unit: "元",
    currentPrice: null,
    tracks: zones.tracks.map((track) => ({
      ...track,
      state: "INSUFFICIENT",
      entryZoneMin: null,
      entryZoneMax: null,
      stopLossPrice: null,
      takeProfitT1: null,
      takeProfitT2: null,
    })),
  };
  mocks.context.mockResolvedValue({
    state: "PARTIAL",
    items: [],
    actionableZones: [nav],
  });
  mocks.analysis.mockResolvedValue({
    state: "INSUFFICIENT",
    indicators: {},
    points: [],
    reasons: [],
    actionableZones: nav,
  });
  renderWithSession(<FundScreenerPage />, "/fund-screener?researchOpen=1");
  const panel = await screen.findByRole("tabpanel");
  await waitFor(() => expect(panel).toHaveTextContent(/净值.*不可.*挂单/));
  expect(panel).toHaveTextContent("实际观察：—");
  expect(panel).toHaveTextContent("入场区间：—–—");
  expect(
    within(panel).queryByRole("button", { name: "跟踪左侧低吸计划" }),
  ).not.toBeInTheDocument();
});

it("keeps a candidate with partial ATR prices and formats the supplied decimal strings", async () => {
  mocks.context.mockResolvedValue({
    state: "PARTIAL",
    items: [],
    actionableZones: [
      {
        ...zones,
        subjectKind: "nav",
        subjectId: "006000",
        priceBasis: "UNIT_NAV",
        tracks: [
          {
            ...zones.tracks[0],
            entryZoneMin: "1.2345",
            entryZoneMax: "1.2355",
            stopLossPrice: null,
            takeProfitT1: null,
            takeProfitT2: null,
            priceState: "PARTIAL",
            missingEvidence: ["真实ATR14"],
          },
        ],
      },
    ],
  });
  renderWithSession(<FundScreenerPage />);
  const panel = screen.getByRole("tabpanel");
  expect(await within(panel).findByText(/1\.2345–1\.2355/)).toBeInTheDocument();
  expect(panel).toHaveTextContent("候选条件成立");
  expect(panel).toHaveTextContent("部分价格依据");
  expect(panel).toHaveTextContent("止损：—");
  expect(panel).toHaveTextContent("待补充：真实ATR14");
});
