import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type {
  DailyResearch,
  OpportunityContext,
  QuantitativeScanItem,
} from "../../api/investment";
import ValueDirections from "./components/ValueDirections";
import QuantitativeScan from "./components/QuantitativeScan";
import MacroContextStrip from "./components/MacroContextStrip";
import { timeLabel } from "./macro-display";

function plan(
  code: string,
  side: "LEFT_VALUE" | "RIGHT_TREND" = "LEFT_VALUE",
  state: "CANDIDATE" | "WATCH" = "CANDIDATE",
  current = "1.1",
  score = 60,
  rr = 2,
): QuantitativeScanItem {
  return {
    methodVersion: "actionable-zones-v3",
    subjectKind: "nav",
    subjectId: code,
    fundCode: code,
    label: `主题基金${code}`,
    priceBasis: "UNIT_NAV",
    unit: "CNY/份",
    asOf: "2026-10-20",
    sourceUri: `https://fund.eastmoney.com/${code}.html`,
    currentPrice: current,
    indicators: { rsi14: 40 },
    selectedTrack: side,
    tracks: [
      {
        track: side,
        state,
        priceState: "READY",
        entryZoneMin: "1",
        entryZoneMax: "1.2",
        stopLossPrice: ".8",
        takeProfitT1: "1.8",
        takeProfitT2: "2",
        reasons: [],
        opportunityScore: score,
        rewardRiskRatio: rr,
        scoreMethodVersion: "opportunity-priority-v1",
      },
    ],
    opportunityScore: score,
    rewardRiskRatio: rr,
    signals: [
      {
        track: side,
        triggerState: state,
        inEntryZone: Number(current) >= 1 && Number(current) <= 1.2,
        entryReady:
          state === "CANDIDATE" &&
          Number(current) >= 1 &&
          Number(current) <= 1.2,
        distanceToEntryPct: current === "1.5" ? 20 : 0,
      },
    ],
  };
}
function daily(items: QuantitativeScanItem[]): DailyResearch {
  return {
    methodVersion: "daily-research-v1",
    state: "READY",
    runId: "presentation48",
    reportRunId: "presentation48",
    scheduledFor: null,
    startedAt: null,
    completedAt: null,
    asOf: "2026-10-20T08:30:00+08:00",
    nextRunAt: null,
    report: {
      asOf: "2026-10-20T08:30:00+08:00",
      summary: "真实研究",
      macroCycle: "观察总纲",
      coverage: [],
      sources: [],
      events: [],
      upcoming: [],
      energy: [],
      actionablePlans: [],
      valueDirections: items.map((item, index) => ({
        key: `d${index}`,
        title: `方向${item.fundCode}`,
        thesis: "根据公开证据观察",
        signal: "WATCH",
        sourceIds: [],
        macroCodes: [],
        watchFor: [],
        plans: [item],
        opportunityScore: item.opportunityScore,
      })),
      quantitativeScan: {
        methodVersion: "fund-quant-scan-v2",
        asOf: "2026-10-20",
        minimumHistorySamples: 220,
        coverage: {
          pricedFundCount: items.length,
          targetHistoryFundCount: items.length,
        },
        items,
      },
    },
  };
}
function show(children: React.ReactNode) {
  return render(<MemoryRouter>{children}</MemoryRouter>);
}
afterEach(() => vi.unstubAllGlobals());
beforeEach(() =>
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        }),
    ),
  ),
);

it("labels left, right, pending and out-of-zone plans consistently in cards", () => {
  show(
    <ValueDirections
      data={daily([
        plan("110001"),
        plan("110002", "RIGHT_TREND"),
        plan("110003", "RIGHT_TREND", "WATCH"),
        plan("110004", "LEFT_VALUE", "CANDIDATE", "1.5"),
      ])}
    />,
  );
  expect(screen.getByText("🎯 已入左侧低吸买区")).toHaveClass("tone-left");
  expect(screen.getByText("📈 已入右侧趋势买区")).toHaveClass("tone-right");
  expect(screen.getByText("价格近均线·待趋势触发")).toHaveClass("tone-wait");
  expect(screen.getByText("⏳ 距左侧买区 20.00%（待触发）")).toHaveClass(
    "tone-away",
  );
});
it("ranks real ready buy zones by score then reward/risk ahead of a stronger untriggered score", () => {
  const items = [
    plan("000001", "LEFT_VALUE", "WATCH", "1.1", 99, 3),
    plan("999003", "LEFT_VALUE", "CANDIDATE", "1.1", 70, 2),
    plan("111005", "LEFT_VALUE", "CANDIDATE", "1.1", 85, 1.5),
    plan("999007", "LEFT_VALUE", "CANDIDATE", "1.1", 85, 2.5),
  ];
  show(<QuantitativeScan data={daily(items)} />);
  const rows = screen.getByRole("table").querySelectorAll("tbody tr");
  expect([...rows].map((row) => row.getAttribute("data-fund-code"))).toEqual([
    "999007",
    "111005",
    "999003",
    "000001",
  ]);
});
it("puts a ready direction before an untriggered direction and uses score within the ready group", () => {
  show(
    <ValueDirections
      data={daily([
        plan("100001", "LEFT_VALUE", "WATCH", "1.1", 99),
        plan("100002", "LEFT_VALUE", "CANDIDATE", "1.1", 65),
        plan("100003", "LEFT_VALUE", "CANDIDATE", "1.1", 88),
      ])}
    />,
  );
  expect(
    screen
      .getAllByRole("heading", { level: 3 })
      .map((node) => node.textContent),
  ).toEqual([
    "今日价值方向与买卖计划",
    "方向100003",
    "方向100002",
    "方向100001",
  ]);
});
it("renders one high-zone warning instead of a duplicate buy label and puts the hot fund behind ready entries", () => {
  const hot = plan("000001", "RIGHT_TREND", "CANDIDATE", "1.1", 100);
  hot.indicators.rsi14 = 75;
  show(
    <QuantitativeScan
      data={daily([hot, plan("999999", "RIGHT_TREND", "CANDIDATE", "1.1", 60)])}
    />,
  );
  const rows = screen.getByRole("table").querySelectorAll("tbody tr");
  expect(rows[0]).toHaveAttribute("data-fund-code", "999999");
  const row = within(rows[1] as HTMLElement);
  expect(row.getAllByText("🔥 已入高位止盈区（勿追高）")).toHaveLength(1);
  expect(row.queryByText(/已入右侧.*买区/)).not.toBeInTheDocument();
});
it("keeps a higher-scoring high-zone cooling fund behind an ordinary waiting fund in cards and the scan", () => {
  const hot = plan("000001", "LEFT_VALUE", "WATCH", "2", 60);
  hot.industryCycle = { stage: "EARLY_COMMERCIALIZATION" };
  hot.industryRiskWarning = "⚠️ 行业去泡沫/等待订单期 · 谨防左侧接飞刀";
  const ordinary = plan("999999", "LEFT_VALUE", "WATCH", "1.5", 20);
  for (const item of [hot, ordinary]) {
    item.entryReady = false;
    item.inBuyZone = false;
    item.tracks[0].entryReady = false;
    item.tracks[0].inBuyZone = false;
  }
  show(
    <>
      <ValueDirections data={daily([hot, ordinary])} />
      <QuantitativeScan data={daily([hot, ordinary])} />
    </>,
  );
  expect(
    [...screen.getByRole("table").querySelectorAll("tbody tr")].map((row) =>
      row.getAttribute("data-fund-code"),
    ),
  ).toEqual(["999999", "000001"]);
  expect(
    screen.getAllByRole("heading", { level: 3 }).map((row) => row.textContent),
  ).toEqual(["今日价值方向与买卖计划", "方向999999", "方向000001"]);
});
it.each(["CANDIDATE", "WATCH"] as const)(
  "puts verified earnings delivery ahead of higher-scoring cooling funds with the same %s execution group",
  (state) => {
    const cooling = plan("000001", "LEFT_VALUE", state, "1.1", 60);
    cooling.industryCycle = { stage: "EARLY_COMMERCIALIZATION" };
    cooling.tracks[0].scoreBreakdown = { industryCyclePenalty: -20 };
    const delivered = plan("999999", "LEFT_VALUE", state, "1.1", 20);
    delivered.industryCycle = { stage: "EARNINGS_DELIVERY" };
    if (state === "WATCH") delivered.entryReady = false;
    show(
      <>
        <ValueDirections data={daily([cooling, delivered])} />
        <QuantitativeScan data={daily([cooling, delivered])} />
      </>,
    );
    expect(
      [...screen.getByRole("table").querySelectorAll("tbody tr")].map((row) =>
        row.getAttribute("data-fund-code"),
      ),
    ).toEqual(["999999", "000001"]);
    expect(
      screen
        .getAllByRole("heading", { level: 3 })
        .map((row) => row.textContent),
    ).toEqual(["今日价值方向与买卖计划", "方向999999", "方向000001"]);
  },
);
it("shows a personal sell card only from nonempty actor alerts even when research is collapsed", () => {
  const base = {
    state: "READY",
    items: [],
    sellRiskAlerts: {
      asOf: "2026-10-20T14:30:00+08:00",
      items: [],
      limitations: [],
    },
  } as unknown as OpportunityContext;
  const { rerender } = render(
    <MemoryRouter initialEntries={["/?researchOpen=0"]}>
      <MacroContextStrip data={base} error={false} />
    </MemoryRouter>,
  );
  expect(
    screen.queryByRole("alert", { name: "持仓/跟踪卖出与风控提醒" }),
  ).not.toBeInTheDocument();
  const personal = {
    ...base,
    sellRiskAlerts: {
      ...base.sellRiskAlerts!,
      items: [
        {
          fundCode: "008281",
          label: "真实持仓基金",
          relationship: ["HELD", "TRACKED"],
          state: "TAKE_PROFIT_T2",
          basis: "ESTIMATE_1430",
          value: "2.5",
          asOf: "2026-10-20T14:30:00+08:00",
          sourceUri: "https://fund.eastmoney.com/008281.html",
          rsi14: 75,
          rsiAsOf: "2026-10-19",
          planOrigin: "FROZEN_STUDY",
          stopLossPrice: "1.2",
          takeProfitT1: "2.2",
          takeProfitT2: "2.4",
        },
      ],
    },
  } as OpportunityContext;
  rerender(
    <MemoryRouter initialEntries={["/?researchOpen=0"]}>
      <MacroContextStrip data={personal} error={false} />
    </MemoryRouter>,
  );
  const alert = screen.getByRole("alert", { name: "持仓/跟踪卖出与风控提醒" });
  expect(alert).toHaveTextContent("真实持仓基金");
  expect(alert).toHaveTextContent("达到止盈目标二");
  expect(alert).toHaveTextContent("14:30估算净值");
  expect(alert).toHaveTextContent("已持仓");
});
it("renders date-only values without an invented missing-time suffix and preserves real Shanghai time", () => {
  expect(timeLabel("2026-10-20")).toBe("2026年10月20日");
  expect(timeLabel("2026-10-20T20:30:00Z")).toBe("2026年10月21日 04:30");
});
it("warns of QDII publication delay without claiming a reverse shock has already happened", () => {
  const item = plan("161129", "RIGHT_TREND");
  item.priceTimeBasis = "LATEST_DISCLOSED_NAV";
  show(<ValueDirections data={daily([item])} />);
  expect(
    screen
      .getAllByText(/留意最新外盘冲击/)
      .find((node) => !node.closest("details")),
  ).toBeVisible();
  expect(screen.queryByText(/已发生反向冲击/)).not.toBeInTheDocument();
});

it("keeps old unscored scans on the technical and distance fallback instead of deriving a new ranking", () => {
  const first = plan("000001"),
    second = plan("999999");
  first.tracks[0].takeProfitT1 = "1.4";
  second.tracks[0].takeProfitT1 = "1.9";
  for (const item of [first, second]) {
    delete item.opportunityScore;
    delete item.tracks[0].opportunityScore;
    delete item.tracks[0].rewardRiskRatio;
    delete item.signals[0].entryReady;
  }
  const data = daily([second, first]);
  data.report!.quantitativeScan!.methodVersion = "fund-quant-scan-v1";
  show(<QuantitativeScan data={data} />);
  expect(
    [...screen.getByRole("table").querySelectorAll("tbody tr")].map((row) =>
      row.getAttribute("data-fund-code"),
    ),
  ).toEqual(["000001", "999999"]);
});

it("keeps a stronger but subscription-restricted candidate behind an unrestricted fund in both views", () => {
  const restricted = plan("000001", "RIGHT_TREND", "CANDIDATE", "1.1", 99, 3);
  restricted.subscriptionRestricted = true;
  restricted.subscriptionLabel = "⛔ 暂停申购";
  restricted.preferredEntryEligible = false;
  const available = plan("999999", "RIGHT_TREND", "CANDIDATE", "1.1", 60, 2);
  show(
    <>
      <ValueDirections data={daily([restricted, available])} />
      <QuantitativeScan data={daily([restricted, available])} />
    </>,
  );
  const rows = screen.getByRole("table").querySelectorAll("tbody tr");
  expect(rows[0]).toHaveAttribute("data-fund-code", "999999");
  expect(
    screen
      .getAllByRole("heading", { level: 3 })
      .map((node) => node.textContent),
  ).toEqual(["今日价值方向与买卖计划", "方向999999", "方向000001"]);
  const blockedRow = within(rows[1] as HTMLElement);
  expect(blockedRow.queryByText("📈 已入右侧趋势买区")).not.toBeInTheDocument();
  expect(blockedRow.getByText("⛔ 暂停申购")).toHaveClass(
    "op-subscription-restricted",
  );
});

it("preserves signal safety precedence above track text and never styles the blocked scan as a buy hit", () => {
  const blocked = plan("161129", "RIGHT_TREND", "CANDIDATE", "1.1", 100, 3);
  Object.assign(blocked.tracks[0], {
    entryStatus: "轨道级风险提示",
    entryStatusTone: "amber",
    entryBlocked: true,
  });
  Object.assign(blocked.signals[0], {
    entryStatus: "已核对最新外盘风险·暂缓入场",
    entryStatusTone: "amber",
    entryBlocked: true,
    entryReady: false,
    inBuyZone: false,
  });
  show(<QuantitativeScan data={daily([blocked])} />);
  const label = screen.getByText("已核对最新外盘风险·暂缓入场");
  expect(label).toHaveClass("op-entry-warning");
  expect(label).not.toHaveClass("op-scan-hit");
  expect(screen.queryByText("轨道级风险提示")).not.toBeInTheDocument();
  expect(screen.queryByText("📈 已入右侧趋势买区")).not.toBeInTheDocument();
});
