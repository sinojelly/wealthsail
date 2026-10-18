import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FundScreenerPage from "./FundScreenerPage";
import { renderWithSession } from "../../test/render-with-session";
import * as study from "../../api/study-api";

const mocks = vi.hoisted(() => ({ context: vi.fn() }));
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
    .mockResolvedValue([{ id: "hs300", name: "沪深300", code: "000300" }]),
  getMarketContext: vi.fn().mockResolvedValue({ state: "UNKNOWN", items: [] }),
  getOpportunityContext: mocks.context,
  screenFunds: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 }),
  searchResearch: vi.fn().mockResolvedValue([]),
  getAnalysis: vi
    .fn()
    .mockResolvedValue({ state: "UNKNOWN", indicators: {}, points: [] }),
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
const plan = (code = "006000") => ({
  methodVersion: "actionable-zones-v3",
  subjectKind: "nav",
  subjectId: code,
  fundCode: code,
  label: `医药研究基金${code}`,
  priceBasis: "UNIT_NAV",
  unit: "CNY/份",
  asOf: "2026-10-16",
  sourceUri: `https://fund.eastmoney.com/${code}.html`,
  currentPrice: "2.1108123456789",
  volatilityMethod: "NAV_ABS_CHANGE_STD_V1",
  indicators: { ma20: "2.1098123456", atr14: null, navVolatility14: "0.0266" },
  selectedTrack: "LEFT_VALUE",
  track: "LEFT_VALUE",
  tracks: [
    {
      track: "LEFT_VALUE",
      state: "CANDIDATE",
      entryZoneMin: "2.0723671610204564",
      entryZoneMax: "2.1108",
      stopLossPrice: "1.985123456789",
      takeProfitT1: "2.376123456789",
      takeProfitT2: "2.523123456789",
      priceState: "COMPLETE",
      reasons: [],
      missingEvidence: [],
    },
  ],
});
const direction = {
  key: "healthcare_recovery",
  title: "创新药与医疗修复",
  thesis: "两项产业事实与信用数据支持景气观察。",
  signal: "LEFT_VALUE",
  sourceIds: ["news:1", "news:2"],
  macroCodes: ["PMI"],
  watchFor: ["下一次订单披露"],
  plans: [plan(), plan("006001")],
};
const context = (extra = {}) => ({
  state: "PARTIAL",
  items: [
    {
      code: "PMI",
      label: "制造业PMI",
      value: 50.1,
      unit: "指数点",
      referencePeriod: "2026-09",
      state: "READY",
    },
  ],
  themes: [{ themeCode: "GEO_CRUDE_OIL", assetName: "静态关联基金" }],
  dailyResearch: {
    state: "READY",
    reportRunId: "ready:today",
    report: {
      macroCycle: "信用边际修复，继续观察需求兑现。",
      summary: "综合研判摘要",
      valueDirections: [structuredClone(direction)],
      actionablePlans: structuredClone(direction.plans),
      coverage: [],
      sources: [
        {
          id: "news:1",
          title: "产业订单事实",
          url: "https://example.org/orders",
          verification: "SOURCE_CHECKED",
          quote: "真实短引文",
        },
        {
          id: "news:2",
          title: "创新药审批事实",
          url: "https://example.org/medicine",
          verification: "REPORTED",
          quote: "第二项依据",
        },
      ],
      events: [
        {
          id: "event:1",
          title: "产业关键要闻",
          summary: "实际订单有所增长。",
          importance: "HIGH",
          phase: "REPORTED",
          sourceIds: ["news:1"],
          channels: ["支持收入修复"],
          affectedDirectionIds: [],
          scenarios: [
            { condition: "订单兑现", impact: "收入改善", counter: "利润不足" },
          ],
          watchFor: ["后续财报"],
          unknowns: [],
        },
      ],
      upcoming: [],
      energy: [],
    },
  },
  ...extra,
});
beforeEach(() => {
  vi.clearAllMocks();
  mocks.context.mockResolvedValue(context());
  vi.mocked(study.createStudyTrack).mockResolvedValue({
    track: { id: "saved" },
  } as never);
});

it("opens with macro, discovered directions and plans before compact news without user selection", async () => {
  renderWithSession(<FundScreenerPage />);
  const macro = await screen.findByRole("region", { name: "今日宏观总纲" });
  const directions = await screen.findByRole("region", {
    name: "今日价值方向与买卖计划",
  });
  const news = screen.getByRole("region", { name: "每日事件研究" });
  expect(
    macro.compareDocumentPosition(directions) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    directions.compareDocumentPosition(news) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(macro).toHaveTextContent("50.1");
  expect(within(macro).getByText("2026年9月")).toBeVisible();
  expect(macro).not.toHaveTextContent("9月1日");
  expect(directions).toHaveTextContent("创新药与医疗修复");
  expect(directions).toHaveTextContent("2.0724–2.1108");
  expect(directions).toHaveTextContent("1.9851");
  expect(directions).not.toHaveTextContent("2.0723671610204564");
  expect(
    screen.queryByRole("combobox", { name: "研究主题" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("静态关联基金")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /持续跟踪产业关键要闻/ }),
  ).not.toBeInTheDocument();
  const manual = screen.getByText("手动研究指数与基金").closest("details")!;
  expect(manual).not.toHaveAttribute("open");
  expect(
    screen.getByRole("combobox", { name: "市场与方向" }),
  ).not.toBeVisible();
});

it("freezes each direction and fund directly without prices or confirmation and retains the daily list", async () => {
  renderWithSession(<FundScreenerPage />);
  const card = await screen.findByRole("region", { name: "创新药与医疗修复" });
  const buttons = within(card).getAllByRole("button", {
    name: /跟踪该方向与买卖点/,
  });
  await userEvent.click(buttons[0]);
  await waitFor(() =>
    expect(study.createStudyTrack).toHaveBeenCalledWith({
      originType: "DAILY_DIRECTION",
      reportRunId: "ready:today",
      valueDirectionKey: "healthcare_recovery",
      subjectKind: "nav",
      subjectId: "006000",
      planTrack: "LEFT_VALUE",
      horizonDays: 30,
    }),
  );
  expect(
    within(card).getByRole("button", { name: /已跟踪.*006000/ }),
  ).toBeDisabled();
  expect(screen.getByRole("tab", { name: "今日重点" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    screen.queryByRole("region", { name: "确认研究假设" }),
  ).not.toBeInTheDocument();
  await userEvent.click(buttons[1]);
  await waitFor(() => expect(study.createStudyTrack).toHaveBeenCalledTimes(2));
  expect(vi.mocked(study.createStudyTrack).mock.calls[1][0]).toEqual(
    expect.objectContaining({ subjectId: "006001" }),
  );
});

it("saves a scan-only fund with the exact frozen report identity and no client prices", async () => {
  const data = context();
  Object.assign(data.dailyResearch.report, {
    quantitativeScan: {
      methodVersion: "quantitative-scan-v1",
      asOf: "2026-10-19T03:12:00+08:00",
      minimumHistorySamples: 220,
      coverage: { pricedFundCount: 1, targetHistoryFundCount: 1 },
      items: [
        {
          ...plan("007466"),
          signals: [
            {
              track: "LEFT_VALUE",
              triggerState: "CANDIDATE",
              inEntryZone: true,
              distanceToEntryPct: 0,
            },
          ],
        },
      ],
    },
  });
  mocks.context.mockResolvedValue(data);
  renderWithSession(<FundScreenerPage />);
  await userEvent.click(await screen.findByText(/查看全市场 1 只/));
  await userEvent.click(screen.getByRole("button", { name: "+ 跟踪 007466" }));
  expect(study.createStudyTrack).toHaveBeenCalledExactlyOnceWith({
    originType: "DAILY_QUANT_SCAN",
    reportRunId: "ready:today",
    subjectKind: "nav",
    subjectId: "007466",
    planTrack: "LEFT_VALUE",
    horizonDays: 30,
  });
  expect(screen.getByRole("button", { name: "已跟踪 007466" })).toBeDisabled();
});

it("keeps a failed direct save retryable without borrowing another fund's status", async () => {
  vi.mocked(study.createStudyTrack).mockRejectedValueOnce(
    new Error("保存失败，请重试"),
  );
  renderWithSession(<FundScreenerPage />);
  const card = await screen.findByRole("region", { name: "创新药与医疗修复" });
  await userEvent.click(
    within(card).getAllByRole("button", { name: /跟踪该方向与买卖点/ })[0],
  );
  expect(await within(card).findByRole("alert")).toHaveTextContent(
    "保存失败，请重试",
  );
  expect(
    within(card).queryByRole("button", { name: /已跟踪/ }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    within(card).getAllByRole("button", { name: /跟踪该方向与买卖点/ })[0],
  );
  expect(
    await within(card).findByRole("button", { name: /已跟踪.*006000/ }),
  ).toBeDisabled();
});

it("shows a direction with missing prices honestly and keeps legacy plans outside invented directions", async () => {
  const data = context();
  data.dailyResearch.report.valueDirections = [{ ...direction, plans: [] }];
  data.dailyResearch.report.actionablePlans = [];
  mocks.context.mockResolvedValue(data);
  renderWithSession(<FundScreenerPage />);
  const card = await screen.findByRole("region", { name: "创新药与医疗修复" });
  expect(card).toHaveTextContent("等待真实价格依据");
  expect(
    within(card).queryByRole("button", { name: /跟踪该方向与买卖点/ }),
  ).not.toBeInTheDocument();
});

it("keeps a clear fund or index deep link open in the manual workbench", async () => {
  renderWithSession(<FundScreenerPage />, "/fund-screener?index=hs300");
  expect(
    (await screen.findByText("手动研究指数与基金")).closest("details"),
  ).toHaveAttribute("open");
  expect(screen.getByRole("combobox", { name: "研究指数" })).toBeVisible();
});

it("keeps dynamic price revisions readable on the compact plan before algorithm details", async () => {
  const data = context();
  Object.assign(data.dailyResearch.report.valueDirections[0].plans[0], {
    priceRevisionStatus: "DOWNGRADED",
    priceRevisionNote: "订单兑现减弱，关注区间下调。",
  });
  mocks.context.mockResolvedValue(data);
  renderWithSession(<FundScreenerPage />);
  const card = await screen.findByRole("article", {
    name: "医药研究基金006000买卖计划",
  });
  expect(within(card).getByText(/计划调整：下修关注区间/)).toBeVisible();
  expect(
    within(card).getByRole("status", { name: "价格计划调整" }),
  ).toHaveTextContent("订单兑现减弱，关注区间下调。");
  expect(
    within(card).getByText("查看技术指标与算法依据").closest("details"),
  ).not.toHaveAttribute("open");
});

it("does not turn an empty or conflicting macro observation into a current zero", async () => {
  mocks.context.mockResolvedValue(
    context({
      items: [{ code: "PMI", label: "制造业PMI", value: "", state: "READY" }],
      alphaPublic: {
        observations: [
          {
            code: "CN_T10",
            value: "1.8",
            referenceDate: "2026-10-16",
            state: "CONFLICT",
          },
        ],
      },
    }),
  );
  renderWithSession(<FundScreenerPage />);
  await screen.findByRole("region", { name: "创新药与医疗修复" });
  const macro = screen.getByRole("region", { name: "今日宏观总纲" });
  expect(macro).not.toHaveTextContent("0.0");
  expect(macro).not.toHaveTextContent("1.80");
});

it("keeps a historical macro conclusion and directions dated instead of presenting them as today's research", async () => {
  const data = context();
  data.dailyResearch.state = "STALE";
  Object.assign(data.dailyResearch.report, {
    asOf: "2026-10-16T09:00:00+08:00",
  });
  mocks.context.mockResolvedValue(data);
  renderWithSession(<FundScreenerPage />);
  await screen.findByRole("region", { name: "创新药与医疗修复" });
  const macro = screen.getByRole("region", { name: "今日宏观总纲" });
  expect(macro).toHaveTextContent("历史宏观研判");
  expect(macro).toHaveTextContent("2026年10月16日 09:00");
  expect(
    screen.getByRole("heading", { name: "历史价值方向与买卖计划" }),
  ).toBeVisible();
});

it("keeps compact frozen macro references folded, formats zero and negatives, and opens the shared market evidence", async () => {
  const data = context();
  Object.assign(data.dailyResearch.report.valueDirections[0], {
    macroEvidence: [
      {
        code: "PMI",
        value: 0,
        unit: "index_point",
        referencePeriod: "2026-09",
        sourceUri: "https://example.org/pmi",
        retrievedAt: "2026-10-18T08:31:00+08:00",
      },
      {
        code: "M1_M2_GAP",
        value: "-4.123456789",
        unit: "percentage_points",
        referenceDate: "2026-09-30",
        sourceUri: "https://example.org/money",
      },
      { code: "CN_T10", value: null, unit: "%", asOf: "2026-10-16" },
      { code: "OTHER_UNKNOWN", value: "", unit: "%" },
    ],
  });
  const original = JSON.stringify(data);
  mocks.context.mockResolvedValue(data);
  renderWithSession(<FundScreenerPage />);
  const card = await screen.findByRole("region", { name: "创新药与医疗修复" });
  const facts = within(card).getByText(/宏观参考：/);
  expect(facts).not.toBeVisible();
  await userEvent.click(within(card).getByText("综合依据与后续观察"));
  expect(facts).toBeVisible();
  expect(facts).toHaveTextContent("制造业PMI 0 指数点（2026年9月）");
  expect(facts).toHaveTextContent("M1与M2剪刀差 -4.12 个百分点");
  expect(facts).toHaveTextContent("中国10年国债收益率 未知");
  expect(facts).toHaveTextContent("其他宏观观察 未知");
  expect(facts).not.toHaveTextContent(
    /M1_M2_GAP|CN_T10|OTHER_UNKNOWN|percentage_points|index_point|4\.123456789/,
  );
  expect(card.querySelector('[aria-label="该方向宏观依据"]')).toBeNull();
  await userEvent.click(
    within(card).getByRole("button", { name: "查看市场证据" }),
  );
  expect(screen.getByRole("tab", { name: "市场证据" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(JSON.stringify(data)).toBe(original);
});

it("labels the displayed debug report and keeps secondary evidence collapsed", async () => {
  const data = context();
  Object.assign(data.dailyResearch, { reportRunMode: "DEBUG" });
  Object.assign(data.dailyResearch.report.valueDirections[0].plans[0], {
    calibrationRuleKeys: [],
    calibrationNote: "strategyMemoryRules为空，无真实规则key",
  });
  mocks.context.mockResolvedValue(data);
  renderWithSession(<FundScreenerPage />);
  expect(await screen.findByText(/当前报告：调试版/)).toBeVisible();
  for (const label of [
    "阅读完整日报",
    "执行与报告状态",
    "查看技术指标与算法依据",
    "综合依据与后续观察",
  ]) {
    for (const summary of screen.getAllByText(label))
      expect(summary.closest("details")).not.toHaveAttribute("open");
  }
  expect(document.body).not.toHaveTextContent("strategyMemoryRules为空");
});
