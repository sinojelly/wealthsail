import { screen, within } from "@testing-library/react";
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

it("shows unaudited direction and mentioned-fund reference levels and band charts on the card front", () => {
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
  const direction = screen.getByRole("article", { name: "AI硬件" });
  expect(within(direction).getByText("基于已有资讯研判")).toBeVisible();
  expect(within(direction).getByText("参考买入区间")).toBeVisible();
  expect(within(direction).getByText("1.0500–1.1500")).toBeVisible();
  expect(within(direction).getByText("23.4%")).toBeVisible();
  expect(
    within(direction).getByRole("img", { name: "000101单位净值走势" }),
  ).toBeVisible();
  const mentions = screen.getByRole("region", { name: "本期提及基金" });
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
  expect(screen.getByText("方向15")).toBeVisible();
  expect(screen.getByText("研究主题12")).toBeVisible();
});

it("tops up from real entry, near-entry and trend scan signals, deduplicating represented funds", () => {
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
  const scan = screen.getByRole("region", { name: "量化模型精选机会" });
  expect(within(scan).getByText(/基金000102/)).toBeVisible();
  expect(within(scan).getByText(/基金000103/)).toBeVisible();
  expect(within(scan).getByText(/基金000104/)).toBeVisible();
  expect(within(scan).queryByText(/基金000101/)).toBeNull();
  expect(within(scan).queryByText(/基金000105/)).toBeNull();
  expect(within(scan).queryByText(/基金000106/)).toBeNull();
  expect(within(scan).getAllByText("参考买入区间")).toHaveLength(3);
});

it("uses current NAV as the distance denominator at the 2.99 and 3.01 percent boundary", () => {
  const data = context();
  Object.assign(data.dailyResearch!.report!, {
    quantitativeScan: {
      items: [
        plan("000201", String(1.15 / (1 - 0.0299))),
        plan("000202", String(1.15 / (1 - 0.0301))),
      ],
    },
  });
  renderWithSession(<TodayFocus data={data} onViewEvidence={() => {}} />);
  const scan = screen.getByRole("region", { name: "量化模型精选机会" });
  expect(within(scan).getByText(/基金000201/)).toBeVisible();
  expect(within(scan).queryByText(/基金000202/)).toBeNull();
});

it("prefers server price position and signal distances over inferred boundaries", () => {
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
  const scan = screen.getByRole("region", { name: "量化模型精选机会" });
  expect(within(scan).getByText(/基金000211/)).toBeVisible();
  expect(within(scan).getByText(/基金000213/)).toBeVisible();
  expect(within(scan).queryByText(/基金000212/)).toBeNull();
  expect(within(scan).queryByText(/基金000214/)).toBeNull();
});
