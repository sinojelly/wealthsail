import { act, render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as api from "../../api/study-api";
import * as funds from "../../api/funds";
import * as personal from "../../api/personal-funds";
import MyFundsWorkspace from "./components/MyFundsWorkspace";
import PersonalActionReminders from "./components/PersonalActionReminders";
import type { SellRiskAlert } from "../../api/investment";
import {
  frozenPlan,
  fundTrack,
  heldFund,
  studyDetail,
} from "./personal-funds.test-data";

vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn(),
  getStudyDetail: vi.fn(),
  setStudyStatus: vi.fn(),
  setFundWatchStatus: vi.fn(),
}));
vi.mock("../../api/personal-funds", () => ({
  getPersonalFundsReference: vi.fn(),
}));
vi.mock("../../api/funds", async (original) => ({
  ...(await original<typeof import("../../api/funds")>()),
  getLatestFundQuotes: vi.fn(),
  getFundNavHistory: vi.fn(),
}));

function Location() {
  const location = useLocation();
  return <output aria-label="当前地址">{location.search}</output>;
}
function show(ui: React.ReactNode, path = "/fund-screener?keep=yes") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        {children}
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { ...render(ui, { wrapper }), client };
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(personal.getPersonalFundsReference).mockResolvedValue({
    version: "personal-funds-reference-v1",
    source: "LOCAL_DATABASE_ONLY",
    readOnly: true,
    asOf: "2026-11-07T12:00:00+08:00",
    funds: [],
  });
  // Most cases exercise plan/quote behavior in the no-IO browser fallback.
  // Viewport cases below install an observer and enter individual chart slots.
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    width: 600,
    height: 220,
    top: 0,
    right: 600,
    bottom: 220,
    left: 0,
    toJSON: () => ({}),
  });
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-07T04:00:00Z"));
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [fundTrack()],
    heldFunds: [heldFund()],
    total: 1,
  });
  vi.mocked(api.getStudyDetail).mockImplementation(async (id) =>
    studyDetail(fundTrack(id), `观察条件${id}`),
  );
  vi.mocked(api.setFundWatchStatus).mockResolvedValue({ enabled: false });
  vi.mocked(funds.getLatestFundQuotes).mockImplementation(async (codes) => ({
    asOf: "2026-11-07T04:00:00Z",
    items: codes.map((fundCode) => ({
      fundCode,
      confirmedNav: null,
      currentEstimate: null,
    })),
  }));
  vi.mocked(funds.getFundNavHistory).mockImplementation(
    async (fundCode, period) => ({
      methodVersion: "fund-unit-nav-trend-v1",
      fundCode,
      fundName: `基金${fundCode}`,
      period,
      priceBasis: "UNIT_NAV",
      unit: "CNY/份",
      state: "READY",
      asOf: "2026-11-05",
      retrievedAt: "2026-11-06T01:00:00Z",
      sourceUri: "https://example.org/nav-history",
      points: [
        { date: "2026-11-04", unitNav: "1.0100" },
        { date: "2026-11-05", unitNav: "1.0800" },
      ],
      coverage: {
        startDate: "2026-11-04",
        endDate: "2026-11-05",
        fullHistory: false,
        requestedStartDate: null,
        sampleCount: 2,
      },
      summary: {
        minimumNav: "1.0100",
        maximumNav: "1.0800",
        currentNav: "1.0800",
        percentilePct: 100,
        percentileMethod: "observed-range",
      },
      refresh: { state: "IDLE", runId: null, pollAfterMs: null },
    }),
  );
});

it("shows watchlist and current mentioned funds even when no formal research plan was saved", async () => {
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [],
    heldFunds: [],
    total: 0,
  });
  vi.mocked(personal.getPersonalFundsReference).mockResolvedValue({
    version: "personal-funds-reference-v1",
    source: "LOCAL_DATABASE_ONLY",
    readOnly: true,
    asOf: "2026-11-07T12:00:00+08:00",
    funds: [
      {
        key: "FUND_CN:013477",
        fundCode: "013477",
        fundMarket: "FUND_CN",
        label: "自选基金",
        relationships: ["WATCHLIST"],
        investmentEligible: false,
        plan: frozenPlan("013477"),
        history: null,
        confirmedNav: null,
        currentEstimate: null,
        missingReasons: [],
      },
      {
        key: "FUND_CN:011035",
        fundCode: "011035",
        fundMarket: "FUND_CN",
        label: "提及观察基金",
        relationships: ["RESEARCH_MENTIONED"],
        investmentEligible: false,
        plan: null,
        history: null,
        confirmedNav: null,
        currentEstimate: null,
        missingReasons: ["LOCAL_PRICE_PLAN_INCOMPLETE"],
      },
    ],
  });
  show(<MyFundsWorkspace actor="tenant:user" />);
  const mentioned = await screen.findByRole("article", {
    name: "提及观察基金",
  });
  expect(mentioned).toHaveTextContent("本期研究提及");
  expect(screen.getByRole("article", { name: "自选基金" })).toHaveTextContent(
    "参考买入区间",
  );
  expect(
    within(mentioned).queryByRole("button", { name: /取消跟踪/ }),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "本期提及" }));
  expect(
    screen.getByRole("article", { name: "提及观察基金" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("article", { name: "自选基金" }),
  ).not.toBeInTheDocument();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("requests only nearby personal NAV charts and keeps an activated chart across scrolling and plan changes", async () => {
  const user = userEvent.setup();
  const observations: Array<{
    callback: IntersectionObserverCallback;
    target?: Element;
    disconnect: ReturnType<typeof vi.fn>;
  }> = [];
  vi.stubGlobal(
    "IntersectionObserver",
    vi.fn(function (callback: IntersectionObserverCallback) {
      const observation = {
        callback,
        target: undefined as Element | undefined,
        observe: (target: Element) => {
          observation.target = target;
        },
        disconnect: vi.fn(),
      };
      observations.push(observation);
      return observation;
    }),
  );
  const first = fundTrack("near-first", "006531");
  const alternate = fundTrack("near-alternate", "006531");
  alternate.priceWatch!.frozenPlan = frozenPlan("006531", "1.0800");
  const offscreen = fundTrack("offscreen", "006532");
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [first, alternate, offscreen],
    total: 3,
  });
  show(<MyFundsWorkspace actor="viewport" />);
  const nearCard = await screen.findByRole("article", { name: "基金006531" });
  const farCard = screen.getByRole("article", { name: "基金006532" });
  expect(funds.getFundNavHistory).not.toHaveBeenCalled();
  expect(within(nearCard).queryByRole("img")).not.toBeInTheDocument();
  expect(within(farCard).queryByRole("img")).not.toBeInTheDocument();

  function intersect(card: HTMLElement, isIntersecting: boolean) {
    const observation = observations.find((row) =>
      row.target ? card.contains(row.target) : false,
    );
    expect(observation).toBeDefined();
    act(() => {
      observation!.callback(
        [
          {
            target: observation!.target,
            isIntersecting,
          } as IntersectionObserverEntry,
        ],
        observation as unknown as IntersectionObserver,
      );
    });
  }

  intersect(nearCard, false);
  expect(funds.getFundNavHistory).not.toHaveBeenCalled();
  intersect(nearCard, true);
  const graph = await within(nearCard).findByRole("img", {
    name: "006531单位净值走势",
  });
  expect(funds.getFundNavHistory).toHaveBeenCalledTimes(1);
  expect(funds.getFundNavHistory).toHaveBeenCalledWith(
    "006531",
    "2y",
    expect.any(AbortSignal),
  );
  expect(nearCard).toHaveTextContent("曲线末点早于最新确认净值");
  intersect(nearCard, false);
  await user.click(
    within(nearCard).getByRole("button", {
      name: /选择计划 研究near-alternate/,
    }),
  );
  expect(nearCard).toHaveTextContent("1.0800–1.1500");
  expect(within(nearCard).getByRole("img")).toBe(graph);
  expect(funds.getFundNavHistory).toHaveBeenCalledTimes(1);
  expect(within(farCard).queryByRole("img")).not.toBeInTheDocument();

  intersect(farCard, true);
  expect(await within(farCard).findByRole("img")).toBeInTheDocument();
  expect(funds.getFundNavHistory).toHaveBeenCalledTimes(2);
});

it("loads private holding and original plans independently of a failed public report and only reads the chosen detail", async () => {
  const user = userEvent.setup();
  const first = fundTrack("first");
  const second = fundTrack("second");
  second.priceWatch!.frozenPlan = frozenPlan("024194", "1.0800");
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [first, second],
    total: 2,
    heldFunds: [heldFund()],
  });
  show(
    <>
      <p>公共研究读取失败</p>
      <MyFundsWorkspace actor="a" />
    </>,
  );
  const card = await screen.findByRole("article", { name: "持仓基金024194" });
  expect(card).toHaveTextContent("已持仓");
  expect(card).toHaveTextContent("最新确认净值");
  expect(card).toHaveTextContent("1.1410");
  expect(card).toHaveTextContent("1.0500–1.1500");
  expect(card).toHaveTextContent("计划行情基准");
  expect(card).toHaveTextContent("研究建立时间");
  expect(screen.queryByText(/实际收益|持仓成本/)).not.toBeInTheDocument();
  expect(api.getStudyDetail).not.toHaveBeenCalled();
  await user.click(
    within(card).getByRole("button", { name: /选择计划 研究second/ }),
  );
  expect(card).toHaveTextContent("1.0800–1.1500");
  expect(await within(card).findByText(/观察条件second/)).toBeInTheDocument();
  expect(api.getStudyDetail).toHaveBeenCalledWith(
    "second",
    expect.any(AbortSignal),
  );
  expect(api.getStudyDetail).toHaveBeenCalledTimes(1);
  expect(card).toHaveTextContent("版本 2");
});

it("shows every fund's complete prices and chart slot by default while deferring offscreen charts", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "IntersectionObserver",
    vi.fn(function () {
      return { observe: vi.fn(), disconnect: vi.fn() };
    }),
  );
  const items = ["000101", "000102", "000103", "000104", "000105"].map((code) =>
    fundTrack(code, code),
  );
  vi.mocked(api.getStudyTracks).mockResolvedValue({ items, total: 5 });
  show(<MyFundsWorkspace actor="five" />);
  const cards = await screen.findAllByRole("article", { name: /基金00010/ });
  expect(cards).toHaveLength(5);
  for (const card of cards) {
    expect(card).toHaveTextContent("最新确认净值");
    expect(card).toHaveTextContent("参考买入区间");
    expect(
      within(card).getByRole("region", { name: /净值走势图区域/ }),
    ).toBeInTheDocument();
  }
  expect(funds.getFundNavHistory).not.toHaveBeenCalled();
  await user.type(
    screen.getByRole("searchbox", { name: "搜索我的基金" }),
    "000105",
  );
  expect(screen.getAllByRole("article", { name: /基金00010/ })).toHaveLength(1);
  expect(
    screen.getByRole("article", { name: "基金000105" }),
  ).toBeInTheDocument();
  expect(funds.getFundNavHistory).not.toHaveBeenCalled();
});

it("separates held and tracked filters and preserves ended and non-fund research in history", async () => {
  const user = userEvent.setup();
  const ended = {
    ...fundTrack("ended", "000202"),
    enabled: false,
    status: "ENDED",
  };
  const index = {
    ...fundTrack("index"),
    scope: { indexId: "hs300" },
    priceWatch: null,
  };
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [fundTrack("tracking", "000201"), ended, index],
    total: 3,
    heldFunds: [heldFund("000203")],
  });
  show(
    <MyFundsWorkspace
      actor="filters"
      renderStudyTracking={() => <p>完整研究编辑与复盘</p>}
    />,
  );
  await screen.findByRole("article", { name: "基金000201" });
  await user.click(screen.getByRole("button", { name: "已持仓" }));
  expect(
    screen.queryByRole("article", { name: "基金000201" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("article", { name: "持仓基金000203" }),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "仅跟踪" }));
  expect(
    screen.queryByRole("article", { name: "持仓基金000203" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "历史复盘" }));
  expect(screen.getByRole("button", { name: /研究ended/ })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /研究index/ }));
  expect(screen.getByLabelText("当前地址")).toHaveTextContent(
    "studyTrack=index",
  );
  expect(screen.getByLabelText("当前地址")).toHaveTextContent("keep=yes");
  expect(screen.getByText("完整研究编辑与复盘")).toBeInTheDocument();
});

it("does not look up public domestic NAV for code-less holdings or equal ETF codes", async () => {
  const holding = { ...heldFund("000301"), fundCode: null };
  const etf = fundTrack("etf", "000302");
  etf.scope.fundMarket = "ETF_CN";
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [etf],
    total: 1,
    heldFunds: [holding],
  });
  show(<MyFundsWorkspace actor="no-code" />);
  await screen.findByRole("article", { name: "持仓基金000301" });
  expect(screen.getAllByText(/净值图暂不可用/)).toHaveLength(2);
  expect(funds.getFundNavHistory).not.toHaveBeenCalled();
  expect(funds.getLatestFundQuotes).not.toHaveBeenCalled();
});

it("keeps a real holding visible after canceling tracking", async () => {
  const user = userEvent.setup();
  let cancelled = false;
  vi.mocked(api.getStudyTracks).mockImplementation(async () => ({
    items: [fundTrack()],
    total: 1,
    heldFunds: [heldFund()],
    suppressedFundKeys: cancelled ? ["FUND_CN:024194"] : [],
  }));
  vi.mocked(api.setFundWatchStatus).mockImplementation(async () => {
    cancelled = true;
    return { enabled: false };
  });
  show(<MyFundsWorkspace actor="cancel" />);
  const cancel = await screen.findByRole("button", { name: "取消跟踪 024194" });
  await user.click(cancel);
  expect(
    await screen.findByText("已取消跟踪，持仓记录保留。"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("article", { name: "持仓基金024194" }),
  ).toHaveTextContent("已持仓");
  expect(api.setFundWatchStatus).toHaveBeenCalledWith({
    fundCode: "024194",
    fundMarket: "FUND_CN",
    enabled: false,
  });
});

it("isolates actor cache and resets private search and plan selection on account switch", async () => {
  const user = userEvent.setup();
  const view = show(<MyFundsWorkspace actor="actor-a" />);
  await screen.findByRole("article", { name: "持仓基金024194" });
  await user.type(
    screen.getByRole("searchbox", { name: "搜索我的基金" }),
    "024194",
  );
  vi.mocked(api.getStudyTracks).mockRejectedValue(
    new Error("account-b unavailable"),
  );
  view.rerender(<MyFundsWorkspace actor="actor-b" />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "我的基金读取失败",
  );
  expect(screen.queryByText("持仓基金024194")).not.toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "搜索我的基金" })).toHaveValue(
    "",
  );
  expect(view.client.getQueryData(["study", "actor-a", "list"])).toBeDefined();
  expect(
    view.client.getQueryData(["study", "actor-b", "list"]),
  ).toBeUndefined();
});

it("opens the original review from an existing studyTrack deep link", async () => {
  show(
    <MyFundsWorkspace
      actor="deep"
      renderStudyTracking={() => <p>原研究复盘可访问</p>}
    />,
    "/fund-screener?researchTab=tracking&studyTrack=old-index",
  );
  expect(await screen.findByText("原研究复盘可访问")).toBeInTheDocument();
});

it("distinguishes reminder read failure, missing quotes and a successfully read no-hit state", async () => {
  const view = show(
    <PersonalActionReminders actor="missing" onViewFunds={() => undefined} />,
  );
  vi.mocked(api.getStudyTracks).mockRejectedValue(new Error("private denied"));
  view.rerender(
    <PersonalActionReminders actor="failed" onViewFunds={() => undefined} />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "行动提醒读取失败",
  );
  expect(screen.queryByText("暂无新触发")).not.toBeInTheDocument();
  const missing = fundTrack();
  missing.priceWatch = null;
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [missing],
    total: 1,
  });
  view.rerender(
    <PersonalActionReminders actor="no-quote" onViewFunds={() => undefined} />,
  );
  expect(await screen.findByText(/行情依据不足/)).toBeInTheDocument();
  expect(screen.queryByText("暂无新触发")).not.toBeInTheDocument();
  const observing = fundTrack();
  observing.priceWatch!.state = "OBSERVING";
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [observing],
    total: 1,
  });
  view.rerender(
    <PersonalActionReminders actor="observing" onViewFunds={() => undefined} />,
  );
  expect(await screen.findByText("暂无新触发")).toBeInTheDocument();
});

it("does not call unplanned real holdings a completed no-hit check", async () => {
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [],
    total: 0,
    heldFunds: [heldFund()],
  });
  show(
    <PersonalActionReminders
      actor="holding-only"
      onViewFunds={() => undefined}
    />,
  );
  expect(
    await screen.findByText(/1 只持仓基金净值与参考买卖点正在补齐/),
  ).toBeVisible();
  expect(screen.queryByText("暂无新触发")).not.toBeInTheDocument();
});

it("shows live quantitative holding triggers and collapses normal holdings into one row", async () => {
  const holding = {
    fundCode: "024194",
    label: "持仓基金",
    relationship: ["HELD"] as ["HELD"],
    state: "BUY_ZONE" as const,
    basis: "CONFIRMED_NAV" as const,
    value: "1.1200",
    asOf: "2026-11-09",
    planOrigin: "CURRENT_ACTIONABLE_ZONES" as const,
    entryZoneMin: "1.0500",
    entryZoneMax: "1.1500",
    sellZoneMin: "1.3500",
    sellZoneMax: "1.5000",
    stopLossPrice: "0.9500",
    distanceToBuyPct: 0,
    distanceToSellPct: 20.54,
    distanceToStopPct: 15.17,
    dailyChangePct: -2.6,
    triggerStates: ["BUY_ZONE", "LARGE_DAILY_MOVE"] as Array<
      "BUY_ZONE" | "LARGE_DAILY_MOVE"
    >,
  };
  show(
    <PersonalActionReminders
      actor="live-holdings"
      onViewFunds={() => {}}
      actionData={{
        asOf: "2026-11-09",
        items: [],
        limitations: [],
        actionReminders: [holding],
        normalHoldings: [
          {
            ...holding,
            fundCode: "000689",
            state: "NORMAL",
            triggerStates: [],
            distanceToBuyPct: 4.5,
          },
        ],
        summary: {
          heldCount: 2,
          evaluatedCount: 2,
          alertCount: 1,
          normalCount: 1,
          unavailableCount: 0,
        },
      }}
    />,
  );
  expect(await screen.findByText(/低吸加仓参考/)).toBeVisible();
  expect(screen.getByText(/当日大幅波动提醒/)).toBeVisible();
  const summary = screen.getByText(/其余 1 只持仓基金当前处于正常持有区间/);
  expect(summary).toBeVisible();
  expect(screen.getByText(/距离买区 4.50%/)).not.toBeVisible();
  await userEvent.click(summary);
  expect(screen.getByText(/距离买区 4.50%/)).toBeVisible();
  expect(screen.queryByText(/尚无可评估的冻结计划/)).toBeNull();
});

it.each(["UNKNOWN", "NONE"] as const)(
  "keeps a %s holding identity pending in both reminders and fund details",
  async (state) => {
    const track = fundTrack();
    track.priceWatch!.state = "HOLDING";
    track.priceWatch!.holding.state = state;
    vi.mocked(api.getStudyTracks).mockResolvedValue({
      items: [track],
      total: 1,
    });
    show(
      <>
        <PersonalActionReminders
          actor="holding-identity"
          onViewFunds={() => undefined}
        />
        <MyFundsWorkspace actor="holding-identity" />
      </>,
    );
    const reminders = screen.getByRole("region", { name: "我的行动提醒" });
    expect(await within(reminders).findByText(/持仓身份待核实/)).toBeVisible();
    expect(within(reminders).queryByText("暂无新触发")).not.toBeInTheDocument();
    const fundsRegion = screen.getByRole("region", { name: "我的基金" });
    expect(await within(fundsRegion).findByText("行情待更新")).toBeVisible();
  },
);

it("keeps a server observation with missing usable quote pending instead of declaring no triggers", async () => {
  const track = fundTrack();
  track.priceWatch!.state = "OBSERVING";
  track.priceWatch!.confirmedNav = null;
  vi.mocked(api.getStudyTracks).mockResolvedValue({ items: [track], total: 1 });
  show(
    <PersonalActionReminders
      actor="no-confirmed-quote"
      onViewFunds={() => undefined}
    />,
  );
  expect(await screen.findByText(/行情依据不足/)).toBeVisible();
  expect(screen.queryByText("暂无新触发")).not.toBeInTheDocument();
});

function frozenSellAlert(track: api.StudyTrack): SellRiskAlert {
  const watch = track.priceWatch!;
  const plan = watch.frozenPlan!.tracks[0];
  return {
    fundCode: track.scope.fundCode!,
    label: track.title,
    relationship: ["TRACKED"],
    state: "STOP_LOSS",
    basis: "CONFIRMED_NAV",
    value: watch.confirmedNav!.value,
    asOf: watch.asOf,
    sourceUri: null,
    rsi14: null,
    rsiAsOf: null,
    planOrigin: "FROZEN_STUDY",
    trackId: track.id,
    selectedTrack: plan.track,
    stopLossPrice: plan.stopLossPrice,
    takeProfitT1: plan.takeProfitT1,
    takeProfitT2: plan.takeProfitT2,
  };
}

it.each(["missing-plan", "missing-track"])(
  "keeps %s observations pending even when a quote exists",
  async (gap) => {
    const track = fundTrack();
    track.priceWatch!.state = "OBSERVING";
    if (gap === "missing-plan") track.priceWatch!.frozenPlan = null;
    else track.priceWatch!.frozenPlan!.tracks = [];
    vi.mocked(api.getStudyTracks).mockResolvedValue({
      items: [track],
      total: 1,
    });
    show(<PersonalActionReminders actor={gap} onViewFunds={() => undefined} />);
    expect(await screen.findByText(/行情依据不足/)).toBeVisible();
    expect(screen.queryByText("暂无新触发")).not.toBeInTheDocument();
  },
);

it("keeps equal-code sell warnings from different markets and different research plans visible", async () => {
  const domestic = fundTrack("domestic-stop", "000402");
  domestic.priceWatch!.state = "STOP_LOSS";
  const foreign = fundTrack("foreign-stop", "000402");
  foreign.scope.fundMarket = "ETF_CN";
  foreign.priceWatch!.state = "STOP_LOSS";
  const otherPlan = fundTrack("another-stop", "000402");
  otherPlan.priceWatch!.state = "STOP_LOSS";
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [domestic, foreign, otherPlan],
    total: 3,
  });
  show(
    <PersonalActionReminders
      actor="same-code-alerts"
      onViewFunds={() => undefined}
      sellRiskAlerts={[frozenSellAlert(domestic)]}
    />,
  );
  const region = screen.getByRole("region", { name: "我的行动提醒" });
  expect(await within(region).findByText(/研究foreign-stop/)).toBeVisible();
  expect(within(region).getByText(/研究another-stop/)).toBeVisible();
  expect(
    within(region).queryByText(/研究domestic-stop/),
  ).not.toBeInTheDocument();
  expect(within(region).getByText(/ETF_CN/)).toBeVisible();
});

it.each(["missing-id", "different-risk", "older-observation"])(
  "does not suppress a sell reminder when the global alert has %s",
  async (difference) => {
    const track = fundTrack("visible-stop", "000402");
    track.priceWatch!.state = "STOP_LOSS";
    const alert = frozenSellAlert(track);
    if (difference === "missing-id") delete alert.trackId;
    if (difference === "different-risk") alert.state = "TAKE_PROFIT_T1";
    if (difference === "older-observation") alert.asOf = "2026-11-05";
    vi.mocked(api.getStudyTracks).mockResolvedValue({
      items: [track],
      total: 1,
    });
    show(
      <PersonalActionReminders
        actor={`sell-${difference}`}
        onViewFunds={() => undefined}
        sellRiskAlerts={[alert]}
      />,
    );
    expect(await screen.findByText(/研究visible-stop/)).toBeVisible();
  },
);

it.each(["UNKNOWN", "MANUAL_REVIEW_REQUIRED", "INVALIDATED"] as const)(
  "retains a %s plan check even when its sell observation duplicates a global alert",
  async (status) => {
    const track = fundTrack("pending-stop", "000402");
    track.status = status;
    track.priceWatch!.state = "STOP_LOSS";
    vi.mocked(api.getStudyTracks).mockResolvedValue({
      items: [track],
      total: 1,
    });
    show(
      <PersonalActionReminders
        actor={`pending-${status}`}
        onViewFunds={() => undefined}
        sellRiskAlerts={[frozenSellAlert(track)]}
      />,
    );
    expect(await screen.findByText(/000402.*待核实/)).toBeVisible();
    expect(screen.queryByText("暂无新触发")).not.toBeInTheDocument();
  },
);

it("points to an already displayed risk instead of declaring no triggers after trusted deduplication", async () => {
  const track = fundTrack("duplicate-stop", "000402");
  track.priceWatch!.state = "STOP_LOSS";
  vi.mocked(api.getStudyTracks).mockResolvedValue({ items: [track], total: 1 });
  show(
    <PersonalActionReminders
      actor="duplicate-risk"
      onViewFunds={() => undefined}
      sellRiskAlerts={[frozenSellAlert(track)]}
    />,
  );
  expect(
    await screen.findByText(/相同风险已在上方.*卖出与风控提醒/),
  ).toBeVisible();
  expect(screen.queryByText("暂无新触发")).not.toBeInTheDocument();
  expect(screen.queryByText(/研究duplicate-stop/)).not.toBeInTheDocument();
});

it("shows only active trigger or explicit check reminders and suppresses duplicate global sell alerts", async () => {
  const user = userEvent.setup();
  const entry = fundTrack("entry", "000401");
  const stop = fundTrack("stop", "000402");
  stop.priceWatch!.state = "STOP_LOSS";
  const check = fundTrack("check", "000403");
  check.priceWatch!.state = "UNKNOWN";
  const disabled = { ...fundTrack("disabled", "000404"), enabled: false };
  const ended = { ...fundTrack("ended", "000405"), status: "ENDED" };
  const expired = {
    ...fundTrack("expired", "000406"),
    expiresOn: "2026-11-06",
  };
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [entry, stop, check, disabled, ended, expired],
    total: 6,
  });
  const onViewFunds = vi.fn();
  show(
    <PersonalActionReminders
      actor="reminders"
      onViewFunds={onViewFunds}
      sellRiskAlerts={[frozenSellAlert(stop)]}
    />,
  );
  const region = await screen.findByRole("region", { name: "我的行动提醒" });
  expect(await within(region).findByText(/基金000401/)).toBeInTheDocument();
  expect(within(region).getByText(/基金000403/)).toBeInTheDocument();
  expect(region).not.toHaveTextContent("000402");
  expect(region).not.toHaveTextContent("000404");
  expect(region).not.toHaveTextContent("000405");
  expect(region).not.toHaveTextContent("000406");
  expect(within(region).queryByRole("table")).not.toBeInTheDocument();
  expect(within(region).queryByRole("img")).not.toBeInTheDocument();
  await user.click(
    within(region).getByRole("button", { name: "查看我的基金" }),
  );
  expect(onViewFunds).toHaveBeenCalledOnce();
});

it("shares a single actor list request between workspace and reminders", async () => {
  const track = fundTrack("shared-study", "006506");
  track.scope.fundMarket = "ETF_CN";
  vi.mocked(api.getStudyTracks).mockResolvedValue({ items: [track], total: 1 });
  show(
    <>
      <MyFundsWorkspace actor="shared" />
      <PersonalActionReminders actor="shared" onViewFunds={() => undefined} />
    </>,
  );
  await screen.findByRole("article", { name: "基金006506" });
  await waitFor(() =>
    expect(
      screen.getByRole("region", { name: "我的行动提醒" }),
    ).toHaveTextContent("006506"),
  );
  // An ETF does not mount the public domestic quote resource. This isolates
  // initial actor-query sharing from the independent quote-update refresh.
  expect(api.getStudyTracks).toHaveBeenCalledOnce();
});

it.each(["CNY", "元", "元/份", "CNY/unit", "CNY/份", "CNY/share"])(
  "keeps original buy, stop and target overlays for the server-supported UNIT_NAV unit %s",
  async (unit) => {
    const track = fundTrack("old-plan", "006501");
    track.priceWatch!.frozenPlan!.unit = unit;
    vi.mocked(api.getStudyTracks).mockResolvedValue({
      items: [track],
      total: 1,
    });
    show(<MyFundsWorkspace actor={`unit-${unit}`} />);
    const card = await screen.findByRole("article", { name: "基金006501" });
    expect(card).toHaveTextContent("1.0500–1.1500");
    expect(await within(card).findByText("建议买入区间")).toBeInTheDocument();
    const graph = within(card).getByRole("img", { name: "006501单位净值走势" });
    await waitFor(() => {
      expect(graph.querySelectorAll(".recharts-reference-area")).toHaveLength(
        2,
      );
      expect(graph.querySelectorAll(".recharts-reference-line")).toHaveLength(
        1,
      );
    });
  },
);

it("keeps unrecognized plan units out of the domestic NAV chart", async () => {
  const track = fundTrack("foreign-unit", "006502");
  track.priceWatch!.frozenPlan!.unit = "USD";
  vi.mocked(api.getStudyTracks).mockResolvedValue({ items: [track], total: 1 });
  show(<MyFundsWorkspace actor="unsupported-unit" />);
  const card = await screen.findByRole("article", { name: "基金006502" });
  expect(card).toHaveTextContent("原计划口径或单位未确认匹配");
  expect(
    await within(card).findByRole("img", { name: "006502单位净值走势" }),
  ).toBeInTheDocument();
  expect(within(card).queryByText("建议买入区间")).not.toBeInTheDocument();
});

it.each(["unknown-market", "invalid-code"])(
  "cancels original studies with their generation when code-based fund identity is unsafe: %s",
  async (kind) => {
    const user = userEvent.setup();
    const code = kind === "invalid-code" ? "legacy-symbol" : "006503";
    let items = [
      fundTrack("first-legacy", code),
      fundTrack("second-legacy", code),
    ];
    items[1].generation = 5;
    if (kind === "unknown-market") {
      for (const track of items) {
        track.scope.fundMarket = undefined;
        track.priceWatch!.frozenPlan!.market = undefined;
      }
    }
    vi.mocked(api.getStudyTracks).mockImplementation(async () => ({
      items,
      total: items.length,
    }));
    vi.mocked(api.setStudyStatus).mockImplementation(async (id, request) => {
      const track = items.find((row) => row.id === id)!;
      if (request.expectedGeneration !== track.generation)
        throw new Error("版本不一致");
      const updated = {
        ...track,
        enabled: request.enabled,
        generation: track.generation + 1,
      };
      items = items.map((row) => (row.id === id ? updated : row));
      return studyDetail(updated);
    });
    show(<MyFundsWorkspace actor={`cancel-${kind}`} />);
    await user.click(
      await screen.findByRole("button", { name: `取消跟踪 ${code}` }),
    );
    expect(
      await screen.findByText(
        "暂无持仓、活动跟踪、自选或本期提及基金；历史研究仍可查看。",
      ),
    ).toBeInTheDocument();
    expect(api.setFundWatchStatus).not.toHaveBeenCalled();
    expect(api.setStudyStatus).toHaveBeenCalledWith("first-legacy", {
      expectedGeneration: 3,
      enabled: false,
    });
    expect(api.setStudyStatus).toHaveBeenCalledWith("second-legacy", {
      expectedGeneration: 5,
      enabled: false,
    });
  },
);

it("refreshes partially cancelled studies and retries only the remaining original generation", async () => {
  const user = userEvent.setup();
  let items = [
    fundTrack("first-partial", "legacy-symbol"),
    fundTrack("second-partial", "legacy-symbol"),
  ].map((track) => ({
    ...track,
    generation: 1,
  }));
  let secondFailed = false;
  vi.mocked(api.getStudyTracks).mockImplementation(async () => ({
    items,
    total: items.length,
  }));
  vi.mocked(api.setStudyStatus).mockImplementation(async (id, request) => {
    const track = items.find((row) => row.id === id)!;
    if (request.expectedGeneration !== track.generation)
      throw new Error("版本不一致");
    if (id === "second-partial" && !secondFailed) {
      secondFailed = true;
      throw new Error("第二份研究取消失败，请重试。");
    }
    const updated = {
      ...track,
      enabled: request.enabled,
      generation: track.generation + 1,
    };
    items = items.map((row) => (row.id === id ? updated : row));
    return studyDetail(updated);
  });
  const view = show(<MyFundsWorkspace actor="partial-cancel" />);
  await user.click(
    await screen.findByRole("button", { name: "取消跟踪 legacy-symbol" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "第二份研究取消失败，请重试。",
  );
  expect(
    screen.queryByText("已取消跟踪，历史研究记录保留。"),
  ).not.toBeInTheDocument();
  await waitFor(() => {
    const card = screen.getByRole("article", { name: "基金legacy-symbol" });
    expect(
      within(card).queryByRole("button", {
        name: /选择计划 研究first-partial/,
      }),
    ).not.toBeInTheDocument();
    expect(
      within(card).getByRole("button", { name: /选择计划 研究second-partial/ }),
    ).toBeInTheDocument();
  });
  expect(
    view.client
      .getQueryData<api.StudyTrackList>(["study", "partial-cancel", "list"])
      ?.items.find((track) => track.id === "first-partial"),
  ).toMatchObject({ enabled: false, generation: 2 });
  await user.click(
    screen.getByRole("button", { name: "取消跟踪 legacy-symbol" }),
  );
  expect(
    await screen.findByText(
      "暂无持仓、活动跟踪、自选或本期提及基金；历史研究仍可查看。",
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(api.setStudyStatus).toHaveBeenCalledTimes(3);
  expect(api.setStudyStatus).toHaveBeenNthCalledWith(1, "first-partial", {
    expectedGeneration: 1,
    enabled: false,
  });
  expect(api.setStudyStatus).toHaveBeenNthCalledWith(2, "second-partial", {
    expectedGeneration: 1,
    enabled: false,
  });
  expect(api.setStudyStatus).toHaveBeenNthCalledWith(3, "second-partial", {
    expectedGeneration: 1,
    enabled: false,
  });
  expect(api.setFundWatchStatus).not.toHaveBeenCalled();
});

it("reports an unsafe held-only identity without creating an UNKNOWN market preference or claiming success", async () => {
  const user = userEvent.setup();
  const holding = { ...heldFund("006504"), fundMarket: "" };
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [],
    total: 0,
    heldFunds: [holding],
  });
  show(<MyFundsWorkspace actor="unsafe-held" />);
  await user.click(
    await screen.findByRole("button", { name: "取消跟踪 006504" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("无法取消跟踪");
  expect(
    screen.getByRole("article", { name: "持仓基金006504" }),
  ).toHaveTextContent("已持仓");
  expect(
    screen.queryByText("已取消跟踪，持仓记录保留。"),
  ).not.toBeInTheDocument();
  expect(api.setFundWatchStatus).not.toHaveBeenCalled();
});

it("retains the safe instrument-only cancellation path for a known-market holding without a code", async () => {
  const user = userEvent.setup();
  const holding = {
    ...heldFund("006505"),
    fundCode: null,
    instrumentId: "00000000-0000-4000-8000-000000000001",
  };
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [],
    total: 0,
    heldFunds: [holding],
  });
  show(<MyFundsWorkspace actor="safe-held" />);
  await user.click(
    await screen.findByRole("button", { name: "取消跟踪 持仓基金006505" }),
  );
  expect(
    await screen.findByText("已取消跟踪，持仓记录保留。"),
  ).toBeInTheDocument();
  expect(api.setFundWatchStatus).toHaveBeenCalledWith({
    instrumentId: "00000000-0000-4000-8000-000000000001",
    fundMarket: "FUND_CN",
    enabled: false,
  });
  expect(
    screen.getByRole("article", { name: "持仓基金006505" }),
  ).toHaveTextContent("已持仓");
});
