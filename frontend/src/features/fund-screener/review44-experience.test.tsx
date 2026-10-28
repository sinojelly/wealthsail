import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ActionableZoneResult, DailyResearch } from "../../api/investment";
import * as study from "../../api/study-api";
import ValueDirections from "./components/ValueDirections";
import MacroContextStrip from "./components/MacroContextStrip";
import StudyTracking from "./components/StudyTracking";
import QuantitativeScan from "./components/QuantitativeScan";
import PriceWatchSummary from "./components/PriceWatchSummary";
import PlanRevision from "./components/PlanRevision";
import { fundDisplayName } from "./plan-display";

vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn(),
  getStudyDetail: vi.fn(),
  createStudyTrack: vi.fn(),
  updateStudyVersion: vi.fn(),
  setStudyStatus: vi.fn(),
  checkStudy: vi.fn(),
  addStudyNote: vi.fn(),
  getEligibleStudyTrades: vi.fn(),
  linkStudyTrade: vi.fn(),
}));
const plan = (code = "012733", current = "1.0000"): ActionableZoneResult => ({
  methodVersion: "actionable-zones-v3",
  subjectKind: "nav",
  subjectId: code,
  fundCode: code,
  label: `${code} 易方达人工智能ETF联接A`,
  priceBasis: "UNIT_NAV",
  unit: "CNY/份",
  asOf: "2026-10-16",
  sourceUri: "https://fund.eastmoney.com/012733.html",
  currentPrice: current,
  indicators: { rsi14: "38.25" },
  selectedTrack: "LEFT_VALUE",
  priceRevisionStatus:
    "MAINTAINED" as ActionableZoneResult["priceRevisionStatus"],
  priceRevisionNote:
    "未出现新的基本面变化，".repeat(20) + "因此维持原有研究区间。",
  tracks: [
    {
      track: "LEFT_VALUE",
      state: "WATCH",
      priceState: "READY",
      entryZoneMin: "0.9800",
      entryZoneMax: "1.0200",
      stopLossPrice: "0.9000",
      takeProfitT1: "1.1200",
      takeProfitT2: "1.2000",
      reasons: [],
    },
    {
      track: "RIGHT_TREND",
      state: "WATCH",
      priceState: "READY",
      entryZoneMin: "1.1000",
      entryZoneMax: "1.2000",
      stopLossPrice: "1.0000",
      takeProfitT1: "1.3000",
      takeProfitT2: "1.4000",
      reasons: [],
    },
  ],
});
const daily = (): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "READY",
  runId: "run",
  reportRunId: "frozen-report",
  scheduledFor: null,
  startedAt: null,
  completedAt: null,
  asOf: "2026-10-19T03:12:00+08:00",
  nextRunAt: null,
  report: {
    summary: "今日研究",
    coverage: [],
    sources: [],
    events: [],
    upcoming: [],
    energy: [],
    valueDirections: [
      {
        key: "ai",
        title: "人工智能",
        thesis: "产业研究",
        signal: "LEFT_VALUE",
        sourceIds: [],
        macroCodes: [],
        watchFor: [],
        plans: [plan()],
      },
    ],
  },
});

it("keeps a maintained reason complete and names clean, with another track rendered from frozen prices", async () => {
  const data = daily();
  const before = JSON.stringify(data);
  render(<ValueDirections data={data} />);
  const card = screen.getByRole("article", { name: /买卖计划/ });
  expect(within(card).getByRole("heading")).toHaveTextContent(
    /^易方达人工智能ETF联接A 012733$/,
  );
  expect(within(card).getByLabelText("价格计划调整")).toHaveTextContent(
    "维持关注区间",
  );
  const reason = within(card).getByText(/因此维持原有研究区间。$/);
  expect(reason).not.toBeVisible();
  await userEvent.click(within(card).getByText("查看完整调整原因"));
  expect(reason).toBeVisible();
  expect(card).toHaveTextContent(
    "备选右轨参考：入场 1.1000–1.2000 | 止损 1.0000 | 目标 1.3000 / 1.4000（距买区 10.00%）",
  );
  expect(card).not.toHaveTextContent("………");
  expect(JSON.stringify(data)).toBe(before);
});

it("collapses a historical repeated price note without clipping or rewriting the original explanation", async () => {
  const original = {
    ...plan(),
    priceRevisionNote:
      "没有新净值，价格不变。入场2.0319–2.1404，止损1.8347，目标2.5227/2.6790；右侧2.3560–2.4349，止损2.1785，目标2.6321/2.7307。",
  };
  const snapshot = JSON.stringify(original);
  render(<PlanRevision plan={original} />);
  const disclosure = screen.getByText("查看完整调整原因").closest("details")!;
  expect(disclosure).not.toHaveAttribute("open");
  expect(screen.getByLabelText("价格计划调整")).toHaveTextContent(
    "维持关注区间",
  );
  expect(screen.getByText(original.priceRevisionNote)).not.toBeVisible();
  await userEvent.click(screen.getByText("查看完整调整原因"));
  expect(screen.getByText(original.priceRevisionNote)).toBeVisible();
  expect(JSON.stringify(original)).toBe(snapshot);
});

function scanned(
  code: string,
  current: string,
  inZone: boolean,
  distance: number,
) {
  const base = plan(code, current);
  base.tracks[0].state = "CANDIDATE";
  return {
    ...base,
    signals: [
      {
        track: "LEFT_VALUE",
        triggerState: "CANDIDATE",
        inEntryZone: inZone,
        distanceToEntryPct: distance,
      },
    ],
  };
}
it("shows the actual scan size, sorts entered then nearest and tracks only frozen report identity", async () => {
  const data = daily();
  Object.assign(data.report!, {
    quantitativeScan: {
      methodVersion: "quantitative-scan-v1",
      asOf: data.asOf,
      minimumHistorySamples: 220,
      coverage: { pricedFundCount: 3, targetHistoryFundCount: 3 },
      items: [
        scanned("270042", "1.05", false, 3),
        scanned("007466", "1", true, 0),
        scanned("008975", "1.04", false, 2),
      ],
    },
  });
  const save = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(
    <MemoryRouter>
      <MacroContextStrip
        data={{ state: "READY", items: [], dailyResearch: data } as never}
        error={false}
        onStudyQuantScan={save}
      />
    </MemoryRouter>,
  );
  const summary = screen.getByText(/查看全市场 3 只核心赛道基金量化扫描总表/);
  expect(summary.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(summary);
  const table = screen.getByRole("table", { name: "核心赛道基金量化扫描" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows.map((row) => row.getAttribute("data-fund-code"))).toEqual([
    "007466",
    "008975",
    "270042",
  ]);
  expect(rows[0]).toHaveTextContent("🎯 已入左侧低吸买区");
  expect(rows[0]).toHaveTextContent("38.25");
  for (const value of ["0.9800", "1.0200", "0.9000", "1.1200", "1.2000"])
    expect(rows[0]).toHaveTextContent(value);
  await userEvent.click(within(rows[0]).getByRole("button", { name: /跟踪/ }));
  expect(save).toHaveBeenCalledExactlyOnceWith({
    reportRunId: "frozen-report",
    subjectKind: "nav",
    subjectId: "007466",
    planTrack: "LEFT_VALUE",
  });
  expect(
    within(rows[0]).getByRole("button", { name: "已跟踪 007466" }),
  ).toBeDisabled();
  rerender(
    <MemoryRouter>
      <MacroContextStrip
        data={
          {
            state: "READY",
            items: [],
            dailyResearch: { ...data, reportRunId: "new-report" },
          } as never
        }
        error={false}
        onStudyQuantScan={save}
      />
    </MemoryRouter>,
  );
  expect(
    screen.getByText(/查看全市场 3 只/).closest("details"),
  ).not.toHaveAttribute("open");
});

it("displays separate frozen, confirmed and estimated prices in the tracking list and detail", async () => {
  const frozen = plan();
  const priceWatch = {
    frozenPlan: frozen,
    confirmedNav: { value: "1.0100", asOf: "2026-10-16", usable: true },
    estimate1430: {
      value: "1.0150",
      asOf: "2026-10-19T14:30:00+08:00",
      usable: true,
    },
    holding: { state: "NONE" },
    state: "ENTRY_ZONE",
    basis: "ESTIMATE_1430",
    asOf: "2026-10-19T14:30:00+08:00",
    reason: "估算净值进入冻结研究区间",
  };
  const track = {
    id: "track",
    title: "人工智能跟踪",
    enabled: true,
    generation: 1,
    status: "OBSERVING",
    createdAt: "2026-10-19",
    updatedAt: "2026-10-19",
    unreadCount: 0,
    scope: { fundCode: "012733" },
    priceWatch,
  };
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [track],
    total: 1,
  } as never);
  vi.mocked(study.getStudyDetail).mockResolvedValue({
    track,
    priceWatch,
    version: {
      id: "v1",
      version: 1,
      snapshot: {
        title: "AI",
        thesis: "AI研究",
        origin: {},
        scope: {},
        sources: [],
        createdAt: "2026-10-19",
        expiresOn: "2026-11-19",
        transmission: [],
        supports: [],
        counterFactors: [],
        watchFor: [],
        invalidationConditions: [],
        observations: [],
        conditions: [],
      },
    },
    versions: [],
    checks: [],
    notes: [],
    tradeLinks: [],
    history: { limit: 50, offset: 0, hasMore: false },
    capabilities: { canEdit: true, canCheck: true, canLinkTrade: true },
  } as never);
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={["/fund-screener?studyTrack=track"]}>
        <StudyTracking actor="actor" scope={{}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(screen.getAllByText("🎯已入买入击球区")).toHaveLength(2),
  );
  const regions = screen.getAllByRole("region", { name: "冻结价位与当前行情" });
  expect(regions).toHaveLength(2);
  for (const region of regions) {
    expect(region).toHaveTextContent("冻结买入区间：0.9800–1.0200");
    expect(region).toHaveTextContent("止损 0.9000");
    expect(region).toHaveTextContent("T1 1.1200 / T2 1.2000");
    expect(region).toHaveTextContent("最新确认净值：1.0100");
    expect(region).toHaveTextContent("基金估算净值：1.0150");
    expect(region).not.toHaveTextContent("持仓观察中");
  }
});

it("keeps an unavailable scan row honest and failed saves retryable without losing another row", async () => {
  const data = daily();
  const invalid = scanned("000051", "1", false, 0.5);
  invalid.tracks[0].state = "INSUFFICIENT";
  invalid.tracks[1].state = "INSUFFICIENT";
  Object.assign(data.report!, {
    quantitativeScan: {
      methodVersion: "scan",
      asOf: data.asOf,
      minimumHistorySamples: 220,
      coverage: { pricedFundCount: 2, targetHistoryFundCount: 2 },
      items: [invalid, scanned("000216", "1", true, 0)],
    },
  });
  const save = vi
    .fn()
    .mockRejectedValueOnce(new Error("无权保存此报告，请重新登录"))
    .mockResolvedValue(undefined);
  render(<QuantitativeScan data={data} onTrack={save} />);
  await userEvent.click(screen.getByText(/查看全市场 2 只/));
  expect(screen.getByRole("button", { name: "+ 跟踪 000051" })).toBeDisabled();
  const button = screen.getByRole("button", { name: "+ 跟踪 000216" });
  await userEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "无权保存此报告，请重新登录",
  );
  expect(button).not.toBeDisabled();
  await userEvent.click(button);
  expect(
    await screen.findByRole("button", { name: "已跟踪 000216" }),
  ).toBeDisabled();
  expect(save).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("button", { name: "+ 跟踪 000051" })).toBeDisabled();
});

it.each([
  ["STOP_LOSS", "⚠️触及止损"],
  ["TAKE_PROFIT", "🎉触及止盈"],
  ["ENTRY_ZONE", "🎯已入买入击球区"],
  ["HOLDING", "📈持仓观察中"],
  ["OBSERVING", "区间观察中"],
  ["UNKNOWN", "行情待更新"],
] as const)(
  "renders the server's %s state without replacing missing observations with frozen prices",
  (state, label) => {
    render(
      <PriceWatchSummary
        watch={{
          frozenPlan: plan(),
          confirmedNav: null,
          estimate1430: null,
          holding: { state: "CONFIRMED" },
          state,
          basis: null,
          asOf: null,
          reason: "同口径行情核对结果",
        }}
      />,
    );
    const region = screen.getByRole("region", { name: "冻结价位与当前行情" });
    expect(
      within(region).getByText(label, { selector: "strong" }),
    ).toBeVisible();
    expect(region).toHaveTextContent("最新确认净值：—");
    expect(region).toHaveTextContent("基金估算净值：----");
  },
);

it("does not claim holdings from a saved plan, and dates unusable estimates separately", () => {
  render(
    <PriceWatchSummary
      watch={{
        frozenPlan: plan(),
        confirmedNav: null,
        estimate1430: {
          value: "1.1000",
          asOf: "2026-10-16T14:30:00+08:00",
          usable: false,
        },
        holding: { state: "NONE" },
        state: "HOLDING",
        basis: null,
        asOf: null,
        reason: "尚无当前持仓事实",
      }}
    />,
  );
  expect(screen.queryByText("📈持仓观察中")).not.toBeInTheDocument();
  expect(screen.getByText("行情待更新", { selector: "strong" })).toBeVisible();
  expect(screen.getByText("仅保留历史记录，不用于当前触发。")).toBeVisible();
  expect(screen.getByRole("region")).toHaveTextContent("2026年10月16日 14:30");
});

it("removes only a matching leading code, keeping middle digits and original names intact", () => {
  expect(fundDisplayName("012733｜易方达中证人工智能ETF联接A", "012733")).toBe(
    "易方达中证人工智能ETF联接A",
  );
  expect(fundDisplayName("012733易方达人工智能ETF联接A", "012733")).toBe(
    "易方达人工智能ETF联接A",
  );
  expect(fundDisplayName("易方达100人工智能012733联接A", "012733")).toBe(
    "易方达100人工智能012733联接A",
  );
  expect(fundDisplayName("012734 易方达人工智能ETF联接A", "012733")).toBe(
    "012734 易方达人工智能ETF联接A",
  );
  expect(fundDisplayName("0127330 系列基金", "012733")).toBe(
    "0127330 系列基金",
  );
});
