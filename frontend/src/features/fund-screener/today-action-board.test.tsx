import { act, fireEvent, screen, within } from "@testing-library/react";
import { renderWithSession } from "../../test/render-with-session";
import type { OpportunityContext } from "../../api/investment";
import { frozenPlan } from "./personal-funds.test-data";
import TodayFocus from "./components/TodayFocus";
import { buildTodayBoard } from "./today-action-board";

function plan(code: string, distance: number, percentile = 50) {
  const p = frozenPlan(code);
  const currentPrice = String(1.15 / (1 - distance / 100));
  p.currentPrice = currentPrice;
  p.indicators.navPercentile2y = percentile;
  p.series = {
    state: "READY",
    fundCode: code,
    asOf: p.asOf,
    unit: "CNY/份",
    priceBasis: "UNIT_NAV",
    sourceUri: p.sourceUri,
    retrievedAt: p.asOf,
    points: Array.from({ length: 122 }, (_, i) => ({
      date:
        i === 0
          ? "2024-10-22"
          : i === 121
            ? p.asOf!
            : new Date(Date.UTC(2026, 0, i)).toISOString().slice(0, 10),
      value: currentPrice,
    })),
  };
  return p;
}
function data() {
  return {
    state: "READY",
    items: [],
    dailyResearch: {
      state: "READY",
      runId: "r",
      reportRunId: "r",
      report: {
        valueDirections: [
          {
            key: "ai",
            title: "AI硬件",
            thesis: "完整保留洞察",
            plans: [plan("000101", 2), plan("000102", 0.55)],
          },
        ],
        topicCatalog: [
          {
            key: "t",
            title: "存储主题",
            thesis: "另一洞察",
            plans: [
              plan("000102", 0.55),
              plan("000103", 0.34),
              plan("000104", 0, 12),
              plan("000105", 0, 8),
            ],
          },
        ],
        mentionedFunds: [
          {
            fundCode: "000102",
            name: "重复提及",
            actionableZones: plan("000102", 0.55),
          },
        ],
      },
    },
  } as unknown as OpportunityContext;
}
it("sorts entry by percentile then strictly by distance, keeping exactly 2% folded and merging fund appearances", () => {
  const board = buildTodayBoard(data());
  expect(board.funds.map((f) => f.code)).toEqual([
    "000105",
    "000104",
    "000103",
    "000102",
    "000101",
  ]);
  expect(board.funds.map((f) => f.priority.expanded)).toEqual([
    true,
    true,
    true,
    true,
    false,
  ]);
  expect(board.funds.find((f) => f.code === "000102")?.origins).toEqual([
    "AI硬件",
    "存储主题",
    "正文提及",
  ]);
  expect(board.insights.map((i) => i.title)).toEqual(["存储主题", "AI硬件"]);
});
it("never treats unknown prices or stop breaches as a zero-distance buying opportunity", () => {
  const value = data();
  const unknown = plan("000106", 0);
  unknown.currentPrice = null;
  const stopped = plan("000107", 0);
  stopped.currentPrice = ".90";
  value.dailyResearch!.report!.mentionedFunds = [unknown, stopped].map((p) => ({
    fundCode: p.subjectId,
    actionableZones: p,
  }));
  const rows = buildTodayBoard(value).funds;
  expect(rows.find((f) => f.code === "000106")?.priority.expanded).toBe(false);
  expect(rows.find((f) => f.code === "000107")?.priority.expanded).toBe(false);
  expect(rows.find((f) => f.code === "000107")?.priority.rank).toBeGreaterThan(
    2,
  );
});
it("keeps nonempty legacy topics beside an empty newer field and merges unpriced mentions", () => {
  const value = data();
  value.dailyResearch!.report!.researchTopics = [];
  value.dailyResearch!.report!.mentionedFunds = [
    { fundCode: "000102", name: "重复提及" },
    { fundCode: "000999", name: "未定价" },
    { fundCode: "000999", name: "重复未定价" },
  ];
  const board = buildTodayBoard(value);
  expect(board.insights.some((i) => i.title === "存储主题")).toBe(true);
  expect(board.unpriced.map((f) => f.fundCode)).toEqual(["000999"]);
  expect(board.funds.find((f) => f.code === "000102")?.origins).toContain(
    "正文提及",
  );
});
it("preserves different dated plans but chooses the newest coordinates for urgency", () => {
  const value = data();
  const newest = plan("000102", 8);
  newest.asOf = "2026-11-13";
  value.dailyResearch!.report!.mentionedFunds = [
    { fundCode: "000102", actionableZones: newest },
  ];
  const fund = buildTodayBoard(value).funds.find((f) => f.code === "000102")!;
  expect(fund.plans).toHaveLength(2);
  expect(fund.plans[0].asOf).toBe("2026-11-13");
  expect(fund.priority.expanded).toBe(false);
});
it("uses the valid displayed prices and only primary funds for theme badges", () => {
  const value = data();
  const bank = plan("008298", 0);
  bank.currentPrice = "2";
  bank.priceRefresh = {
    state: "PARTIAL",
    navAsOf: bank.asOf,
    methodVersion: bank.methodVersion,
  };
  const wrong = plan("004069", 0);
  value.dailyResearch!.report!.researchTopics = [
    {
      key: "bank",
      title: "银行红利卖区评估",
      primaryFundCodes: ["008298"],
      plans: [wrong, bank],
    },
    {
      key: "grid",
      title: "电网建设",
      primaryFundCodes: [],
      plans: [wrong],
    },
  ];
  const board = buildTodayBoard(value);
  expect(board.funds.find((f) => f.code === "008298")?.priority.label).toBe(
    "已达卖出参考区",
  );
  expect(board.funds.find((f) => f.code === "004069")?.origins).not.toContain(
    "银行红利卖区评估",
  );
  expect(
    board.insights.find((row) => row.key === "研究主题:bank")?.priority.label,
  ).toBe("已入卖区（止盈评估）");
  expect(
    board.insights.find((row) => row.key === "研究主题:grid"),
  ).toMatchObject({ funds: [], priority: { label: "主题观察" } });
});
it("keeps one preferred representative across providers in the same track", () => {
  const value = data();
  const a = { ...plan("001631", 0), label: "天弘中证食品饮料ETF联接A" };
  const c = { ...plan("001632", 0), label: "天弘中证食品饮料ETF联接C" };
  const other = { ...plan("001633", 0), label: "另家中证食品饮料ETF联接C" };
  value.dailyResearch!.report!.mentionedFunds = [a, c, other].map(
    (actionableZones) => ({
      fundCode: actionableZones.subjectId,
      actionableZones,
    }),
  );
  const board = buildTodayBoard(value, ["001632"]);
  expect(
    board.funds
      .filter((f) => f.code.startsWith("00163"))
      .map((f) => [f.code, f.priority.expanded]),
  ).toEqual([
    ["001631", false],
    ["001632", true],
    ["001633", false],
  ]);
  const group = board.groups.find(
    (row) => row.representative.code === "001632",
  );
  expect(group?.alternatives.map((fund) => fund.code)).toEqual([
    "001631",
    "001633",
  ]);
});

it("limits automatic details to eight current representatives with one year of verified NAV", () => {
  const value = data();
  value.dailyResearch!.report!.valueDirections = [];
  value.dailyResearch!.report!.topicCatalog = [];
  const candidates = Array.from({ length: 12 }, (_, i) =>
    plan(`0002${String(i).padStart(2, "0")}`, 0, i),
  );
  candidates[0].series!.points[0].date = "2026-09-01";
  candidates[1].priceRefresh = {
    state: "PARTIAL",
    navAsOf: candidates[1].asOf,
    methodVersion: "test",
    reason: "NEWER_CONFIRMED_NAV_AVAILABLE",
  };
  candidates[2].series!.state = "STALE";
  const held = plan("000309", 0);
  held.currentPrice = "0.91";
  held.label = "科技军工ETF联接A";
  value.sellRiskAlerts = {
    asOf: held.asOf,
    limitations: [],
    items: [
      {
        fundCode: "000309",
        label: held.label,
        relationship: ["HELD"],
        state: "STOP_LOSS",
        basis: "CONFIRMED_NAV",
        value: held.currentPrice,
        asOf: held.asOf,
        sourceUri: held.sourceUri,
        rsi14: null,
        rsiAsOf: null,
        planOrigin: "DAILY_REPORT",
        stopLossPrice: held.tracks[0].stopLossPrice,
        takeProfitT1: held.tracks[0].takeProfitT1,
        takeProfitT2: held.tracks[0].takeProfitT2,
      },
    ],
  };
  value.dailyResearch!.report!.mentionedFunds = [...candidates, held].map(
    (actionableZones) => ({
      fundCode: actionableZones.subjectId,
      actionableZones,
    }),
  );
  const board = buildTodayBoard(value);
  expect(
    board.funds
      .filter((fund) => fund.priority.expanded)
      .map((fund) => fund.code),
  ).toEqual([
    "000203",
    "000204",
    "000205",
    "000206",
    "000207",
    "000208",
    "000209",
    "000210",
  ]);
  expect(board.holdingAlerts.map((fund) => fund.code)).toEqual(["000309"]);
});

it("opens the alternate disclosure from a research association without opening its chart", () => {
  const value = data();
  const report = value.dailyResearch!.report!;
  report.valueDirections![0].plans = [
    { ...plan("023884", 0, 8), label: "华夏中证金融科技ETF联接A" },
    { ...plan("013477", 0, 15), label: "华宝中证金融科技ETF联接A" },
  ];
  renderWithSession(
    <TodayFocus
      data={value}
      watchedCodes={["013477"]}
      onViewEvidence={() => {}}
    />,
  );
  const details = screen.getByText(/同赛道备选基金/).closest("details")!;
  expect(details).not.toHaveAttribute("open");
  fireEvent.click(screen.getByText(/研究方向与主题/));
  fireEvent.click(
    screen.getByText("AI硬件", { selector: ".op-today-insight strong" }),
  );
  fireEvent.click(
    screen.getByRole("link", { name: /华夏中证金融科技ETF联接A/ }),
  );
  expect(details).toHaveAttribute("open");
  const row = within(details).getByTestId("today-fund-row");
  expect(row).not.toHaveAttribute("open");
  expect(within(row).queryByText("参考买入区间")).toBeNull();
});
it("shows every insight, mounts each fund once, and only mounts a folded fund after expansion", async () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  renderWithSession(<TodayFocus data={data()} onViewEvidence={() => {}} />);
  const funds = screen.getByRole("region", {
    name: "按操作紧迫度排序的基金机会",
  });
  const rows = within(funds).getAllByTestId("today-fund-row");
  expect(rows.map((r) => r.getAttribute("data-fund-code"))).toEqual([
    "000105",
    "000104",
    "000103",
    "000102",
    "000101",
  ]);
  expect(within(funds).getAllByText("参考买入区间")).toHaveLength(4);
  expect(screen.queryByRole("region", { name: "本期提及基金" })).toBeNull();
  expect(screen.getByText("AI硬件", { selector: "strong" })).not.toBeVisible();
  fireEvent.click(screen.getByText(/研究方向与主题/));
  expect(screen.getByText("AI硬件", { selector: "strong" })).toBeVisible();
  expect(screen.getByText("存储主题", { selector: "strong" })).toBeVisible();
  const folded = rows.at(-1)!;
  expect(within(folded).queryByText("参考买入区间")).toBeNull();
  fireEvent.click(within(folded).getByText(/基金000101/));
  await act(async () => {});
  expect(within(folded).getByText("参考买入区间")).toBeVisible();
  vi.unstubAllGlobals();
});
