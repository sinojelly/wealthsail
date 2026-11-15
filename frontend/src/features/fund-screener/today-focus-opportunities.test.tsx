import { act, fireEvent, screen, within } from "@testing-library/react";
import { renderWithSession } from "../../test/render-with-session";
import type {
  OpportunityContext,
  QuantitativeScanItem,
} from "../../api/investment";
import { frozenPlan } from "./personal-funds.test-data";
import TodayFocus from "./components/TodayFocus";
import { todayPriority } from "./today-action-board";

afterEach(() => vi.unstubAllGlobals());

function plan(code: string, current = "1.1000"): QuantitativeScanItem {
  const value = frozenPlan(code);
  value.label = `基金${code}`;
  value.currentPrice = current;
  value.asOf = "2026-11-09";
  value.indicators.navPercentile2y = 23.4;
  value.historicalPriceWindow = {
    state: "READY",
    sampleCount: 470,
    requiredSamples: 120,
    start: "2024-11-10",
    end: value.asOf,
  };
  value.priceRefresh = {
    state: "READY",
    navAsOf: value.asOf,
    methodVersion: value.methodVersion,
  };
  value.series = {
    state: "READY",
    fundCode: code,
    asOf: value.asOf,
    unit: "CNY/份",
    priceBasis: "UNIT_NAV",
    sourceUri: value.sourceUri,
    retrievedAt: value.asOf,
    points: [
      { date: "2024-11-10", value: "1.09" },
      { date: "2026-11-09", value: current },
    ],
  };
  return { ...value, signals: [] };
}

function context(): OpportunityContext {
  return {
    state: "READY",
    items: [],
    dailyResearch: {
      state: "READY",
      runId: "report",
      reportRunId: "report",
      report: {
        asOf: "2026-11-10",
        summary: "市场观察。",
        valueDirections: [],
        mentionedFunds: [],
        topicCatalog: [],
      },
    },
  } as unknown as OpportunityContext;
}

it("shows unaudited direction and mentioned-fund reference levels and band charts on the card front", async () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  const data = context();
  Object.assign(data.dailyResearch!.report!, {
    valueDirections: [
      {
        key: "ai",
        title: "AI硬件",
        thesis: "算力与存储需求",
        auditStatus: "UNAUDITED_SCOPE",
        qualificationStatus: "OBSERVATION_ONLY",
        plans: [plan("000101")],
      },
    ],
    mentionedFunds: [
      {
        fundCode: "000102",
        name: "正文提及基金",
        actionableZones: plan("000102"),
      },
    ],
  });
  renderWithSession(<TodayFocus data={data} onViewEvidence={() => {}} />);
  const direction = screen
    .getAllByTestId("today-fund-row")
    .find((r) => r.getAttribute("data-fund-code") === "000101")!;
  fireEvent.click(screen.getByText(/研究方向与主题/));
  fireEvent.click(
    screen.getByText("AI硬件", { selector: ".op-today-insight strong" }),
  );
  expect(screen.getByText("基于已有资讯研判")).toBeVisible();
  expect(within(direction).getByText("参考买入区间")).toBeVisible();
  expect(within(direction).getByText("1.0500–1.1500")).toBeVisible();
  expect(within(direction).getByText("23.4%")).toBeVisible();
  await act(async () => vi.dynamicImportSettled());
  expect(
    within(direction).getByRole("img", { name: "000101单位净值走势" }),
  ).toBeVisible();
  const mentions = screen
    .getAllByTestId("today-fund-row")
    .find((r) => r.getAttribute("data-fund-code") === "000102")!;
  expect(
    within(mentions).getByText("参考卖出区间（止盈 T1 / T2）"),
  ).toBeVisible();
  expect(
    within(mentions).getByRole("img", { name: "000102单位净值走势" }),
  ).toBeVisible();
});

it("retains every topic and direction instead of truncating discovery output", () => {
  const data = context();
  Object.assign(data.dailyResearch!.report!, {
    valueDirections: Array.from({ length: 16 }, (_, i) => ({
      key: `d${i}`,
      title: `方向${i}`,
      thesis: "方向观点",
      plans: [],
    })),
    topicCatalog: Array.from({ length: 12 }, (_, i) => ({
      key: `topic${i}`,
      title: `研究主题${i + 1}`,
      thesis: "主题观点",
    })),
  });
  renderWithSession(<TodayFocus data={data} onViewEvidence={() => {}} />);
  fireEvent.click(screen.getByText(/研究方向与主题/));
  expect(
    screen.getByText("方向15", { selector: ".op-today-insight strong" }),
  ).toBeVisible();
  expect(screen.getByText("研究主题12")).toBeVisible();
});

it("merges real scan signals with AI funds and keeps non-actionable scans folded", () => {
  const data = context();
  const trend = plan("000104", "1.2500");
  trend.tracks.push({
    ...trend.tracks[0],
    track: "RIGHT_TREND",
    state: "CANDIDATE",
  });
  Object.assign(trend.indicators, { ma20: 1.2, ma60: 1.17 });
  Object.assign(data.dailyResearch!.report!, {
    valueDirections: [
      {
        key: "ai",
        title: "AI硬件",
        thesis: "保留方向",
        plans: [plan("000101")],
      },
    ],
    quantitativeScan: {
      items: [
        plan("000101"),
        plan("000102"),
        plan("000103", "1.1650"),
        trend,
        plan("000105", "1.2800"),
        plan("000106", ".9000"),
      ],
    },
  });
  renderWithSession(<TodayFocus data={data} onViewEvidence={() => {}} />);
  const scan = screen.getByRole("region", {
    name: "按操作紧迫度排序的基金机会",
  });
  const rows = within(scan).getAllByTestId("today-fund-row");
  expect(rows).toHaveLength(6);
  expect(
    rows.filter((r) => r.getAttribute("data-fund-code") === "000101"),
  ).toHaveLength(1);
  expect(
    rows
      .filter((r) => r.hasAttribute("open"))
      .map((r) => r.getAttribute("data-fund-code")),
  ).toEqual(["000101", "000102"]);
  expect(within(scan).getAllByText("参考买入区间")).toHaveLength(2);
});

it("groups both 1.99% and 2% into the buy watch group with charts collapsed", () => {
  const data = context();
  Object.assign(data.dailyResearch!.report!, {
    quantitativeScan: {
      items: [
        plan("000201", String(1.15 / (1 - 0.0199))),
        plan("000202", String(1.15 / (1 - 0.02))),
      ],
    },
  });
  renderWithSession(<TodayFocus data={data} onViewEvidence={() => {}} />);
  const rows = screen.getAllByTestId("today-fund-row");
  expect(rows[0]).not.toHaveAttribute("open");
  expect(rows[1]).not.toHaveAttribute("open");
  expect(rows[0].closest("section")).toHaveAccessibleName(/即将入区/);
  expect(rows[1].closest("section")).toHaveAccessibleName(/即将入区/);
  expect(within(rows[1]).queryByText("参考买入区间")).toBeNull();
  fireEvent.click(within(rows[1]).getByText(/基金000202/));
});

it("shows same-track buys and their logic alongside the right-trend evidence", async () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  const data = context();
  const coal = {
    ...plan("161032"),
    label: "富国中证煤炭指数A",
    opportunityScore: 42,
    macroOpportunity: {
      stance: "TAILWIND",
      label: "🔥 宏观/政策顺风",
      rationale: "上游价格走高有利于煤炭收入，仍需核对需求和供给。",
      riskLevel: "NONE",
    },
  };
  const coalAlternative = { ...plan("161724"), label: "招商中证煤炭指数A" };
  const trend = plan("012552", "1.25");
  trend.tracks.push({
    ...trend.tracks[0],
    track: "RIGHT_TREND",
    state: "CANDIDATE",
  });
  Object.assign(trend.indicators, {
    ma20: 1.218,
    ma60: 1.145,
    rsi14: 53.5,
    momentum20: 0.075,
    excessReturn20d: 5.45,
  });
  Object.assign(data.dailyResearch!.report!, {
    discoveryActionableZones: [coal, coalAlternative, trend],
    researchTopics: [
      {
        key: "coal",
        title: "煤炭机会",
        thesis: "关注煤炭价格与库存变化。",
        primaryFundCodes: ["161032"],
        plans: [coal],
      },
    ],
  });
  renderWithSession(<TodayFocus data={data} onViewEvidence={() => {}} />);
  const rows = screen.getAllByTestId("today-fund-row");
  expect(rows.map((row) => row.dataset.fundCode)).toEqual([
    "161032",
    "161724",
    "012552",
  ]);
  expect(within(rows[0]).getByText(/上游价格走高/)).toBeVisible();
  expect(within(rows[0]).getByText(/关注煤炭价格与库存/)).toBeVisible();
  expect(
    within(rows[1].querySelector("summary")!).getByText("已入买区"),
  ).toBeVisible();
  expect(within(rows[2]).getByText(/MA20 1.2180 > MA60 1.1450/)).toBeVisible();
  expect(within(rows[2]).getByText(/RSI14 53.5/)).toBeVisible();
  expect(within(rows[2]).getByText(/近20日 \+7.50%/)).toBeVisible();
  expect(within(rows[2]).getByText(/较沪深300 \+5.45 个百分点/)).toBeVisible();
  await act(async () => vi.dynamicImportSettled());
  expect(
    within(rows[0]).getAllByRole("img", { name: "161032单位净值走势" }),
  ).toHaveLength(1);
});

it("uses displayed current price and reference bands for urgency rather than a stale three-percent signal", () => {
  const data = context();
  const serverNear = plan("000211", "1.2500");
  Object.assign(serverNear, {
    pricePosition: { status: "NEAR_ENTRY_ZONE", distanceToEntryPct: 2.99 },
  });
  const serverNeutral = plan("000212", "1.1650");
  Object.assign(serverNeutral, {
    pricePosition: { status: "NEUTRAL", distanceToEntryPct: 3.01 },
  });
  const signalNear = plan("000213", "1.2500");
  signalNear.signals = [
    {
      track: "LEFT_VALUE",
      triggerState: "WATCH",
      inEntryZone: false,
      distanceToEntryPct: 2.99,
    },
  ];
  const signalFar = plan("000214", "1.1650");
  signalFar.signals = [
    {
      track: "LEFT_VALUE",
      triggerState: "WATCH",
      inEntryZone: false,
      distanceToEntryPct: 3.01,
    },
  ];
  Object.assign(data.dailyResearch!.report!, {
    quantitativeScan: {
      items: [serverNear, serverNeutral, signalNear, signalFar],
    },
  });
  renderWithSession(<TodayFocus data={data} onViewEvidence={() => {}} />);
  const rows = screen.getAllByTestId("today-fund-row");
  expect(
    rows
      .filter((r) => r.hasAttribute("open"))
      .map((r) => r.getAttribute("data-fund-code")),
  ).toEqual([]);
  expect(
    rows
      .filter((r) => !r.hasAttribute("open"))
      .map((r) => r.getAttribute("data-fund-code")),
  ).toEqual(["000212", "000214", "000211", "000213"]);
});

it("puts near-sell coal above its trend signal and opens its single chart from any quick-table cell", async () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  const data = context();
  const coal = plan("161032", "2.1560");
  coal.label = "富国中证煤炭指数(LOF)A";
  coal.tracks[0].entryZoneMin = "1.8800";
  coal.tracks[0].entryZoneMax = "1.9501";
  coal.tracks[0].takeProfitT1 = "2.2332";
  coal.tracks[0].takeProfitT2 = "2.3100";
  coal.tracks[0].buyZoneBacktest = {
    touchCount: 8,
    evaluatedCount: 6,
    winRatePct: 50,
    averageMaxReboundPct: 7.4,
    forwardObservations: 60,
    winThresholdPct: 5,
  };
  coal.indicators = {
    navPercentile2y: 68.6,
    ma20: 2.05,
    ma60: 2,
    excessReturn20d: 5,
  };
  coal.macroOpportunity = {
    stance: "TAILWIND",
    label: "宏观/政策顺风",
    rationale: "上游价格走高有利于煤炭收入。",
    riskLevel: "NONE",
  };
  coal.industryCycle = {
    stage: "UNKNOWN",
    evidenceSummary: "炼焦煤库存累积，下游钢厂需求偏弱。",
  };
  data.dailyResearch!.report!.discoveryActionableZones = [coal];
  renderWithSession(<TodayFocus data={data} onViewEvidence={() => {}} />);
  const fund = screen.getByTestId("today-fund-row");
  expect(fund.closest("section")).toHaveAccessibleName(/高位止盈参考/);
  expect(fund).not.toHaveAttribute("open");
  expect(within(fund).queryByText(/宏观\/政策顺风/)).toBeNull();
  const table = screen.getByRole("table", { name: "今日操作速览" });
  fireEvent.click(within(table).getByText(/高位勿追，持仓/));
  expect(fund).toHaveAttribute("open");
  expect(within(fund).getByText(/68.6% 分位.*9.55%.*3.58%/)).toBeVisible();
  expect(
    within(
      within(fund).getByRole("region", { name: "投研结论与操作理由" }),
    ).getByText(/炼焦煤库存累积/),
  ).toBeVisible();
  expect(
    within(
      within(fund).getByRole("region", { name: "参考买卖点与操作状态" }),
    ).getByText(/接近卖出参考区/),
  ).toBeVisible();
  expect(within(fund).getByText(/按当前区间回看触及 8 次/)).toBeVisible();
  expect(within(fund).queryByText(/上游价格走高/)).toBeNull();
  await act(async () => vi.dynamicImportSettled());
  expect(
    within(fund).getAllByRole("img", { name: "161032单位净值走势" }),
  ).toHaveLength(1);
});

it("requires observed strength rather than a saved candidate flag for a right-side row", () => {
  const p = plan("000001", "1.25");
  p.tracks.push({ ...p.tracks[0], state: "CANDIDATE", track: "RIGHT_TREND" });
  Object.assign(p.indicators, { ma20: 1.25, ma60: 1.2, excessReturn20d: 2 });
  expect(todayPriority(p).tone).toBe("neutral");
  p.indicators.ma20 = 1.2;
  expect(todayPriority(p).tone).toBe("trend");
  p.indicators.ma20 = 1.25;
  p.indicators.excessReturn20d = 2.01;
  expect(todayPriority(p).tone).toBe("trend");
});
