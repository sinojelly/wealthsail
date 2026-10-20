import { render, screen, within } from "@testing-library/react";
import type {
  ActionableZoneResult,
  DailyResearch,
  QuantitativeScanItem,
} from "../../api/investment";
import QuantitativeScan from "./components/QuantitativeScan";
import ValueDirections from "./components/ValueDirections";

function plan(): ActionableZoneResult {
  return {
    methodVersion: "actionable-zones-v4",
    subjectKind: "nav",
    subjectId: "161129",
    fundCode: "161129",
    label: "易方达原油",
    priceBasis: "UNIT_NAV",
    unit: "CNY/份",
    asOf: "2026-10-19",
    sourceUri: null,
    currentPrice: "2.0000",
    indicators: { ma20: 2, rsi14: 58 },
    selectedTrack: "RIGHT_TREND",
    tracks: [
      {
        track: "RIGHT_TREND",
        state: "CANDIDATE",
        priceState: "READY",
        entryZoneMin: "1.9800",
        entryZoneMax: "2.0100",
        stopLossPrice: "1.9000",
        takeProfitT1: "2.2000",
        takeProfitT2: "2.3000",
        reasons: [],
      },
    ],
  };
}

function report(row: ActionableZoneResult): DailyResearch {
  const scan: QuantitativeScanItem = {
    ...row,
    signals: [
      {
        track: "RIGHT_TREND",
        triggerState: "CANDIDATE",
        inEntryZone: true,
        entryReady: true,
        distanceToEntryPct: 0,
      },
    ],
  };
  return {
    methodVersion: "daily-research-v1",
    state: "READY",
    runId: "risk47",
    reportRunId: "risk47",
    scheduledFor: null,
    startedAt: null,
    completedAt: null,
    asOf: "2026-10-20T21:00:00+08:00",
    nextRunAt: null,
    report: {
      macroCycle: "关注外盘波动",
      summary: "风险观察",
      sources: [],
      events: [],
      upcoming: [],
      energy: [],
      coverage: [],
      valueDirections: [
        {
          key: "oil",
          title: "原油观察",
          thesis: "等待风险释放",
          signal: "WATCH",
          sourceIds: [],
          macroCodes: [],
          watchFor: [],
          plans: [row],
        },
      ],
      quantitativeScan: {
        methodVersion: "scan-v1",
        asOf: "2026-10-20T21:00:00+08:00",
        minimumHistorySamples: 220,
        coverage: { pricedFundCount: 1, targetHistoryFundCount: 1 },
        items: [scan],
      },
    },
  };
}

function renderBoth(data: DailyResearch) {
  render(
    <>
      <ValueDirections data={data} />
      <QuantitativeScan data={data} />
    </>,
  );
}

it("overrides candidate buy-zone labels on cards and the scan when delayed NAV is blocked", () => {
  const row = Object.assign(plan(), {
    entryStatus: "⚠️ 净值滞后1天·当晚外盘急跌暂缓入场",
    entryStatusTone: "amber",
    entryBlocked: true,
    entryReady: false,
    inBuyZone: false,
  });
  const data = report(row);
  renderBoth(data);
  const labels = screen.getAllByText(row.entryStatus);
  expect(labels).toHaveLength(2);
  for (const label of labels) expect(label).toHaveClass("op-entry-warning");
  expect(
    screen.queryByText(/已进入观察买入区|已入右侧买区/),
  ).not.toBeInTheDocument();
});

it("uses the right-track warning even when only the track carries the protected status", () => {
  const row = plan();
  Object.assign(row.tracks[0], {
    entryStatus: "⚠️ 处于近1年高位区(98%分位)·勿追高",
    entryStatusTone: "amber",
    entryBlocked: true,
    inBuyZone: false,
  });
  renderBoth(report(row));
  expect(
    screen.getAllByText("⚠️ 处于近1年高位区(98%分位)·勿追高"),
  ).toHaveLength(2);
  expect(
    screen.queryByText(/已进入观察买入区|已入右侧买区/),
  ).not.toBeInTheDocument();
});

it("shows the distance above the MA20 pullback zone on a right-track card", () => {
  const row = plan();
  row.currentPrice = "2.0804";
  render(<ValueDirections data={report(row)} />);
  expect(
    screen.getByText("🚀 高于买区 3.50%·等待回踩均线"),
  ).toBeInTheDocument();
  expect(screen.queryByText("已进入观察买入区")).not.toBeInTheDocument();
});

it("shows subscription restrictions beside the fund name on cards and the scan", () => {
  const row = Object.assign(plan(), {
    subscriptionRestricted: true,
    subscriptionLabel: "⛔ 单日限购10元",
  });
  renderBoth(report(row));
  const card = screen.getByRole("article", { name: "易方达原油买卖计划" });
  expect(within(card).getByRole("heading", { level: 4 })).toHaveTextContent(
    "⛔ 单日限购10元",
  );
  const labels = screen.getAllByText("⛔ 单日限购10元");
  expect(labels).toHaveLength(2);
  for (const label of labels)
    expect(label).toHaveClass("op-subscription-restricted");
});
