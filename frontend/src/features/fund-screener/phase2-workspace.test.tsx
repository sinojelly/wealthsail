import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FundScreenerPage from "./FundScreenerPage";
import { renderWithSession } from "../../test/render-with-session";
import * as study from "../../api/study-api";

const mocks = vi.hoisted(() => ({ context: vi.fn(), analysis: vi.fn() }));
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
  screenFunds: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 }),
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
  getStudyDetail: vi.fn(),
  createStudyTrack: vi.fn(),
}));

const plan = {
  methodVersion: "actionable-zones-v2",
  subjectKind: "index",
  subjectId: "hs300",
  label: "沪深300",
  priceBasis: "INDEX_POINTS",
  unit: "points",
  asOf: "2026-10-18",
  sourceUri: "https://www.csindex.com.cn/",
  currentPrice: "4000.125",
  indicators: { ma20: 3990 },
  tracks: [
    {
      track: "LEFT_VALUE",
      state: "CANDIDATE",
      entryZoneMin: "3900",
      entryZoneMax: "4000",
      stopLossPrice: null,
      takeProfitT1: null,
      takeProfitT2: null,
      priceState: "PARTIAL",
      reasons: [],
    },
  ],
  themeCode: "GEO_CRUDE_OIL",
  track: "LEFT_VALUE",
  selectedTrack: "LEFT_VALUE",
  priceRevisionStatus: "TAKE_PROFIT_ALERT",
  priceRevisionNote: "当前价已接近上次目标，重新核查原假设。",
  calibrationRuleKeys: ["GEO_CRUDE_OIL:TIMING"],
  calibrationNote: "参考3项独立复盘，判断支持率不代表盈利胜率。",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.context.mockResolvedValue({ state: "PARTIAL", items: [], themes: [] });
  mocks.analysis.mockResolvedValue({
    state: "NOT_COLLECTED",
    score: null,
    indicators: {},
    points: [],
    reasons: [],
  });
  vi.mocked(study.createStudyTrack).mockRejectedValue(
    new Error("保留确认以核对请求"),
  );
});

it("does not expose static themes or forward a retired theme filter from old URLs", async () => {
  mocks.context.mockResolvedValue({
    state: "PARTIAL",
    items: [],
    themes: [{ themeCode: "GEO_CRUDE_OIL", assetName: "原油研究基金" }],
  });
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&theme=GEO_CRUDE_OIL",
  );
  await waitFor(() => expect(mocks.context).toHaveBeenCalled());
  expect(
    screen.queryByRole("combobox", { name: "研究主题" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("原油研究基金")).not.toBeInTheDocument();
  expect(mocks.context).toHaveBeenLastCalledWith(
    expect.not.objectContaining({ themeCode: expect.anything() }),
    expect.anything(),
  );
});

it("shows the structured daily plan revision and bounded calibration separately from macro opinion", async () => {
  mocks.context.mockResolvedValue({
    state: "PARTIAL",
    items: [],
    dailyResearch: {
      state: "READY",
      report: {
        summary: "市场观察",
        coverage: [],
        sources: [],
        events: [],
        upcoming: [],
        energy: [],
        macroCycle: "信用修复仍需后续事实确认。",
        actionablePlans: [plan],
      },
    },
  });
  renderWithSession(<FundScreenerPage />);
  const today = await screen.findByRole("tabpanel");
  expect(
    await within(today).findByText("信用修复仍需后续事实确认。"),
  ).toBeVisible();
  const card = await within(today).findByRole("region", {
    name: "沪深300双轨计划",
  });
  expect(card).toHaveTextContent("止盈复核提示");
  expect(card).toHaveTextContent(plan.priceRevisionNote);
  expect(card).toHaveTextContent(plan.calibrationNote);
  expect(card).toHaveTextContent("GEO_CRUDE_OIL:TIMING");
  expect(card).toHaveTextContent("研究轨道：左侧低吸");
  expect(card).not.toHaveTextContent("冻结轨道");
});

it("shows new public macro and four-city housing gaps without creating values or charts", async () => {
  mocks.context.mockResolvedValue({
    state: "PARTIAL",
    items: [],
    alphaPublic: {
      asOf: "2026-10-18T09:00:00+08:00",
      observations: [
        {
          code: "CN_T10",
          referenceDate: "2026-10-16",
          value: "1.8",
          unit: "%",
          sourceUri: "https://data.eastmoney.com/cjsj/zmgzsyl.html",
          retrievedAt: "2026-10-18T08:00:00+08:00",
        },
        {
          code: "M1_M2_GAP",
          referenceDate: "2026-09-30",
          value: "-5.0",
          unit: "percentage_points",
          sourceUri: "https://data.eastmoney.com/cjsj/hbgyl.html",
        },
      ],
      catalysts: [],
      odds: [],
      launches: [],
      sourceResults: {
        AKSHARE_HOUSE_MAINLAND: { status: "UNAVAILABLE", records: 0 },
      },
    },
  });
  renderWithSession(<FundScreenerPage />, "/fund-screener?researchTab=macro");
  const panel = await screen.findByRole("tabpanel");
  expect(await within(panel).findByText("中国10年国债收益率")).toBeVisible();
  expect(panel).toHaveTextContent("1.8 %");
  expect(panel).toHaveTextContent("M1与M2剪刀差");
  expect(panel).toHaveTextContent("-5 个百分点");
  for (const city of ["海口", "三亚", "上海", "深圳"]) {
    expect(panel).toHaveTextContent(`${city}新房环比`);
    expect(panel).toHaveTextContent(`${city}二手房环比`);
  }
  expect(panel).toHaveTextContent("Shibor一周");
  expect(panel).toHaveTextContent("暂无有效观察");
  expect(panel.querySelector("svg.recharts-surface")).not.toBeInTheDocument();
});

it("keeps media, implied odds and planned launches as bounded source-labelled observations", async () => {
  mocks.context.mockResolvedValue({
    state: "PARTIAL",
    items: [],
    alphaPublic: {
      observations: [],
      sourceResults: {},
      asOf: "2026-10-18T09:00:00+08:00",
      catalysts: [
        {
          id: "cls:1",
          title: "相关媒体线索",
          content: "事件实施仍待核实。",
          publishedAt: "2026-10-18T08:00:00+08:00",
          sourceUri: "https://www.cls.cn/detail/1",
        },
      ],
      odds: [
        {
          marketId: "m1",
          question: "事件是否发生？",
          outcome: "是",
          impliedProbabilityPct: "0",
          sourceUri: "https://gamma-api.polymarket.com/markets/m1",
          observedAt: "2026-10-18T08:00:00+08:00",
        },
      ],
      launches: [
        {
          id: "l1",
          name: "研究发射计划",
          phase: "PLANNED",
          scheduledAt: "2026-11-01T08:00:00+08:00",
          sourceUri: "https://ll.thespacedevs.com/2.3.0/launches/l1/",
        },
      ],
    },
  });
  renderWithSession(<FundScreenerPage />, "/fund-screener?researchTab=macro");
  await userEvent.click(
    await screen.findByText("媒体线索、隐含赔率与发射计划"),
  );
  const evidence = screen.getByRole("region", { name: "公共事件与计划线索" });
  expect(evidence).toHaveTextContent("媒体报道，事实待核实");
  expect(evidence).toHaveTextContent("0 %");
  expect(evidence).toHaveTextContent("市场隐含赔率，不是统计真实概率");
  expect(evidence).toHaveTextContent("计划中，尚未发射");
  expect(
    within(evidence).getByRole("link", { name: "相关媒体线索来源" }),
  ).toHaveAttribute("href", "https://www.cls.cn/detail/1");
});
