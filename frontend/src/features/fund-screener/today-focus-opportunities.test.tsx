import { act, fireEvent, screen, within } from "@testing-library/react";
import { renderWithSession } from "../../test/render-with-session";
import type {
  OpportunityContext,
  QuantitativeScanItem,
} from "../../api/investment";
import { frozenPlan } from "./personal-funds.test-data";
import TodayFocus from "./components/TodayFocus";

afterEach(() => vi.unstubAllGlobals());

function plan(code: string, current = "1.1000"): QuantitativeScanItem {
  const value = frozenPlan(code);
  value.label = `基金${code}`;
  value.currentPrice = current;
  value.asOf = "2026-11-09";
  value.indicators.navPercentile2y = 23.4;
  value.series = {
    state: "READY",
    fundCode: code,
    asOf: value.asOf,
    unit: "CNY/份",
    priceBasis: "UNIT_NAV",
    sourceUri: value.sourceUri,
    retrievedAt: value.asOf,
    points: [
      { date: "2026-11-06", value: "1.09" },
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
        plan("000103", "1.1700"),
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
  ).toEqual(["000101", "000102", "000103", "000104"]);
  expect(within(scan).getAllByText("参考买入区间")).toHaveLength(4);
});

it("uses the strict two percent boundary for expansion and preserves all scans", () => {
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
  expect(rows[0]).toHaveAttribute("open");
  expect(rows[1]).not.toHaveAttribute("open");
  expect(within(rows[1]).queryByText("参考买入区间")).toBeNull();
  fireEvent.click(within(rows[1]).getByText(/基金000202/));
});

it("uses displayed current price and reference bands for urgency rather than a stale three-percent signal", () => {
  const data = context();
  const serverNear = plan("000211", "1.2500");
  Object.assign(serverNear, {
    pricePosition: { status: "NEAR_ENTRY_ZONE", distanceToEntryPct: 2.99 },
  });
  const serverNeutral = plan("000212", "1.1700");
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
  const signalFar = plan("000214", "1.1700");
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
  ).toEqual(["000212", "000214"]);
  expect(
    rows
      .filter((r) => !r.hasAttribute("open"))
      .map((r) => r.getAttribute("data-fund-code")),
  ).toEqual(["000211", "000213"]);
});
