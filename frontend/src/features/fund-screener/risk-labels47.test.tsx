import { render, screen, within } from "@testing-library/react";
import type {
  ActionableZoneResult,
  DailyResearch,
  QuantitativeScanItem,
} from "../../api/investment";
import QuantitativeScan from "./components/QuantitativeScan";
import ValueDirections from "./components/ValueDirections";
import ActionablePlan, {
  ActionablePlanEvidence,
} from "./components/ActionablePlan";

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

it("prioritizes the server sell status and shows paired buy/sell ranges with a clear action", () => {
  const row = plan();
  row.currentPrice = "2.2500";
  Object.assign(row, {
    inSellZone: true,
    entryStatus: "⚠️ 处于近1年高位区(98%分位)·勿追高",
    entryStatusTone: "amber",
    entryBlocked: true,
  });
  Object.assign(row.tracks[0], {
    inSellZone: true,
    entryStatus: "🔥 已入建议卖出区间(98%分位)·持仓宜止盈/空仓勿追高",
    entryStatusTone: "rose",
    sellZoneMin: "2.1800",
    sellZoneMax: "2.3200",
  });
  renderBoth(report(row));
  const card = screen.getByRole("article", { name: "易方达原油买卖计划" });
  const sellStatus = within(card).getByText(row.tracks[0].entryStatus!);
  expect(sellStatus).toHaveClass("tone-rose");
  expect(within(card).queryByText(row.entryStatus!)).not.toBeInTheDocument();
  expect(within(card).getByText("建议买入区间")).toBeInTheDocument();
  expect(within(card).getByText("建议卖出区间")).toBeInTheDocument();
  expect(within(card).getByText("2.1800–2.3200")).toHaveClass(
    "op-sell-zone-active",
  );
  expect(within(card).getByText("T1 / T2 分批止盈")).toBeInTheDocument();
  expect(
    within(card).getByText(
      "当前净值已进入建议卖出区间，持仓可分批止盈兑现；空仓请等待回落至建议买入区间再评估。",
    ),
  ).toBeInTheDocument();
});

it("shows the frozen server sell conclusion in the full actionable plan and falls back to legacy T1/T2 prices", () => {
  const row = plan();
  Object.assign(row, { inSellZone: true, entryStatusTone: "rose" });
  render(<ActionablePlan result={row} frozen />);
  const right = screen.getByRole("article", { name: "右侧趋势" });
  expect(within(right).getByText("建议买入区间")).toBeInTheDocument();
  expect(within(right).getByText("建议卖出区间")).toBeInTheDocument();
  expect(within(right).getByText("2.2000–2.3000")).toHaveClass(
    "op-sell-zone-active",
  );
  expect(
    within(right).getByText(
      "当前净值已进入建议卖出区间，持仓可分批止盈兑现；空仓请等待回落至建议买入区间再评估。",
    ),
  ).toBeInTheDocument();
});

it("explains historical support/resistance inputs with their exact Chinese labels", () => {
  const row = plan();
  row.indicators = {
    p35: "1.4200",
    p40: "1.4800",
    p50: "1.5300",
    p55: "1.5800",
    p60: "1.6100",
    p65: "1.6500",
    p85: "1.8200",
    p95: "1.9000",
    peakHigh: "2.0000",
  };
  row.parameters = {
    historicalPriceWindow: [220, 500],
    historicalPercentileMethod: "LINEAR_INTERPOLATION",
    middleHighNavPercentile: 70,
    maximumTrendBuyPricePercentile: 65,
    sellZoneLowerPricePercentile: 85,
    sellZoneUpperPricePercentile: 95,
    navRewardRiskBasis: "HISTORICAL_SUPPORT_RESISTANCE_NO_RR_FLOOR",
  };
  row.historicalPriceWindow = {
    state: "READY",
    requiredSamples: 220,
    sampleCount: 500,
    start: "2024-10-20",
    end: "2026-10-19",
  };
  render(<ActionablePlanEvidence result={row} />);
  expect(screen.getByText(/历史35%分位价格/)).toHaveTextContent("1.4200");
  expect(screen.getByText(/历史中位价格/)).toHaveTextContent("1.5300");
  expect(screen.getByText(/历史周期高点/)).toHaveTextContent("2.0000");
  expect(screen.getByText(/历史价格锚点/)).toHaveTextContent(
    "500个有效交易样本",
  );
  expect(screen.getByText(/参数：/)).toHaveTextContent(
    "历史价格分位算法=线性插值",
  );
  expect(screen.getByText(/参数：/)).toHaveTextContent(
    "净值买卖区间依据=历史支撑与阻力，不按现价抬高目标",
  );
  expect(
    screen.queryByText(/其他算法观察|其他策略参数/),
  ).not.toBeInTheDocument();
});
